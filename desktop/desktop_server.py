"""The Modernform Quote Helper server as the desktop launcher runs it: in the background, without a console.

Started by desktop_launcher with the data folder, port and a one-time token. The token is the
only way to stop it from outside (``POST /__desktop/shutdown``); web pages cannot read it, so a
website cannot shut Modernform Quote Helper down.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_common as common
import desktop_update as updates

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class UpdateService:
    """What the update banner can ask for: the update status, and to install the update found."""

    def __init__(self, install: Path, spawn: Callable[[Path], None] | None = None) -> None:
        self.install = install
        self.spawn = spawn or (lambda package: spawn_apply(install, package))

    def status(self) -> dict[str, Any]:
        return updates.update_status(self.install, common.load_config())

    def apply(self, requested_version: str | None) -> tuple[int, dict[str, Any]]:
        status = self.status()
        available = status.get("available")
        if status.get("applying"):
            return 409, {"detail": "An update is already being installed."}
        if not status.get("supported") or not available or not available.get("can_apply"):
            reason = (available or {}).get("reason") or status.get("reason") or "No update is ready to install."
            return 409, {"detail": reason}
        if requested_version and requested_version != available["version"]:
            return 409, {"detail": f"The update found is {available['version']}, not {requested_version}."}
        self.spawn(Path(available["file"]))
        return 202, {"status": "updating", "from_version": status["installed_version"],
                     "version": available["version"]}


def spawn_apply(install: Path, package: Path) -> None:
    """Start the launcher's update step on its own; it stops this server, swaps the files and starts the new one."""
    command = [sys.executable, str(install / "desktop" / "launcher.pyw"), "--apply-update", str(package), "--no-window"]
    environment = {key: value for key, value in os.environ.items() if key != common.TOKEN_ENV}
    environment[common.NO_DIALOGS_ENV] = "1"  # the window shows the progress and the result
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                    | subprocess.CREATE_NO_WINDOW)
    else:
        options["start_new_session"] = True
    log = common.home() / "logs" / "update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as output:
        output.write(f"\n---- {datetime.now(UTC).isoformat(timespec='seconds')} installing {package} ----\n")
        output.flush()
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, env=environment,
                         cwd=str(install), close_fds=True, **options)


def _json(status: int, payload: dict[str, Any]) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
    return status, headers, json.dumps(payload).encode("utf-8")


class DesktopControl:
    """ASGI wrapper that adds the desktop routes in front of the app.

    ``POST /__desktop/shutdown`` needs the launcher's token. The update routes are for this app's own
    window only: they answer requests addressed to 127.0.0.1:<port> or localhost:<port> (not a
    rebound DNS name), and installing also needs the ``X-Quote-Helper: 1`` header and a same-origin
    ``Origin``, which a page on another site cannot send without the browser asking first (and this
    server never says yes).
    """

    def __init__(self, app: Any, token: str, on_shutdown: Callable[[], None], *, port: int | None = None,
                 updates: UpdateService | None = None) -> None:
        self.app = app
        self.token = token
        self.on_shutdown = on_shutdown
        self.port = port
        self.updates = updates

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(common.DESKTOP_PREFIX):
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        path, method = scope["path"], scope["method"]
        if path == common.SHUTDOWN_PATH:
            supplied = headers.get("x-desktop-token", "")
            allowed = method == "POST" and bool(self.token) and secrets.compare_digest(supplied, self.token)
            await self._send(send, 202 if allowed else 403, [(b"content-type", b"text/plain")],
                             b"stopping" if allowed else b"forbidden")
            if allowed:
                self.on_shutdown()
            return
        if self.updates is not None and path == common.UPDATE_STATUS_PATH:
            if method != "GET":
                await self._send(send, *_json(405, {"detail": "Use GET."}))
            elif not self._local_host(headers, scope):
                await self._send(send, *_json(403, {"detail": "forbidden"}))
            else:
                await self._send(send, *await self._call(lambda: (200, self.updates.status())))
            return
        if self.updates is not None and path == common.APPLY_UPDATE_PATH:
            if method != "POST":
                await self._send(send, *_json(405, {"detail": "Use POST."}))
                return
            body = await self._read_body(receive)
            if body is None or not self._same_origin_request(headers, scope):
                await self._send(send, *_json(403, {"detail": "forbidden"}))
                return
            try:
                payload = json.loads(body.decode("utf-8") or "{}") if body.strip() else {}
            except (UnicodeDecodeError, ValueError):
                payload = None
            if not isinstance(payload, dict):
                await self._send(send, *_json(400, {"detail": "Send a JSON object."}))
                return
            version = payload.get("version")
            await self._send(send, *await self._call(
                lambda: self.updates.apply(version if isinstance(version, str) else None)))
            return
        await self._send(send, *_json(404, {"detail": "Not Found"}))

    def _allowed_hosts(self, scope: Scope) -> set[str]:
        port = self.port or (scope.get("server") or (None, None))[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _local_host(self, headers: dict[str, str], scope: Scope) -> bool:
        return headers.get("host", "").lower() in self._allowed_hosts(scope)

    def _same_origin_request(self, headers: dict[str, str], scope: Scope) -> bool:
        origin = headers.get("origin", "").lower()
        return (headers.get("x-quote-helper") == "1" and self._local_host(headers, scope)
                and origin in {f"http://{host}" for host in self._allowed_hosts(scope)})

    @staticmethod
    async def _read_body(receive: Receive, limit: int = 4096) -> bytes | None:
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return None
            body += message.get("body", b"")
            if len(body) > limit:
                return None
            if not message.get("more_body"):
                return body

    @staticmethod
    async def _call(work: Callable[[], tuple[int, dict[str, Any]]]) -> tuple[int, list, bytes]:
        try:
            status, payload = await asyncio.to_thread(work)
        except Exception as exc:  # the banner shows the reason instead of a bare 500
            logging.getLogger("quote_helper.desktop").exception("Update request failed")
            status, payload = 500, {"detail": f"Modernform Quote Helper could not check for updates: {exc}"}
        return _json(status, payload)

    @staticmethod
    async def _send(send: Send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def configure_environment(data_dir: Path, app_root: Path) -> None:
    """Point the backend at the data folder and this version's interface before it is imported."""
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["QUOTE_HELPER_V2_DATABASE"] = str(data_dir / common.DATABASE_NAME)
    os.environ["QUOTE_HELPER_FRONTEND_DIST"] = str(app_root / "frontend" / "dist")
    os.environ["QUOTE_HELPER_REVIEW_MODE"] = "0"
    backend = str(app_root / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    # Packing workers are separate Python processes; they must import this version's code.
    existing = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join([backend, *([existing] if existing else [])])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--app-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("quote_helper.desktop")
    token = os.environ.pop(common.TOKEN_ENV, "")
    if not token:
        log.error("No control token was given; start Modernform Quote Helper from its shortcut.")
        return 2

    app_root = args.app_dir or common.app_dir()
    configure_environment(args.data_dir, app_root)
    import uvicorn
    from app.main import app

    server: uvicorn.Server | None = None

    def stop() -> None:
        log.info("Shutdown requested")
        if server is not None:
            server.should_exit = True

    control = DesktopControl(app, token, stop, port=args.port, updates=UpdateService(common.install_dir()))
    config = uvicorn.Config(control, host="127.0.0.1", port=args.port, log_level="info", access_log=False,
                            log_config=None)
    server = uvicorn.Server(config)
    pid = os.getpid()
    common.write_state({
        "pid": pid, "port": args.port, "token": token, "version": common.installed_version(app_root),
        "app_dir": str(app_root), "data_dir": str(args.data_dir), "executable": sys.executable,
        "started_at": datetime.now(UTC).isoformat(),
    })
    log.info("Modernform Quote Helper %s starting on port %s with data in %s",
             common.installed_version(app_root), args.port, args.data_dir)
    try:
        server.run()
    finally:
        common.clear_state(pid)
        log.info("Modernform Quote Helper stopped")
    return 0 if server.started else 1


if __name__ == "__main__":
    sys.exit(main())
