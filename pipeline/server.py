"""
A small web server on this machine only (127.0.0.1), used by `head preview`, `head mark --page` and
`head serve`. It serves the skill's own pages and one project folder, and accepts two kinds of save:
the preview sheet the preview page draws, and the marks the marking page sets.
"""
from __future__ import annotations

import base64
import json
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

MAX_BODY = 40 * 1024 * 1024
THREE_CDN = "https://cdn.jsdelivr.net/npm/three@0.170.0"
PNG_PREFIX = "data:image/png;base64,"


def resolve_under(root: Path, relative: str) -> Path | None:
    """`relative` inside `root`, or None when it would escape it."""
    candidate = (root / relative.lstrip("/")).resolve()
    root = root.resolve()
    return candidate if candidate == root or root in candidate.parents else None


class Routes:
    """Where each URL prefix is served from, and what a save does."""

    def __init__(self, skill: Path, project: Path | None, three: Path | None,
                 on_save: Callable[[str, bytes], tuple[int, str]] | None = None) -> None:
        self.skill, self.project, self.three, self.on_save = skill, project, three, on_save

    def locate(self, url_path: str) -> tuple[str, Any]:
        """("file", Path), ("redirect", url) or ("missing", None) for a GET path."""
        path = unquote(urlsplit(url_path).path)
        if path.startswith("/three/"):
            if self.three is None:
                return "redirect", THREE_CDN + path[len("/three"):]
            found = resolve_under(self.three, path[len("/three/"):])
        elif path.startswith("/project/"):
            found = resolve_under(self.project, path[len("/project/"):]) if self.project else None
        else:
            found = resolve_under(self.skill, path)
            if found is not None and any(part.startswith(".") for part in found.relative_to(self.skill.resolve()).parts):
                found = None  # nothing hidden (.git and the like) is served
        if found is not None and found.is_dir():
            found = found / "index.html"
        return ("file", found) if found is not None and found.is_file() else ("missing", None)


def make_handler(routes: Routes) -> type[SimpleHTTPRequestHandler]:
    class Handler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".mjs": "text/javascript",
                          ".glb": "model/gltf-binary", ".json": "application/json", ".wav": "audio/wav"}

        def log_message(self, format: str, *args: Any) -> None:  # quiet: the command prints what matters
            return

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def translate_path(self, path: str) -> str:
            kind, found = routes.locate(path)
            return str(found) if kind == "file" else str(routes.skill / "__missing__")

        def do_GET(self) -> None:
            kind, found = routes.locate(self.path)
            if kind == "redirect":
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", found)
                self.end_headers()
                return
            if kind == "missing":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            super().do_GET()

        def do_POST(self) -> None:
            name = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if routes.on_save is None or not name.startswith("/save/") or not 0 < length <= MAX_BODY:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            status, message = routes.on_save(name[len("/save/"):], self.rfile.read(length))
            body = json.dumps({"ok": status == 200, "message": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def png_from_data_url(body: bytes) -> bytes:
    """The PNG bytes inside a data URL, checked to be a PNG."""
    text = body.decode("ascii", errors="replace")
    if not text.startswith(PNG_PREFIX):
        raise ValueError("not a PNG data URL")
    data = base64.b64decode(text[len(PNG_PREFIX):], validate=True)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return data


def start(routes: Routes, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Starts the server on a background thread. Returns it and its address."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(routes))
    server.daemon_threads = False  # so `stop` can wait for an answer that is still being sent
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def stop(server: ThreadingHTTPServer) -> None:
    """Stops the server after the requests it is answering have been answered."""
    server.shutdown()
    server.server_close()
