"""HTTP (bearer auth, host allowlist, legacy clients) and stdio transport tests."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import httpx2
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from plex_mcp.config import Settings
from plex_mcp.http_app import build_http_app, check_http_safety
from plex_mcp.server import build_server

TOKEN = "a-very-long-test-token-1234567890"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(make_settings) -> Iterator[str]:
    port = _free_port()
    settings = make_settings(host="127.0.0.1", port=port, auth_token=TOKEN, allowed_hosts=["127.0.0.1", "plex-box"])
    app = build_http_app(build_server(settings), settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.anyio
async def test_http_with_token_works(http_server: str) -> None:
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as http:
        async with Client(streamable_http_client(f"{http_server}/mcp", http_client=http)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert "edit_metadata" in names
            result = await client.call_tool("get_item", {"rating_key": 101})
            assert result.structured_content["title"] == "Alien"


def test_http_rejects_missing_or_wrong_token(http_server: str) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with httpx2.Client(timeout=10) as http:
        assert http.post(f"{http_server}/mcp", json=body).status_code == 401
        wrong = http.post(f"{http_server}/mcp", json=body, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert http.get(f"{http_server}/health").json() == {"status": "ok"}


def test_http_rejects_unknown_host_header(http_server: str) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}", "Host": "evil.example.com",
               "Accept": "application/json, text/event-stream"}
    with httpx2.Client(timeout=10) as http:
        response = http.post(f"{http_server}/mcp", headers=headers,
                             json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 421


def test_http_legacy_protocol_client(http_server: str) -> None:
    """Clients still on the 2025 protocol (initialize handshake + session id) must keep working."""
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream",
               "Host": "plex-box:8765"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "legacy", "version": "1"}}}
    with httpx2.Client(timeout=10) as http:
        response = http.post(f"{http_server}/mcp", headers=headers, json=init)
        assert response.status_code == 200, response.text
        payload = _parse(response)
        assert payload["result"]["serverInfo"]["name"] == "plex"
        session = response.headers.get("mcp-session-id")
        follow = dict(headers, **({"Mcp-Session-Id": session} if session else {}),
                      **{"MCP-Protocol-Version": payload["result"]["protocolVersion"]})
        http.post(f"{http_server}/mcp", headers=follow, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = http.post(f"{http_server}/mcp", headers=follow, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert "search_library" in {t["name"] for t in _parse(listed)["result"]["tools"]}


def _parse(response: Any) -> dict[str, Any]:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


def test_refuses_lan_bind_without_token(make_settings) -> None:
    with pytest.raises(SystemExit, match="without PLEX_MCP_AUTH_TOKEN"):
        check_http_safety(make_settings(host="0.0.0.0"))
    with pytest.raises(SystemExit, match="too short"):
        check_http_safety(make_settings(host="0.0.0.0", auth_token="short"))
    check_http_safety(make_settings(host="127.0.0.1"))


def test_settings_parse_csv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLEX_TOKEN", "abc")
    monkeypatch.setenv("PLEX_MCP_ALLOWED_LIBRARIES", "Movies, TV Shows")
    monkeypatch.setenv("PLEX_MCP_ALLOW_DELETE", "false")
    monkeypatch.setenv("PLEX_MCP_LOG_LEVEL", "debug")
    monkeypatch.setenv("PATH", "/usr/bin")  # must not leak into the `path` field
    monkeypatch.setenv("PORT", "1")
    settings = Settings(_env_file=None)
    assert settings.path == "/mcp"
    assert settings.port == 8765
    assert settings.allowed_libraries == ["Movies", "TV Shows"]
    assert settings.allow_delete is False
    assert settings.log_level == "DEBUG"


@pytest.mark.anyio
async def test_stdio_transport(fake_plex) -> None:
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PLEX_URL=fake_plex.url, PLEX_TOKEN="test-token",
               PYTHONPATH=os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH")])))
    params = StdioServerParameters(command=sys.executable, args=["-m", "plex_mcp", "--transport", "stdio"], env=env)
    async with Client(stdio_client(params)) as client:
        result = await client.call_tool("search_library", {"library": "Movies", "query": "heat"})
        assert [r["title"] for r in result.structured_content["results"]] == ["Heat"]


def test_page_info_without_plex_total() -> None:
    from plex_mcp.tools._common import page_info

    assert page_info(returned=50, offset=0, limit=50, total=None)["has_more"] is True
    tail = page_info(returned=12, offset=50, limit=50, total=None)
    assert (tail["has_more"], tail["next_offset"]) == (False, None)
    assert page_info(returned=0, offset=0, limit=50, total=0)["has_more"] is False
