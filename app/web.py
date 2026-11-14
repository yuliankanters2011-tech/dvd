"""Small LAN dashboard. No authentication, no CORS, no arbitrary file serving."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .service import Service

LOG = logging.getLogger("dvd.web")
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "DVDBridge"
    sys_version = ""
    service: Service

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            alive = self.service.worker_alive()
            self._json(200 if alive else 503, {"ok": alive})
            return
        if path == "/api/status":
            self._json(200, self.service.snapshot())
            return
        static = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }
        if path not in static:
            self._json(404, {"error": "Niet gevonden"})
            return
        filename, content_type = static[path]
        try:
            self._send(200, (WEB_ROOT / filename).read_bytes(), content_type)
        except OSError:
            self._json(500, {"error": "Webbestand ontbreekt"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/command":
            self._json(404, {"error": "Niet gevonden"})
            return
        # A custom header forces a CORS preflight in browsers. We do not grant
        # CORS, so another website cannot silently eject/copy via a simple form.
        if self.headers.get("X-DVD-Bridge") != "1":
            self._json(403, {"error": "Ontbrekende beveiligingsheader"})
            return
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlparse(origin)
                if parsed.scheme not in {
                    "http",
                    "https",
                } or parsed.netloc != self.headers.get("Host"):
                    raise ValueError
            except ValueError:
                self._json(403, {"error": "Cross-origin opdracht geweigerd"})
                return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self._json(415, {"error": "Gebruik application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = MAX_BODY + 1
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": "Ongeldige opdrachtgrootte"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            command = payload["command"]
            if not isinstance(command, str):
                raise TypeError
            self.service.request(command)
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "Ongeldige opdracht"})
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})
        else:
            self._json(202, {"accepted": True, "command": command})


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, service: Service) -> None:
        handler = type("BoundHandler", (Handler,), {"service": service})
        super().__init__(("0.0.0.0", port), handler)


def serve_in_thread(server: DashboardServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="dvd-http", daemon=True)
    thread.start()
    return thread
