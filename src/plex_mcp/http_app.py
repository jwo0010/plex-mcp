"""HTTP transport: Streamable HTTP app wrapped with bearer-token auth and a health check."""

from __future__ import annotations

import hmac
import json
import logging
import socket
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from plex_mcp.config import Settings

log = logging.getLogger(__name__)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class BearerAuthMiddleware:
    """Pure ASGI middleware (safe for streaming responses) that requires `Authorization: Bearer <token>`."""

    def __init__(self, app: ASGIApp, token: str, open_paths: frozenset[str] = frozenset({"/health"})) -> None:
        self.app = app
        self._token = token.encode()
        self._open_paths = open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._open_paths or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers") or []).get(b"authorization", b"")
        scheme, _, supplied = header.partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(supplied.strip(), self._token):
            client = scope.get("client")
            log.warning("Rejected unauthenticated request from %s to %s", client[0] if client else "?", scope.get("path"))
            body = json.dumps({"error": "unauthorized", "error_description": "Missing or invalid bearer token"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b'Bearer realm="plex-mcp"'),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _detect_local_names() -> set[str]:
    names = {"127.0.0.1", "localhost", "[::1]"}
    try:
        hostname = socket.gethostname()
        names.update({hostname, hostname.lower(), socket.getfqdn().lower()})
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            names.add(f"[{addr}]" if ":" in addr else addr)
    except OSError:
        pass
    # The IP used for outbound traffic is usually the LAN address, even if hostname resolution is odd.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET address; no packets are sent for UDP connect
            names.add(probe.getsockname()[0])
    except OSError:
        pass
    return {n for n in names if n}


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """Host-header allowlist (DNS-rebinding protection)."""
    if "*" in settings.allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    names = set(settings.allowed_hosts) or _detect_local_names()
    names |= {"127.0.0.1", "localhost", "[::1]"}
    allowed_hosts = sorted({h for n in names for h in (n, f"{n}:*")})
    allowed_origins = sorted({f"{scheme}://{n}{port}" for n in names for scheme in ("http", "https") for port in ("", ":*")})
    log.info("Accepting Host headers: %s", ", ".join(sorted(names)))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def build_http_app(mcp: MCPServer, settings: Settings) -> Any:
    """Return the ASGI app to serve: MCP endpoint + /health, behind bearer auth when configured."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = mcp.streamable_http_app(
        streamable_http_path=settings.path,
        transport_security=transport_security(settings),
        host=settings.host,
    )
    if settings.auth_token is not None:
        return BearerAuthMiddleware(app, settings.auth_token.get_secret_value())
    return app


def check_http_safety(settings: Settings) -> None:
    """Refuse to expose an unauthenticated server beyond localhost."""
    if settings.auth_token is None and settings.host not in LOCAL_HOSTS:
        raise SystemExit(
            "Refusing to listen on {host} without PLEX_MCP_AUTH_TOKEN. Set a token "
            "(generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\") "
            "or bind to 127.0.0.1.".format(host=settings.host)
        )
    if settings.auth_token is not None and len(settings.auth_token.get_secret_value()) < 24:
        raise SystemExit("PLEX_MCP_AUTH_TOKEN is too short; use at least 24 characters.")
