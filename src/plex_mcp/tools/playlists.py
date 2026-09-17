"""Playlist tools: create, update, delete. (Reordering lives in collections.move_item.)"""

from typing import Annotated, Any

from pydantic import Field

from plex_mcp.plex import detail, summarize
from plex_mcp.tools._common import DESTRUCTIVE, WRITE, WRITE_NON_IDEMPOTENT, RatingKey, ToolDeps
from plex_mcp.tools.collections import SmartRules


def register(deps: ToolDeps) -> None:
    mcp, ctx, settings = deps.mcp, deps.ctx, deps.settings

    @mcp.tool(title="Create playlist", annotations=WRITE_NON_IDEMPOTENT)
    def create_playlist(
        title: Annotated[str, Field(min_length=1)],
        rating_keys: Annotated[
            list[int] | None,
            Field(description="Items in play order. Shows/seasons/albums expand to their episodes/tracks. "
                              "Don't mix video, audio, and photo."),
        ] = None,
        smart_library: Annotated[
            str | None, Field(description="Smart playlists: library title or ID the rules apply to.")
        ] = None,
        smart: Annotated[SmartRules | None, Field(description="Rules for a smart playlist (instead of rating_keys).")] = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Create a regular playlist from specific items, or a smart playlist from filter rules on one library.
        Fails if a playlist with the same title already exists (use update_playlist)."""
        if bool(rating_keys) == bool(smart):
            raise ValueError("Provide either rating_keys (regular playlist) or smart + smart_library, not both.")
        existing = [p for p in ctx.server.playlists() if p.title.casefold() == title.casefold()]
        if existing:
            raise ValueError(
                f"A playlist named '{existing[0].title}' already exists (rating_key {existing[0].ratingKey}). "
                "Use update_playlist to change it."
            )
        if smart:
            if not smart_library:
                raise ValueError("smart_library is required for a smart playlist.")
            section = ctx.section(smart_library)
            playlist = ctx.server.createPlaylist(
                title, section=section, smart=True, libtype=smart.libtype,
                filters=smart.filters or None, sort=smart.sort, limit=smart.limit,
            )
        else:
            playlist = ctx.server.createPlaylist(title, items=ctx.items(rating_keys or []))
        if summary is not None:
            playlist.editSummary(summary)
        return {"status": "created", "playlist": detail(ctx.item(playlist.ratingKey))}

    @mcp.tool(title="Update playlist", annotations=WRITE)
    def update_playlist(
        rating_key: RatingKey,
        title: str | None = None,
        summary: str | None = None,
        add_rating_keys: Annotated[list[int] | None, Field(description="Regular playlists: items to append.")] = None,
        remove_rating_keys: Annotated[list[int] | None, Field(description="Regular playlists: items to remove.")] = None,
        smart: Annotated[SmartRules | None, Field(description="Smart playlists: replace the rules (libtype is ignored).")] = None,
    ) -> dict[str, Any]:
        """Rename, re-describe, append/remove items, or change the rules of an existing playlist.
        Use move_item to reorder."""
        playlist = ctx.playlist(rating_key)
        if (add_rating_keys or remove_rating_keys) and playlist.smart:
            raise ValueError("This is a smart playlist; change its rules with 'smart' instead.")
        if smart and not playlist.smart:
            raise ValueError("This is a regular playlist; it has no smart rules.")

        if add_rating_keys:
            playlist.addItems(ctx.items(add_rating_keys))
        if remove_rating_keys:
            current = {i.ratingKey: i for i in playlist.items()}
            missing = [k for k in remove_rating_keys if k not in current]
            if missing:
                raise ValueError(f"Not in the playlist: {missing}")
            playlist.removeItems([current[k] for k in remove_rating_keys])
        if smart:
            playlist.updateFilters(filters=smart.filters or None, sort=smart.sort, limit=smart.limit)

        edits = {"editTitle": title, "editSummary": summary}
        edits = {k: v for k, v in edits.items() if v is not None}
        if edits:
            playlist.batchEdits()
            for method, value in edits.items():
                getattr(playlist, method)(value)
            playlist.saveEdits()
        return {"status": "updated", "playlist": detail(ctx.item(rating_key))}

    if settings.allow_delete:

        @mcp.tool(title="Delete playlist", annotations=DESTRUCTIVE)
        def delete_playlist(
            rating_key: RatingKey,
            confirm_title: Annotated[str, Field(description="The playlist's exact title, as a safety check.")],
        ) -> dict[str, Any]:
            """Permanently delete a playlist. The media in it is NOT deleted."""
            playlist = ctx.playlist(rating_key)
            if playlist.title != confirm_title:
                raise ValueError(
                    f"confirm_title {confirm_title!r} does not match the playlist's title {playlist.title!r}."
                )
            info = summarize(playlist)
            playlist.delete()
            return {"status": "deleted", "playlist": info}
