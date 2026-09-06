"""Cisco AnyConnect vpncli.exe subprocess control."""
from __future__ import annotations
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from src.logging_config import get_logger

log = get_logger(__name__)

# vpncli prompts (banner/certificate accept) that must be auto-answered "y"
# so the interactive process doesn't stall before it can launch the SAML browser.
_PROMPT_MARKERS = ("y/n", "accept?", "please respond", "trust this certificate")


class VpnState(Enum):
    CONNECTED = "Connected"
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    UNKNOWN = "Unknown"


@dataclass
class VpnCli:
    vpncli_path: str
    _process: subprocess.Popen | None = field(default=None, init=False, repr=False)

    def _run(self, *args: str, timeout: int = 10) -> str:
        """Run vpncli.exe with args, return stdout. Used for short one-off commands (state/disconnect)."""
        cmd = [self.vpncli_path, *args]
        log.debug("vpncli cmd: %s", cmd)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            log.warning("vpncli timed out after %ds", timeout)
            return ""
        except FileNotFoundError:
            log.error("vpncli.exe not found at %s", self.vpncli_path)
            raise

    def connect(self, host: str) -> bool:
        """Start VPN connection as a background process (non-blocking).

        vpncli connect stays interactive (banner/cert prompts) until the SAML
        browser flow completes, so it must not be run synchronously with a
        short timeout — that would kill it before the user can authenticate.

        NOTE: vpncli rejects SAML-only groups ("authentication type not
        supported in AnyConnect CLI") — use src.gui.connect_via_gui for those.
        """
        cmd = [self.vpncli_path, "connect", host]
        log.info("Starting vpncli connect: %s", cmd)
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            log.error("vpncli.exe not found at %s", self.vpncli_path)
            raise
        threading.Thread(target=self._pump_output, daemon=True).start()
        return True

    @staticmethod
    def _close_gui() -> None:
        """Kill any running AnyConnect GUI so vpncli can take control of the session."""
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "vpnui.exe"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if "SUCCESS" in result.stdout.upper():
            log.info("Closed running AnyConnect GUI (vpnui.exe).")
            time.sleep(1)


    def _pump_output(self) -> None:
        """Read vpncli connect output, auto-answering banner/certificate prompts."""
        proc = self._process
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                log.info("vpncli: %s", line.rstrip())
                if any(marker in line.lower() for marker in _PROMPT_MARKERS):
                    try:
                        proc.stdin.write("y\n")
                        proc.stdin.flush()
                        log.info("Auto-answered vpncli prompt with 'y'.")
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("vpncli output pump ended: %s", exc)

    def is_connect_process_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def terminate_connect_process(self) -> None:
        """Best-effort cleanup of the background connect process."""
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def disconnect(self) -> str:
        output = self._run("disconnect", timeout=10)
        log.info("vpncli disconnect:\n%s", output)
        return output

    def state(self) -> VpnState:
        """Parse vpncli.exe state output."""
        output = self._run("state", timeout=10)
        return parse_state(output)


def parse_state(output: str) -> VpnState:
    """Parse vpncli state output into VpnState enum."""
    lower = output.lower()
    if "state: connected" in lower or ">> state: connected" in lower:
        return VpnState.CONNECTED
    if "state: disconnected" in lower or ">> state: disconnected" in lower:
        return VpnState.DISCONNECTED
    if "state: connecting" in lower or ">> state: connecting" in lower:
        return VpnState.CONNECTING
    # Heuristic fallback
    if "connected" in lower and "disconnected" not in lower:
        return VpnState.CONNECTED
    if "disconnected" in lower:
        return VpnState.DISCONNECTED
    return VpnState.UNKNOWN


def wait_for_connected(cli: VpnCli, timeout: int = 60) -> VpnState:
    """Poll vpncli state until Connected or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = cli.state()
        log.info("VPN state: %s", st.value)
        if st == VpnState.CONNECTED:
            return st
        if st == VpnState.DISCONNECTED:
            # Could mean auth failed
            log.error("VPN reports Disconnected during wait — auth may have failed.")
            return st
        time.sleep(3)
    log.error("Timed out waiting for Connected after %ds.", timeout)
    return VpnState.UNKNOWN
