"""Launch browser with CDP debug port, attach via Playwright."""
from __future__ import annotations
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from src.logging_config import get_logger

log = get_logger(__name__)


class DebugBrowser:
    """Manages a CDP-debuggable browser instance using a TEMP profile.

    Real browser profiles (Chrome/Edge with 40+ tabs, extensions, startup
    boost) consistently fail to bind the CDP debug port. A clean temp profile
    starts in <2 seconds with no interference.
    """

    def __init__(
        self,
        browser_exe: str,
        cdp_port: int = 9222,
        user_data_dir: str = "",
    ):
        self.browser_exe = browser_exe
        self.cdp_port = cdp_port
        # Always use a temp profile — real profiles have startup boost,
        # extensions, and session restore that block CDP binding.
        self.user_data_dir = user_data_dir or tempfile.mkdtemp(prefix="vpn-auto-login-")
        self._process: subprocess.Popen | None = None
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"

    def launch(self) -> None:
        """Start browser with remote debugging enabled on a temp profile."""
        exe_name = Path(self.browser_exe).name
        # Kill any lingering instances that might hold the port
        subprocess.run(
            ["taskkill", "/F", "/IM", exe_name],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(1)

        cmd = [
            self.browser_exe,
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--no-default-browser-check",
            "about:blank",
        ]
        log.info("Launching browser (temp profile): %s", self.browser_exe)
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        if not self._wait_for_port(self.cdp_port, timeout=15):
            log.error("CDP port %d did not become available within 15s.", self.cdp_port)
            raise RuntimeError(f"CDP port {self.cdp_port} not ready")
        log.info("Browser ready (PID %d, CDP port %d)", self._process.pid, self.cdp_port)

    @staticmethod
    def _wait_for_port(port: int, timeout: int = 30) -> bool:
        """Block until the given TCP port is accepting connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    return True
            except OSError:
                time.sleep(1)
        return False

    def connect(self) -> BrowserContext:
        """Connect Playwright to the running browser via CDP."""
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = self._browser.new_context()
        log.info("Playwright connected via CDP. Pages: %d", len(self._context.pages))
        return self._context

    def find_page_by_url(self, url_pattern: str, timeout: int = 30) -> Page | None:
        """Poll pages until one matches the URL pattern."""
        import re
        pattern = re.compile(url_pattern, re.IGNORECASE)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for page in self._context.pages:
                if pattern.search(page.url):
                    log.info("Found matching page: %s", page.url)
                    return page
            time.sleep(1)
        log.warning("No page matching '%s' found within %ds.", url_pattern, timeout)
        return None

    def close(self) -> None:
        """Clean up: close Playwright, kill browser, remove temp dir."""
        log.info("Shutting down browser...")
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        # Kill any lingering process on the debug port
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        # Also kill any chrome/edge still on our debug port
        self._kill_debug_port_processes()
        log.info("Browser shut down.")

    def _kill_debug_port_processes(self) -> None:
        """Kill any browser processes using our CDP port (safety net)."""
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                if f":{self.cdp_port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit():
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True, timeout=5,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                        log.info("Killed lingering browser PID %s on port %d", pid, self.cdp_port)
        except Exception as exc:
            log.debug("Port cleanup failed (non-fatal): %s", exc)
