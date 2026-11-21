"""
A small web server on this machine only (127.0.0.1), used by `head preview`, `head mark --page`,
`head serve` and `head test --browser`. It serves the skill's own pages, one project folder and a local
three.js, and accepts saves from its own pages: the preview sheet, the marks, browser test results.

It is careful about who it answers:
- only requests addressed to 127.0.0.1, localhost or [::1] on its own port (so a web page elsewhere
  cannot reach it through a DNS name that points here);
- saves only as JSON with this run's token in the X-Head-Token header (so another page cannot post
  to it, and a form cannot either);
- never a file or folder whose name starts with a dot, on any route.
"""
from __future__ import annotations

import base64
import json
import secrets
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

MAX_BODY = 40 * 1024 * 1024
PNG_PREFIX = "data:image/png;base64,"
TOKEN_HEADER = "X-Head-Token"
LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]")

Saver = Callable[[str, Any], tuple[int, str]]


def resolve_under(root: Path, relative: str) -> Path | None:
    """`relative` inside `root`, or None when it would escape it or pass through a hidden (dot) name."""
    root = root.resolve()
    candidate = (root / relative.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if any(part.startswith(".") for part in candidate.relative_to(root).parts):
        return None
    return candidate


def host_allowed(host: str | None, port: int) -> bool:
    """Whether a Host header names this machine on this server's port."""
    return host is not None and host.strip().lower() in {f"{name}:{port}" for name in LOCAL_NAMES}


class Routes:
    """Where each URL prefix is served from, what a save does, and this run's token."""

    def __init__(self, skill: Path, project: Path | None, three: Path | None,
                 on_save: Saver | None = None, token: str | None = None) -> None:
        self.skill, self.project, self.three, self.on_save = skill, project, three, on_save
        self.token = token or secrets.token_urlsafe(18)
        self.port = 0

    def locate(self, url_path: str) -> tuple[str, Any]:
        """("file", Path) or ("missing", None) for a GET path."""
        path = unquote(urlsplit(url_path).path)
        if path.startswith("/three/"):
            found = resolve_under(self.three, path[len("/three/"):]) if self.three else None
        elif path.startswith("/project/"):
            found = resolve_under(self.project, path[len("/project/"):]) if self.project else None
        else:
            found = resolve_under(self.skill, path)
        if found is not None and found.is_dir():
            found = found / "index.html"
        return ("file", found) if found is not None and found.is_file() else ("missing", None)

    def check_save(self, path: str, host: str | None, content_type: str | None, token: str | None,
                   length: int) -> tuple[int, str] | None:
        """Why a save must be refused, as (status, reason), or None when it may go ahead."""
        if not host_allowed(host, self.port):
            return HTTPStatus.FORBIDDEN, "wrong host"
        if self.on_save is None or not urlsplit(path).path.startswith("/save/"):
            return HTTPStatus.NOT_FOUND, "nothing to save here"
        if (content_type or "").split(";")[0].strip().lower() != "application/json":
            return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "saves must be application/json"
        if not token or not secrets.compare_digest(token, self.token):
            return HTTPStatus.FORBIDDEN, "missing or wrong token"
        if not 0 < length <= MAX_BODY:
            return HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > MAX_BODY else HTTPStatus.BAD_REQUEST, "bad length"
        return None


def make_handler(routes: Routes) -> type[SimpleHTTPRequestHandler]:
    class Handler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".mjs": "text/javascript",
                          ".glb": "model/gltf-binary", ".json": "application/json", ".wav": "audio/wav", ".mp3": "audio/mpeg"}

        def log_message(self, format: str, *args: Any) -> None:  # quiet: the command prints what matters
            return

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def translate_path(self, path: str) -> str:
            kind, found = routes.locate(path)
            return str(found) if kind == "file" else str(routes.skill / "__missing__")

        def answer(self, status: int, message: str) -> None:
            body = json.dumps({"ok": status == 200, "message": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not host_allowed(self.headers.get("Host"), routes.port):
                self.send_error(HTTPStatus.FORBIDDEN, "wrong host")
                return
            if routes.locate(self.path)[0] == "missing":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            super().do_GET()

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            refused = routes.check_save(self.path, self.headers.get("Host"), self.headers.get("Content-Type"),
                                        self.headers.get(TOKEN_HEADER), length)
            if refused is not None:
                self.answer(*refused)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.answer(HTTPStatus.BAD_REQUEST, "the body is not JSON")
                return
            self.answer(*routes.on_save(urlsplit(self.path).path[len("/save/"):], payload))

    return Handler


def png_from_data_url(text: str) -> bytes:
    """The PNG bytes inside a data URL, checked to be a PNG."""
    if not isinstance(text, str) or not text.startswith(PNG_PREFIX):
        raise ValueError("not a PNG data URL")
    data = base64.b64decode(text[len(PNG_PREFIX):], validate=True)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return data


def start(routes: Routes, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Starts the server on a background thread. Returns it and its address."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(routes))
    server.daemon_threads = False  # so `stop` can wait for an answer that is still being sent
    routes.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{routes.port}"


def stop(server: ThreadingHTTPServer) -> None:
    """Stops the server after the requests it is answering have been answered."""
    server.shutdown()
    server.server_close()
