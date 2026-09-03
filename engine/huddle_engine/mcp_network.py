"""Optional network MCP server (streamable HTTP) for agents on other machines on the LAN.

Off by default. When enabled it listens on 127.0.0.1:<free port>; the desktop shell forwards
<mcp.port> on all interfaces to it (so macOS attributes the incoming-connection permission to
Huddle, not to the Python engine). HUDDLE_MCP_BIND_PUBLIC=1 binds 0.0.0.0 directly (headless
use without the shell). Requires
``Authorization: Bearer hud_…`` — a key generated in Settings → MCP. Runs in its own
uvicorn thread so the private engine API stays loopback-only.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time

from .db import Database
from .schemas import McpStatus
from .services import api_keys

log = logging.getLogger(__name__)


def lan_addresses() -> list[str]:
    out: list[str] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except socket.gaierror:
        pass
    if not out:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            out.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class NetworkMcpServer:
    def __init__(self, db: Database, build_server, cfg):
        self.db = db
        self._build = build_server
        self.cfg = cfg
        self._server = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.loopback_port: int | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server and self._server.started)

    def start(self, port: int) -> None:
        if self.running and self.port == port:
            return
        self.stop()
        import uvicorn
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        mcp = self._build(self.cfg)
        app = mcp.streamable_http_app(
            host="0.0.0.0", stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        db = self.db

        class Auth(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                header = request.headers.get("authorization", "")
                key = header[7:] if header.lower().startswith("bearer ") else None
                if not api_keys.verify(db, key):
                    return JSONResponse({"error": "unauthorized — use a Huddle API key"}, status_code=401)
                return await call_next(request)

        app.add_middleware(Auth)
        public = os.environ.get("HUDDLE_MCP_BIND_PUBLIC") == "1"
        host = "0.0.0.0" if public else "127.0.0.1"
        bind_port = port if public else _free_port()
        config = uvicorn.Config(app, host=host, port=bind_port, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self.port = port
        self.loopback_port = bind_port
        self.error = None

        def run():
            try:
                self._server.run()
            except Exception as e:  # port in use etc.
                self.error = str(e)
                log.error("network MCP server failed: %s", e)

        self._thread = threading.Thread(target=run, name="huddle-mcp-network", daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._server.started or self.error:
                break
            time.sleep(0.1)
        if not self._server.started and not self.error:
            self.error = f"Port {bind_port} could not be opened."
        log.info("network MCP server on %s:%d (%s)", host, bind_port, "ok" if self._server.started else self.error)

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._server, self._thread = None, None
        self.loopback_port = None

    def status(self, settings: dict) -> McpStatus:
        return McpStatus(stdio_enabled=bool(settings.get("mcp.enabled", True)),
                         network_enabled=bool(settings.get("mcp.networkEnabled", False)), running=self.running,
                         port=int(settings.get("mcp.port", 48800)), addresses=lan_addresses() if self.running else [],
                         key_count=len(api_keys.list_keys(self.db)), error=self.error,
                         loopback_port=self.loopback_port if self.running else None)
