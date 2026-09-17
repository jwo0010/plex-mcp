"""Builds the MCPServer instance."""

from __future__ import annotations

from plexapi.server import PlexServer

from mcp.server import MCPServer
from plex_mcp import __version__
from plex_mcp.config import Settings
from plex_mcp.plex import PlexContext
from plex_mcp.tools import register_all
from plex_mcp.tools._common import ToolDeps

INSTRUCTIONS = """\
Tools for a Plex Media Server: browse and search libraries, edit item metadata and tags,
and manage collections and playlists.

How to work with it:
- Every item, collection, and playlist is identified by a numeric rating_key. Find them with
  search_library, search_all, list_collections, list_playlists, or list_children.
- Call server_info first to see the libraries and whether write/delete tools are enabled.
- Before building filters for search_library or a smart collection/playlist, call
  list_filter_options (with field='genre' etc.) to learn valid field names and values.
- Edits lock fields by default so Plex agents don't overwrite them; pass lock=false to avoid that.
- Adding a 'collection' tag via edit_tags is the quickest way to put many items in a collection.
- Confirm with the user before bulk changes or deletes.
"""


def build_server(settings: Settings, plex: PlexServer | None = None) -> MCPServer:
    """Create the MCP server and register tools according to the safety settings."""
    mcp = MCPServer(
        "plex",
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level=settings.log_level,
    )
    ctx = PlexContext(settings, server=plex)
    register_all(ToolDeps(mcp=mcp, ctx=ctx, settings=settings))
    return mcp
