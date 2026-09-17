from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable

import pytest
from mcp import Client

from plex_mcp.config import Settings
from plex_mcp.server import build_server
from tests.fake_plex import FakePlexServer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_plex() -> Any:
    with FakePlexServer() as server:
        yield server


@pytest.fixture
def make_settings(fake_plex: FakePlexServer) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        return Settings(plex_url=fake_plex.url, plex_token="test-token", **overrides)

    return factory


class ToolCaller:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def __call__(self, name: str, **args: Any) -> dict[str, Any]:
        result = await self.client.call_tool(name, args)
        if result.is_error:
            raise ToolFailed(result.content[0].text)
        if result.structured_content is not None:
            return result.structured_content
        return json.loads(result.content[0].text)


class ToolFailed(Exception):
    pass


@pytest.fixture
async def call(make_settings: Callable[..., Settings]) -> AsyncIterator[ToolCaller]:
    async with Client(build_server(make_settings()), raise_exceptions=True) as client:
        yield ToolCaller(client)
