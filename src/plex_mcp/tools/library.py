"""Read-only tools: browse libraries, search, and inspect items."""

from typing import Annotated, Any, Literal

from pydantic import Field

from plex_mcp import __version__
from plex_mcp.plex import detail, summarize, summarize_many
from plex_mcp.tools._common import READ, Filters, Library, LibType, RatingKey, Sort, ToolDeps


def register(deps: ToolDeps) -> None:
    mcp, ctx, settings = deps.mcp, deps.ctx, deps.settings

    @mcp.tool(title="Server info", annotations=READ)
    def server_info() -> dict[str, Any]:
        """Show the Plex server's name and version, its libraries, and this MCP server's safety settings.
        A good first call."""
        plex = ctx.server
        return {
            "plex_server": {
                "name": plex.friendlyName,
                "version": plex.version,
                "platform": f"{plex.platform} {plex.platformVersion}",
            },
            "libraries": [
                {"id": int(s.key), "title": s.title, "type": s.type} for s in ctx.sections()
            ],
            "mcp_server": {
                "version": __version__,
                "read_only": settings.read_only,
                "delete_tools_enabled": settings.allow_delete and not settings.read_only,
                "library_allowlist": settings.allowed_libraries or "all",
                "max_results": settings.max_results,
            },
        }

    @mcp.tool(title="List libraries", annotations=READ)
    def list_libraries() -> dict[str, Any]:
        """List library sections with their ID, type, agent, item count, and folders on disk."""
        libraries = []
        for s in ctx.sections():
            libraries.append(
                {
                    "id": int(s.key),
                    "title": s.title,
                    "type": s.type,
                    "agent": s.agent,
                    "language": s.language,
                    "item_count": s.totalSize,
                    "folders": list(s.locations),
                    "updated_at": s.updatedAt.isoformat() if s.updatedAt else None,
                }
            )
        return {"libraries": libraries}

    @mcp.tool(title="Search a library", annotations=READ)
    def search_library(
        library: Library,
        query: Annotated[str | None, Field(description="Partial title match.")] = None,
        libtype: LibType = None,
        filters: Filters = None,
        sort: Sort = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
        offset: Annotated[int, Field(ge=0, description="Skip this many results (for paging).")] = 0,
    ) -> dict[str, Any]:
        """Search one library by title, type, and advanced Plex filters, returning compact summaries
        with rating_keys. Use this to find items before editing them or adding them to collections/playlists."""
        section = ctx.section(library)
        limit = deps.limit(limit)
        results = section.search(
            title=query,
            libtype=libtype,
            filters=filters or None,
            sort=sort,
            maxresults=limit,
            container_start=offset or None,
        )
        return {
            "library": section.title,
            "offset": offset,
            "returned": len(results),
            "results": summarize_many(results),
        }

    @mcp.tool(title="Search everything", annotations=READ)
    def search_all(
        query: Annotated[str, Field(min_length=1)],
        mediatype: Annotated[
            str | None,
            Field(description="Optional: movie, show, season, episode, artist, album, track, collection, playlist, actor, genre..."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Results per result group.")] = 10,
    ) -> dict[str, Any]:
        """Fuzzy, spell-checked search across all libraries (like the Plex search bar).
        Also matches people and tags."""
        results = []
        for obj in ctx.server.search(query, mediatype=mediatype, limit=limit):
            rating_key = obj.__dict__.get("ratingKey")
            if rating_key is None:
                results.append({"type": getattr(obj, "TAG", "tag").lower(), "tag": getattr(obj, "tag", None)})
                continue
            if not ctx.is_section_allowed(obj.__dict__.get("librarySectionID"), obj.__dict__.get("librarySectionTitle")) \
                    and obj.__dict__.get("librarySectionID") is not None:
                continue
            results.append(summarize(obj))
        return {"query": query, "results": results}

    @mcp.tool(title="Get item details", annotations=READ)
    def get_item(rating_key: RatingKey) -> dict[str, Any]:
        """Get full metadata for any item (movie, show, season, episode, artist, album, track,
        collection, or playlist): fields, tags, ratings, locked fields, external IDs, and media/file info."""
        return detail(ctx.item(rating_key))

    @mcp.tool(title="List children", annotations=READ)
    def list_children(
        rating_key: RatingKey,
        leaves: Annotated[
            bool,
            Field(description="For a show or artist, return every episode/track instead of seasons/albums."),
        ] = False,
        limit: Annotated[int, Field(ge=1, le=5000)] = 200,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """List what is inside an item: seasons/episodes of a show, albums/tracks of an artist,
        or the items in a collection or playlist (in order)."""
        obj = ctx.item(rating_key)
        kind = obj.TYPE
        if kind in {"collection", "playlist"}:
            children = obj.items()
        elif leaves:
            children = obj.fetchItems(f"/library/metadata/{obj.ratingKey}/allLeaves")
        else:
            children = obj.fetchItems(f"/library/metadata/{obj.ratingKey}/children")
        total = len(children)
        page = children[offset : offset + deps.limit(limit)]
        return {
            "parent": summarize(obj),
            "total": total,
            "offset": offset,
            "children": summarize_many(page),
        }

    @mcp.tool(title="Recently added", annotations=READ)
    def recently_added(
        library: Annotated[str | None, Field(description="Library title or ID. Omit for all libraries.")] = None,
        libtype: LibType = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 25,
    ) -> dict[str, Any]:
        """List the most recently added items, newest first."""
        limit = deps.limit(limit)
        if library is not None:
            items = ctx.section(library).recentlyAdded(maxresults=limit, libtype=libtype)
        else:
            items = []
            for section in ctx.sections():
                items.extend(section.recentlyAdded(maxresults=limit, libtype=libtype))
            items.sort(key=lambda o: o.__dict__.get("addedAt") or 0, reverse=True)
        return {"results": summarize_many(items[:limit])}

    @mcp.tool(title="Filter and sort options", annotations=READ)
    def list_filter_options(
        library: Library,
        libtype: LibType = None,
        field: Annotated[
            str | None,
            Field(description="If set (e.g. 'genre', 'contentRating', 'studio'), also return the values that exist for it."),
        ] = None,
        include_advanced_fields: Annotated[
            bool, Field(description="Also list every advanced filter field (larger response).")
        ] = False,
    ) -> dict[str, Any]:
        """Discover the filter fields, sort fields, and existing tag values for a library.
        Use before building search filters or smart collections/playlists."""
        section = ctx.section(library)
        out: dict[str, Any] = {
            "library": section.title,
            "libtype": libtype or section.TYPE,
            "filters": [{"key": f.filter, "title": f.title, "type": f.filterType} for f in section.listFilters(libtype)],
            "sorts": [{"key": s.key, "title": s.title} for s in section.listSorts(libtype)],
        }
        if include_advanced_fields:
            out["advanced_fields"] = [
                {"key": f.key, "title": f.title, "type": f.type} for f in section.listFields(libtype)
            ]
        if field:
            choices = section.listFilterChoices(field, libtype=libtype)
            out["choices"] = {"field": field, "values": [c.title for c in choices[:1000]], "total": len(choices)}
        return out

    @mcp.tool(title="List collections", annotations=READ)
    def list_collections(
        library: Library,
        query: Annotated[str | None, Field(description="Partial collection title.")] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        """List collections in a library (regular and smart), with rating_keys and item counts."""
        section = ctx.section(library)
        results = section.search(title=query, libtype="collection", maxresults=deps.limit(limit))
        return {"library": section.title, "collections": summarize_many(results)}

    @mcp.tool(title="List playlists", annotations=READ)
    def list_playlists(
        playlist_type: Annotated[Literal["video", "audio", "photo"] | None, Field()] = None,
        query: Annotated[str | None, Field(description="Partial playlist title.")] = None,
    ) -> dict[str, Any]:
        """List playlists owned by the server's admin account."""
        playlists = ctx.server.playlists(playlistType=playlist_type)
        if query:
            needle = query.casefold()
            playlists = [p for p in playlists if needle in (p.title or "").casefold()]
        return {"playlists": summarize_many(playlists)}

    @mcp.tool(title="Find metadata matches", annotations=READ)
    def find_matches(
        rating_key: RatingKey,
        title: Annotated[str | None, Field(description="Title to search the metadata agent for.")] = None,
        year: Annotated[int | None, Field()] = None,
    ) -> dict[str, Any]:
        """Search the library's metadata agent for possible matches for a movie, show, artist, or album.
        Pass a returned guid to fix_match to re-match the item."""
        obj = ctx.item(rating_key)
        if not hasattr(obj, "matches"):
            raise ValueError(f"A {obj.TYPE} cannot be matched.")
        kwargs: dict[str, Any] = {}
        if title is not None:
            kwargs["title"] = title
        if year is not None:
            kwargs["year"] = year
        results = obj.matches(**kwargs)
        return {
            "item": summarize(obj),
            "current_guid": obj.__dict__.get("guid"),
            "matches": [
                {"guid": m.guid, "name": m.name, "year": m.year, "score": m.score}
                for m in results[:25]
            ],
        }

    @mcp.tool(title="List available artwork", annotations=READ)
    def list_artwork(
        rating_key: RatingKey,
        kind: Annotated[Literal["poster", "art", "logo", "square_art", "theme"], Field(description="'art' is the background.")] = "poster",
    ) -> dict[str, Any]:
        """List artwork options Plex has for an item (from agents and uploads). Pass an option's key to set_artwork."""
        obj = ctx.item(rating_key)
        method = {"poster": "posters", "art": "arts", "logo": "logos", "square_art": "squareArts", "theme": "themes"}[kind]
        if not hasattr(obj, method):
            raise ValueError(f"A {obj.TYPE} has no {kind} artwork.")
        return {
            "item": summarize(obj),
            "kind": kind,
            "options": [
                {"key": r.ratingKey, "provider": r.provider, "selected": bool(r.selected), "url": r.key}
                for r in getattr(obj, method)()
            ],
        }
