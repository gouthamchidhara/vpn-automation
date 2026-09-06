"""Capture the SAML URL AnyConnect hands to the default browser.

AnyConnect opens the SSO page with ShellExecute, which launches the user's
default browser with its default profile — a window Playwright cannot attach
to (Chrome/Edge 136+ refuse --remote-debugging-port on the default profile
directory). Rather than interfere with that launch, we let it happen exactly
as AnyConnect expects and read the URL out of the launched process's command
line, then load that same URL in the browser we control.

The launcher process is short-lived: it hands the URL to the already-running
browser and exits within a few hundred milliseconds. Polling alone can miss
it, so the watcher starts BEFORE Connect is clicked and combines a WMI
process-creation subscription with a fast poll, streaming every browser
command line it sees back to us.
"""
from __future__ import annotations
import re
import subprocess
import sys
import threading
import time

from src.logging_config import get_logger

log = get_logger(__name__)

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BROWSER_PROCESSES = ("chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe", "brave.exe")

_URL_RE = re.compile(r'https?://[^\s"\']+')

# Pages a browser opens for itself — never the SSO URL, so they must not be
# mistaken for one when the configured SAML pattern doesn't match.
_JUNK_URL_RE = re.compile(
    r"(ntp\.msn\.com|/newtab|google\.com/_/chrome|msedge\.microsoft\.com|"
    r"welcome|first[-_]?run|blank)",
    re.IGNORECASE,
)

_CIM_FILTER = " or ".join(f"Name='{name}'" for name in BROWSER_PROCESSES)

_SNAPSHOT_SCRIPT = (
    "Get-CimInstance Win32_Process -Filter \"{filter}\" "
    "| Select-Object -ExpandProperty CommandLine"
)

# One PowerShell process does both jobs: it subscribes to process-creation
# events (which catch a launcher that exits before any poll could see it) and
# polls the browser processes as a fallback for locked-down WMI. Every command
# line it finds is written to stdout, flushed immediately so Python sees it
# while it still matters.
_WATCH_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$deadline = (Get-Date).AddSeconds({seconds})
$seen = @{{}}
function Emit($line) {{
    if ($line -and -not $seen.ContainsKey($line)) {{
        $seen[$line] = $true
        [Console]::Out.WriteLine($line)
        [Console]::Out.Flush()
    }}
}}
try {{
    Register-CimIndicationEvent -Query "SELECT * FROM __InstanceCreationEvent WITHIN 0.2 WHERE TargetInstance ISA 'Win32_Process'" -SourceIdentifier vpnwatch | Out-Null
}} catch {{ }}
while ((Get-Date) -lt $deadline) {{
    foreach ($evt in @(Get-Event -SourceIdentifier vpnwatch)) {{
        Emit $evt.SourceEventArgs.NewEvent.TargetInstance.CommandLine
        Remove-Event -EventIdentifier $evt.EventIdentifier
    }}
    foreach ($proc in @(Get-CimInstance Win32_Process -Filter "{filter}")) {{
        Emit $proc.CommandLine
    }}
    Start-Sleep -Milliseconds 300
}}
"""


def _urls_in(command_lines, ignore=()) -> list[str]:
    """Every URL on those command lines that wasn't already open."""
    found = []
    for line in command_lines:
        for url in _URL_RE.findall(line):
            url = url.rstrip('",\'')
            if url not in ignore and url not in found:
                found.append(url)
    return found


def extract_saml_url(command_lines, pattern: re.Pattern[str], ignore=()) -> str | None:
    """Return the first URL in any command line that matches the SAML pattern."""
    for url in _urls_in(command_lines, ignore):
        if pattern.search(url):
            return url
    return None


def extract_new_url(command_lines, ignore=()) -> str | None:
    """Return the first newly opened URL that isn't a browser's own start page.

    Used when the configured SAML pattern doesn't match: any URL handed to a
    browser between "Connect" and the timeout is, in practice, the SSO URL.
    """
    for url in _urls_in(command_lines, ignore):
        if not _JUNK_URL_RE.search(url):
            return url
    return None


def _run_powershell(script: str, timeout: int = 20) -> list[str]:
    if not IS_WINDOWS:
        return []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Process scan failed (non-fatal): %s", exc)
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _process_command_lines() -> list[str]:
    """Command lines of every running browser process (Windows only)."""
    return _run_powershell(_SNAPSHOT_SCRIPT.format(filter=_CIM_FILTER))


class SamlUrlWatcher:
    """Watches for the browser launch AnyConnect performs, and keeps its URL.

    Start it before clicking Connect: the URL of interest exists only for as
    long as the launcher process does.
    """

    def __init__(self, pattern: re.Pattern[str], watch_seconds: int = 120):
        self.pattern = pattern
        self.watch_seconds = watch_seconds
        self.baseline: set[str] = set()
        self._url: str | None = None
        self._fallback_url: str | None = None
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str | None:
        """The URL that matched the SAML pattern, once one has been seen."""
        with self._lock:
            return self._url

    @property
    def fallback_url(self) -> str | None:
        """Any newly opened URL, for when the SAML pattern matches nothing."""
        with self._lock:
            return self._url or self._fallback_url

    def snapshot_baseline(self) -> None:
        """Record URLs already on a browser command line, so stale ones from an
        earlier attempt are not mistaken for this run's SAML URL."""
        for line in _process_command_lines():
            self.baseline.update(_URL_RE.findall(line))
        if self.baseline:
            log.debug("Ignoring %d URL(s) already open before Connect.", len(self.baseline))

    def start(self) -> bool:
        """Begin watching. Returns False if the watcher could not be started."""
        if not IS_WINDOWS:
            return False
        script = _WATCH_SCRIPT.format(seconds=self.watch_seconds, filter=_CIM_FILTER)
        try:
            self._process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            log.warning("Could not start the SAML URL watcher: %s", exc)
            return False
        self._thread = threading.Thread(target=self._read_output, daemon=True)
        self._thread.start()
        log.info("Watching for the browser AnyConnect launches...")
        return True

    def _read_output(self) -> None:
        proc = self._process
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                self._record([line])
        except Exception as exc:
            log.debug("SAML URL watcher ended: %s", exc)

    def _record(self, command_lines) -> None:
        """Keep the best URL seen so far: a SAML match, else any new URL."""
        url = extract_saml_url(command_lines, self.pattern, self.baseline)
        with self._lock:
            if url and self._url is None:
                self._url = url
                log.info("Captured the SAML URL AnyConnect opened.")
                log.debug("SAML URL: %s", url)
                return
            if url or self._fallback_url is not None:
                return
        other = extract_new_url(command_lines, self.baseline)
        if other:
            with self._lock:
                if self._fallback_url is None:
                    self._fallback_url = other
                    log.debug("Noted a newly opened URL: %s", other)

    def poll_once(self) -> str | None:
        """Scan the process table directly — a fallback if the watcher died."""
        if self.url:
            return self.url
        self._record(_process_command_lines())
        return self.url

    def wait(self, timeout: int, poll: float = 1.0) -> str | None:
        """Block until the SAML URL turns up, or the timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.url:
                return self.url
            time.sleep(poll)
        return self.url

    def stop(self) -> None:
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._process = None


def capture_saml_url(pattern: re.Pattern[str], timeout: int = 30, poll: float = 1.0) -> str | None:
    """One-shot scan of browser command lines (no prior watcher needed)."""
    if not IS_WINDOWS:
        return None
    log.info("Scanning browser processes for the SAML URL (up to %ds)...", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = extract_saml_url(_process_command_lines(), pattern)
        if url:
            log.info("Recovered the SAML URL from a browser process.")
            log.debug("SAML URL: %s", url)
            return url
        time.sleep(poll)
    log.warning("No SAML URL found in any browser process within %ds.", timeout)
    return None
