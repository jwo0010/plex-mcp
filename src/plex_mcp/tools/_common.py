"""Shared helpers for tool modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from plex_mcp.config import Settings
from plex_mcp.plex import PlexContext

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE_NON_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

RatingKey = Annotated[int, Field(description="Plex rating_key (numeric ID) of the item.", ge=1)]
RatingKeys = Annotated[list[int], Field(description="List of Plex rating_keys.", min_length=1, max_length=500)]
Library = Annotated[str, Field(description="Library title (e.g. 'Movies') or numeric library ID.")]
LibType = Annotated[
    str | None,
    Field(
        description=(
            "Restrict to one media type: movie, show, season, episode, artist, album, track, "
            "photoalbum, photo, collection. Defaults to the library's primary type."
        )
    ),
]
Filters = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Plex advanced filters. Keys are filter fields with an optional operator suffix; values are "
            "strings, numbers, booleans, or lists (OR). Examples: {\"genre\": \"Horror\"}, "
            "{\"year>>\": 1999, \"year<<\": 2010}, {\"unwatched\": true}, {\"addedAt>>\": \"30d\"}, "
            "{\"show.collection\": \"Marvel\"}, {\"genre&\": [\"Comedy\", \"Romance\"]} (AND). "
            "Operators: tags/int '!' is-not; int '>>' greater, '<<' less; string (no suffix) contains, "
            "'=' is, '!=' is-not, '<' begins-with, '>' ends-with; bool '!' false; date '>>' after, '<<' before "
            "(YYYY-MM-DD or relative like 30d, 2w, 6mon, 1y). Call list_filter_options to discover fields and values."
        )
    ),
]
Sort = Annotated[
    str | None,
    Field(description="Sort as 'field:dir', comma-separated for multiple, e.g. 'addedAt:desc' or 'year:desc,titleSort'."),
]


@dataclass
class ToolDeps:
    mcp: MCPServer
    ctx: PlexContext
    settings: Settings

    def limit(self, requested: int) -> int:
        return max(1, min(requested, self.settings.max_results))


def page_info(returned: int, offset: int, limit: int, total: int | None) -> dict[str, Any]:
    """Paging fields shared by list/search tools, so agents can tell when a result set is incomplete.

    `limit` is the effective page size after PLEX_MCP_MAX_RESULTS is applied, which may be lower than requested.
    If Plex didn't report a total, a full page is treated as a sign that more results may exist.
    """
    has_more = offset + returned < total if total is not None else returned >= limit
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": returned,
        "has_more": has_more,
        "next_offset": offset + returned if has_more else None,
    }
