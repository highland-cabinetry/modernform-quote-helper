"""Open Modernform Quote Helper: start its server in the background if needed, then show it in a Chrome app window.

    launcher.pyw              start (if needed) and open the window
    launcher.pyw --stop       stop the background server (used by the installer before an update)
    launcher.pyw --status     print the server state as JSON
    launcher.pyw --no-window  start the server without opening a window
    launcher.pyw --open-data  open the data folder in Explorer

Updates without an installer (see desktop_update.py):

    launcher.pyw --check-updates          look for an update package (prints JSON; from the Start menu it
                                          asks whether to install the one it found)
    launcher.pyw --apply-update [ZIP]     install an update package (without ZIP: choose the file)
    launcher.pyw --rollback-update        go back to the version the last update package replaced
    --reinstall                           with --apply-update: allow the same version again
    --no-window                           with the above: do not open the window afterwards
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_common as common
import desktop_update as updates
from desktop_common import LauncherError

START_TIMEOUT = 180.0  # the first start of a new version may back up and migrate a large database
STOP_TIMEOUT = 25.0
LOG_ROTATE_BYTES = 5 * 1024 * 1024
START_MARKER = "---- starting Modernform Quote Helper"


def runtime_dir() -> Path:
    return Path(sys.executable).resolve().parent


def server_python() -> str:
    """pythonw.exe beside the running interpreter (no console window), else the interpreter itself."""
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    return str(windowless if sys.platform == "win32" and windowless.exists() else executable)


def _rotate_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            older = path.with_name(path.name + ".1")
            older.unlink(missing_ok=True)
            path.replace(older)
    except FileNotFoundError:
        pass


def last_start_log() -> str:
    """The log written since the most recent start attempt."""
    try:
        text = common.log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text.rsplit(START_MARKER, 1)[-1]


EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*(?:Error|Exception)): (.+)$")


def _start_error(prefix: str) -> LauncherError:
    """Explain a failed start with the exception the server raised, when it raised one."""
    reason = ""
    for line in reversed(last_start_log().splitlines()):
        match = EXCEPTION_LINE.match(line.strip())
        if match:
            reason = match.group(2) if match.group(1) in {"DatabaseTooNewError", "LauncherError"} else line.strip()
            break
    message = prefix + (f"\n\n{reason}" if reason else "")
    return LauncherError(f"{message}\n\nDetails are in {common.log_path()}.")


# --------------------------------------------------------------------------- stop


def stop_server(state: dict | None = None, timeout: float = STOP_TIMEOUT) -> bool:
    """Stop the background server. Returns True when one was running."""
    state = state or common.read_state()
    if not state:
        return False
    pid, port = int(state["pid"]), int(state["port"])
    if not common.pid_alive(pid):
        common.clear_state(pid)
        return False
    started = time.monotonic()
    accepted = common.request_shutdown(port, str(state["token"]))
    deadline = started + timeout
    while time.monotonic() < deadline and common.pid_alive(pid):
        time.sleep(0.25)
    outcome = f"stopped after {time.monotonic() - started:.1f} s"
    if common.pid_alive(pid):
        executable = state.get("executable")
        runtime = Path(executable).parent if executable else runtime_dir()
        if common.is_our_server(pid, runtime):
            common.terminate(pid)
            end = time.monotonic() + 10
            while time.monotonic() < end and common.pid_alive(pid):
                time.sleep(0.25)
            outcome = "did not stop in time; ended it" + ("" if not common.pid_alive(pid) else " (still running)")
        else:
            outcome = "did not stop in time; left it running (process not recognised)"
    _log_line(f"stop: server {pid} on port {port}: shutdown {'accepted' if accepted else 'not answered'}, {outcome}")
    common.clear_state(pid)
    return True


def _log_line(message: str) -> None:
    try:
        path = common.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} launcher: {message}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- start


def ensure_server(config: common.Config) -> int:
    """Return the port of a running, current Modernform Quote Helper server, starting one if needed."""
    version = common.installed_version()
    app_root = str(common.app_dir())
    state = common.read_state()
    if state:
        pid = int(state["pid"])
        if common.pid_alive(pid):
            answer = common.health(int(state["port"]))
            current = (answer and answer.get("version") == version and state.get("app_dir") == app_root
                       and state.get("data_dir") == str(config.data_dir))
            if current:
                return int(state["port"])
            stop_server(state)  # an older version, another data folder, or not answering
        common.clear_state()

    port = _choose_port(config.port)
    token = secrets.token_urlsafe(32)
    log = common.log_path()
    _rotate_log(log)
    command = [server_python(), str(common.DESKTOP_DIR / "desktop_server.py"), "--port", str(port),
               "--data-dir", str(config.data_dir), "--app-dir", app_root]
    environment = dict(os.environ, **{common.TOKEN_ENV: token})
    options: dict = {}
    if sys.platform == "win32":
        options["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                    | subprocess.CREATE_NO_WINDOW)
    else:
        options["start_new_session"] = True
    common.home().mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as output:
        output.write(f"\n{START_MARKER} {version} on port {port} ----\n")
        output.flush()
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   env=environment, cwd=str(common.home()), close_fds=True, **options)

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise _start_error("Modernform Quote Helper could not start.")
        answer = common.health(port, timeout=1.0)
        if answer and answer.get("version") == version:
            return port
        time.sleep(0.4)
    raise _start_error("Modernform Quote Helper is taking too long to start.")


def _choose_port(preferred: int) -> int:
    candidates = [preferred, *[port for port in common.PORT_RANGE if port != preferred]]
    for port in candidates:
        if common.port_free(port):
            return port
        answer = common.health(port)
        if answer and port == preferred:
            raise LauncherError(
                f"Another copy of Modernform Quote Helper (version {answer.get('version', 'unknown')}) is "
                f"already running at "
                f"http://127.0.0.1:{port}. Stop it (Start menu > Modernform Quote Helper Tools > Stop Modernform "
                f"Quote Helper), "
                "then open Modernform Quote Helper again."
            )
    raise LauncherError(f"Ports {common.PORT_RANGE.start} to {common.PORT_RANGE.stop - 1} are all in use by "
                        f"other programs. Close one, then try again.")


# --------------------------------------------------------------------------- window


def _registry_path(executable: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValue(key, None)
            except OSError:
                continue
            if value and Path(value.strip('"')).exists():
                return value.strip('"')
    return None


def find_browser(name: str) -> str | None:
    executable = {"chrome": "chrome.exe", "edge": "msedge.exe"}[name]
    found = _registry_path(executable)
    if found:
        return found
    folders = {
        "chrome": [r"%ProgramFiles%\Google\Chrome\Application", r"%ProgramFiles(x86)%\Google\Chrome\Application",
                   r"%LOCALAPPDATA%\Google\Chrome\Application"],
        "edge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application", r"%ProgramFiles%\Microsoft\Edge\Application"],
    }[name]
    for folder in folders:
        candidate = Path(os.path.expandvars(folder)) / executable
        if candidate.exists():
            return str(candidate)
    return None


def open_window(url: str) -> str:
    """Open ``url`` as an app window. Returns which browser was used."""
    chrome = find_browser("chrome")
    if chrome:
        # The person's normal Chrome profile: the Build2Quote Bridge and saved drafts are there.
        subprocess.Popen([chrome, f"--app={url}"], close_fds=True)
        return "chrome"
    edge = find_browser("edge")
    if edge:
        subprocess.Popen([edge, f"--app={url}"], close_fds=True)
        notify("Google Chrome was not found, so Modernform Quote Helper opened in Microsoft Edge. "
               "Broker autofill (Build2Quote Bridge) needs Chrome.")
        return "edge"
    import webbrowser

    webbrowser.open(url)
    return "default"


# --------------------------------------------------------------------------- messages


def dialogs_allowed() -> bool:
    """Message boxes only when started from a shortcut (pythonw), never inside the update step."""
    return (sys.platform == "win32" and os.environ.get(common.NO_DIALOGS_ENV) != "1"
            and (sys.stderr is None or Path(sys.executable).name.lower() == "pythonw.exe"))


def notify(message: str, error: bool = False) -> None:
    if dialogs_allowed():
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, message, "Modernform Quote Helper", flags | 0x10000)  # MB_SETFOREGROUND
        return
    if os.environ.get(common.NO_DIALOGS_ENV) == "1":
        _log_line(message.replace("\n", " "))
    stream = sys.stderr or sys.stdout
    if stream:
        print(message, file=stream)


def ask(message: str) -> bool:
    """A Yes/No question in a message box; False when no dialog can be shown."""
    if not dialogs_allowed():
        return False
    import ctypes

    # MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND; IDYES == 6
    return ctypes.windll.user32.MessageBoxW(None, message, "Modernform Quote Helper", 0x4 | 0x20 | 0x10000) == 6


def open_file_dialog_structure():
    """OPENFILENAMEW (commdlg.h). A function so a Windows test can check its size without a dialog."""
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR), ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD), ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR), ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR), ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD), ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM), ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD),
        ]

    return OPENFILENAMEW


def choose_update_file(folder: Path | None) -> str | None:
    """Windows' own Open dialog for an update package (no other program is started)."""
    if not dialogs_allowed():
        return None
    import ctypes
    from ctypes import wintypes

    structure = open_file_dialog_structure()
    file_buffer = ctypes.create_unicode_buffer(4096)
    filter_buffer = ctypes.create_unicode_buffer(
        "Modernform Quote Helper update "
        "(ModernformQuoteHelperUpdate-*.zip)\0ModernformQuoteHelperUpdate-*.zip\0All files (*.*)\0*.*\0\0")
    dialog = structure()
    dialog.lStructSize = ctypes.sizeof(structure)
    dialog.lpstrFilter = ctypes.cast(filter_buffer, wintypes.LPCWSTR)
    dialog.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(file_buffer)
    dialog.lpstrInitialDir = str(folder) if folder and folder.is_dir() else None
    dialog.lpstrTitle = "Install a Modernform Quote Helper update"
    dialog.Flags = 0x1000 | 0x800 | 0x80000 | 0x8  # FILEMUSTEXIST | PATHMUSTEXIST | EXPLORER | NOCHANGEDIR
    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(dialog)):
        return file_buffer.value
    return None


def status() -> dict:
    state = common.read_state() or {}
    running = bool(state) and common.pid_alive(int(state.get("pid", 0)))
    answer = common.health(int(state["port"])) if running else None
    config = common.load_config()
    return {
        "running": running and answer is not None,
        "port": state.get("port"),
        "pid": state.get("pid"),
        "version": answer.get("version") if answer else None,
        "installed_version": common.installed_version(),
        "data_dir": str(config.data_dir),
        "config": str(common.config_path()),
        "log": str(common.log_path()),
        "previous_version": updates.previous_version(common.install_dir()),
        "last_update": updates.read_last_result(),
    }


# --------------------------------------------------------------------------- updates


def _stop_for_update() -> None:
    state = common.read_state()
    if not state:
        return
    pid = int(state["pid"])
    stop_server(state)
    if common.pid_alive(pid):
        raise updates.UpdateError("Modernform Quote Helper's background server could not be stopped, so nothing "
                                  "was changed. "
                                  "Restart the computer, then try again.")


def _start_installed(open_window_after: bool) -> bool:
    """Start the version now in the program folder with its own launcher; True when it answered."""
    install = common.install_dir()
    command = [sys.executable, str(install / "desktop" / "launcher.pyw")]
    if not open_window_after:
        command.append("--no-window")
    environment = {key: value for key, value in os.environ.items() if key != common.INSTALL_DIR_ENV}
    environment[common.NO_DIALOGS_ENV] = "1"
    try:
        return subprocess.run(command, cwd=str(install), env=environment, stdin=subprocess.DEVNULL,
                              timeout=START_TIMEOUT + 60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_from_copy(arguments: list[str]) -> int:
    """Run an update action from a copy of the launcher, because the originals in desktop/ are replaced."""
    install = common.install_dir()
    copy = updates.prepare_launcher_copy(install)
    environment = dict(os.environ, **{common.INSTALL_DIR_ENV: str(install)})
    try:
        return subprocess.run([sys.executable, str(copy / "desktop_launcher.py"), *arguments, "--from-copy"],
                              cwd=str(install), env=environment).returncode
    finally:
        updates.remove_tree_quietly(copy)


def _report(result: dict) -> int:
    if sys.stdout:
        print(json.dumps(result, indent=2))
    if dialogs_allowed():
        notify(result.get("message") or "", error=not result.get("ok"))
    return 0 if result.get("ok") else 1


def update_command(args: argparse.Namespace) -> int:
    install = common.install_dir()
    if not updates.is_installed_layout(install):
        raise LauncherError("Update packages apply only to the installed Modernform Quote Helper, not to a "
                            "source checkout.")
    if args.check_updates:
        report = updates.update_status(install, common.load_config())
        available = report.get("available")
        if sys.stdout:
            print(json.dumps(report, indent=2))
        if not dialogs_allowed():
            return 0
        if available and available.get("can_apply"):
            question = (f"Modernform Quote Helper {available['version']} is ready to install (you have "
                        f"{report['installed_version']}).\n\nInstall it now? Modernform Quote Helper closes for "
                        f"a moment and "
                        "opens again. Your data and settings are kept.")
            if ask(question):
                return _run_from_copy(["--apply-update", available["file"]])
        elif available:
            notify(f"An update was found but cannot be installed:\n\n{available.get('reason')}")
        else:
            folders = "\n".join(report.get("folders") or [])
            notify(f"No newer Modernform Quote Helper than {report['installed_version']} was found in:\n\n{folders}")
        return 0
    if not args.from_copy:
        if args.rollback_update:
            previous = updates.previous_version(install)
            question = (f"Go back to Modernform Quote Helper {previous}? Modernform Quote Helper closes for a "
                        "moment and opens again.")
            if dialogs_allowed() and previous and not ask(question):
                return 0
            return _run_from_copy(["--rollback-update", *(["--no-window"] if args.no_window else [])])
        package = args.apply_update
        if not package:
            config = common.load_config()
            package = choose_update_file(config.update_folder or common.updates_dir())
            if not package:
                if dialogs_allowed():
                    return 0  # the person cancelled the dialog
                raise LauncherError("Name the update package: --apply-update ModernformQuoteHelperUpdate-<version>.zip")
        forwarded = ["--apply-update", str(Path(package).resolve())]
        forwarded += (["--reinstall"] if args.reinstall else []) + (["--no-window"] if args.no_window else [])
        return _run_from_copy(forwarded)

    # Running from the copy: do the work.
    def start() -> bool:
        return _start_installed(open_window_after=not args.no_window)

    if args.rollback_update:
        return _report(updates.rollback(install, stop=_stop_for_update, start=start, log=_log_line))
    return _report(updates.apply_package(install, Path(args.apply_update), reinstall=args.reinstall,
                                         stop=_stop_for_update, start=start, log=_log_line))


def prepare_start() -> None:
    """Before a normal start: wait for an update being applied; finish or undo an interrupted one."""
    install = common.install_dir()
    if not (install / updates.WORK_DIR).exists():
        return
    if not updates.wait_for_apply(install):
        raise LauncherError("A Modernform Quote Helper update is still being installed. Try again in a minute.")
    note = updates.recover(install)
    if note:
        _log_line(note)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--stop", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--open-data", action="store_true")
    action.add_argument("--check-updates", action="store_true")
    action.add_argument("--apply-update", nargs="?", const="", metavar="ZIP")
    action.add_argument("--rollback-update", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--reinstall", action="store_true")
    parser.add_argument("--from-copy", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.stop:
            stopped = stop_server()
            if sys.stdout:
                print("stopped" if stopped else "not running")
            return 0
        if args.status:
            if sys.stdout:
                print(json.dumps(status(), indent=2))
            return 0
        if args.check_updates or args.apply_update is not None or args.rollback_update:
            return update_command(args)
        config = common.load_config()
        if args.open_data:
            config.data_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(config.data_dir)  # type: ignore[attr-defined]  # Windows only
            return 0
        prepare_start()
        port = ensure_server(config)
        if not args.no_window:
            open_window(f"http://127.0.0.1:{port}/")
        return 0
    except LauncherError as exc:
        notify(str(exc), error=True)
        return 1
    except Exception as exc:  # last resort: never fail silently from a shortcut
        notify(f"Modernform Quote Helper could not start: {exc}\n\nDetails are in {common.log_path()}.", error=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
