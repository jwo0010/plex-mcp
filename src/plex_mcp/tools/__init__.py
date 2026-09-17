"""Tool registration."""

from plex_mcp.tools import collections, library, metadata, playlists
from plex_mcp.tools._common import ToolDeps


def register_all(deps: ToolDeps) -> None:
    library.register(deps)
    if deps.settings.read_only:
        return
    metadata.register(deps)
    collections.register(deps)
    playlists.register(deps)
