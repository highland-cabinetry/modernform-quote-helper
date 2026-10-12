"""Paths, configuration and run state shared by the desktop launcher and server.

Installed layout (one folder, replaced as a whole by each update):

    <install>/app/backend/app      backend code
    <install>/app/frontend/dist    built interface
    <install>/app/release.json     version
    <install>/bridge               Build2Quote Bridge (unpacked Chrome extension)
    <install>/desktop              this launcher
    <install>/runtime              bundled Python (only the full installer changes it)
    <install>/previous             the version an update package replaced (for --rollback-update)
    <install>/update-work          scratch space while an update package is applied

Everything that must survive an update lives outside it, under %LOCALAPPDATA%\\ModernformQuoteHelper:
config.json (where the data folder is), run/server.json (the running server), updates/ (where
update packages can be dropped) and logs/. The data folder itself (database, imported documents,
backups) is wherever config.json says.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_ID = "ModernformQuoteHelper"
DATABASE_NAME = "quote_helper_v2.sqlite3"
# Highland's Quote Helper uses 8000-8009; a different range lets both run on one computer.
DEFAULT_PORT = 8030
PORT_RANGE = range(8030, 8040)
TOKEN_ENV = "QUOTE_HELPER_DESKTOP_TOKEN"
INSTALL_DIR_ENV = "QUOTE_HELPER_INSTALL_DIR"
NO_DIALOGS_ENV = "QUOTE_HELPER_NO_DIALOGS"
DESKTOP_PREFIX = "/__desktop/"
SHUTDOWN_PATH = "/__desktop/shutdown"
UPDATE_STATUS_PATH = "/__desktop/update-status"
APPLY_UPDATE_PATH = "/__desktop/apply-update"
DESKTOP_DIR = Path(__file__).resolve().parent


class LauncherError(RuntimeError):
    """A problem to show the person, in plain words."""


def home() -> Path:
    override = os.environ.get("QUOTE_HELPER_HOME")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_ID


def install_dir() -> Path:
    """The program folder. The update step runs from a copy of the launcher and names it explicitly."""
    override = os.environ.get(INSTALL_DIR_ENV)
    return Path(override) if override else DESKTOP_DIR.parent


def app_dir() -> Path:
    """The folder holding backend/, frontend/dist and release.json.

    Installed: <install>/app. Running from a source checkout: the checkout itself.
    """
    override = os.environ.get("QUOTE_HELPER_APP_DIR")
    if override:
        return Path(override)
    installed = install_dir() / "app"
    return installed if (installed / "release.json").exists() else install_dir()


def installed_version(root: Path | None = None) -> str:
    release = (root or app_dir()) / "release.json"
    try:
        return str(json.loads(release.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError) as exc:
        message = f"Modernform Quote Helper's files are incomplete ({release} could not be read). Reinstall it."
        raise LauncherError(message) from exc


@dataclass(frozen=True)
class Config:
    data_dir: Path
    port: int = DEFAULT_PORT
    update_folder: Path | None = None  # optional shared folder where update packages are dropped

    @property
    def database(self) -> Path:
        return self.data_dir / DATABASE_NAME


def config_path() -> Path:
    return home() / "config.json"


def _read_raw_config(path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            message = f"The settings file {path} is damaged: {exc}. Fix or delete it, then try again."
            raise LauncherError(message) from exc
        if not isinstance(raw, dict):
            raise LauncherError(f"The settings file {path} is damaged. Fix or delete it, then try again.")
    return raw


def load_config() -> Config:
    path = config_path()
    raw = _read_raw_config(path)
    data_dir = Path(os.path.expandvars(str(raw.get("data_dir") or home() / "data")))
    port = raw.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise LauncherError(f"The port in {path} must be a number between 1024 and 65535.")
    update_folder = raw.get("update_folder")
    if update_folder is not None and not isinstance(update_folder, str):
        raise LauncherError(f"The update_folder in {path} must be a folder path in quotes.")
    return Config(data_dir=data_dir, port=port,
                  update_folder=Path(os.path.expandvars(update_folder)) if update_folder else None)


def save_config(config: Config) -> None:
    """Write the settings, keeping any keys this version does not know about."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_raw_config(path)
    payload.update({"data_dir": str(config.data_dir), "port": config.port})
    if config.update_folder is not None:
        payload["update_folder"] = str(config.update_folder)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


# --------------------------------------------------------------------------- run state


def state_path() -> Path:
    return home() / "run" / "server.json"


def log_path() -> Path:
    return home() / "logs" / "server.log"


def updates_dir() -> Path:
    """The default folder for update packages (ModernformQuoteHelperUpdate-<version>.zip)."""
    return home() / "updates"


def update_result_path() -> Path:
    return home() / "run" / "update-result.json"


def read_state() -> dict[str, Any] | None:
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and {"pid", "port", "token"} <= set(state) else None


def write_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def clear_state(pid: int | None = None) -> None:
    """Remove the run state; with ``pid``, only when it still describes that process."""
    if pid is not None:
        state = read_state()
        if state and state.get("pid") != pid:
            return
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- local HTTP

# Never route requests to 127.0.0.1 through a corporate proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(port: int, timeout: float = 2.0) -> dict[str, Any] | None:
    """The /api/health answer of a Modernform Quote Helper on ``port``, or None."""
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) and payload.get("status") == "ok" else None


def request_shutdown(port: int, token: str, timeout: float = 5.0) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{SHUTDOWN_PATH}", method="POST", data=b"", headers={"X-Desktop-Token": token}
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status == 202
    except (OSError, urllib.error.URLError):
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if sys.platform != "win32":
            # As uvicorn binds: a port whose last connections are in TIME_WAIT (just after a restart,
            # e.g. for an update) is free, so the app keeps its address and the open window finds it.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


# --------------------------------------------------------------------------- processes


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie child has exited but still has a process-table entry
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def process_image(pid: int) -> str | None:
    """Full path of the program a process runs, when the system tells us."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return None
        finally:
            kernel32.CloseHandle(handle)
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def is_our_server(pid: int, runtime: Path) -> bool:
    """True when ``pid`` is a Python from ``runtime`` (guards against a reused process ID)."""
    image = process_image(pid)
    if not image:
        return False
    try:
        return Path(image).resolve().parent == runtime.resolve()
    except OSError:
        return False


def terminate(pid: int) -> None:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        return
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
