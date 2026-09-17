"""Write tools for item metadata: fields, tags, locks, artwork, ratings, watched state, matching."""

import os
import re
from typing import Annotated, Any, Literal

from pydantic import Field

from plex_mcp.plex import TAG_ATTRS, detail, summarize
from plex_mcp.tools._common import WRITE, WRITE_NON_IDEMPOTENT, Library, RatingKey, RatingKeys, ToolDeps

# friendly name -> (plexapi edit method, Plex field key used for locking)
FIELD_EDITORS: dict[str, tuple[str, str]] = {
    "title": ("editTitle", "title"),
    "sort_title": ("editSortTitle", "titleSort"),
    "original_title": ("editOriginalTitle", "originalTitle"),
    "summary": ("editSummary", "summary"),
    "tagline": ("editTagline", "tagline"),
    "studio": ("editStudio", "studio"),
    "content_rating": ("editContentRating", "contentRating"),
    "originally_available_at": ("editOriginallyAvailable", "originallyAvailableAt"),
    "year": ("", "year"),
    "critic_rating": ("editCriticRating", "rating"),
    "audience_rating": ("editAudienceRating", "audienceRating"),
    "edition_title": ("editEditionTitle", "editionTitle"),
    "track_number": ("editTrackNumber", "index"),
    "disc_number": ("editDiscNumber", "parentIndex"),
    "added_at": ("editAddedAt", "addedAt"),
}
ARTWORK_LOCKS = {"poster": "thumb", "art": "art", "theme": "theme", "logo": "clearLogo", "square_art": "squareArt"}

TagType = Literal["genre", "label", "collection", "country", "director", "writer", "producer", "mood", "style", "similar"]
LockableField = Literal[
    "title", "sort_title", "original_title", "summary", "tagline", "studio", "content_rating",
    "originally_available_at", "year", "critic_rating", "audience_rating", "edition_title",
    "track_number", "disc_number", "added_at",
    "genre", "label", "collection", "country", "director", "writer", "producer", "mood", "style", "similar",
    "poster", "art", "theme", "logo", "square_art",
]
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Only media files may be uploaded from the server's disk, so the tool can't be used to copy arbitrary files into Plex.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tbn"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def register(deps: ToolDeps) -> None:
    mcp, ctx = deps.mcp, deps.ctx

    @mcp.tool(title="Edit metadata fields", annotations=WRITE)
    def edit_metadata(
        rating_key: RatingKey,
        title: str | None = None,
        sort_title: str | None = None,
        original_title: Annotated[str | None, Field(description="For tracks this is the track artist.")] = None,
        summary: str | None = None,
        tagline: str | None = None,
        studio: str | None = None,
        content_rating: Annotated[str | None, Field(description="e.g. PG-13, TV-MA")] = None,
        originally_available_at: Annotated[str | None, Field(description="Release date, YYYY-MM-DD.")] = None,
        year: Annotated[int | None, Field(ge=1800, le=2200)] = None,
        critic_rating: Annotated[float | None, Field(ge=0, le=10)] = None,
        audience_rating: Annotated[float | None, Field(ge=0, le=10)] = None,
        edition_title: Annotated[str | None, Field(description="Movie edition, e.g. \"Director's Cut\".")] = None,
        track_number: Annotated[int | None, Field(ge=0)] = None,
        disc_number: Annotated[int | None, Field(ge=0)] = None,
        added_at: Annotated[str | None, Field(description="Date added, YYYY-MM-DD (changes sort order in 'Recently Added').")] = None,
        user_rating: Annotated[
            float | None, Field(ge=-1, le=10, description="Your star rating, 0-10 (10 = 5 stars). -1 clears it.")
        ] = None,
        lock: Annotated[
            bool, Field(description="Lock edited fields so Plex's agents don't overwrite them on refresh.")
        ] = True,
    ) -> dict[str, Any]:
        """Edit metadata fields on one item (movie, show, season, episode, artist, album, track, collection,
        or playlist). Only the fields you pass change; pass an empty string to clear a text field.
        All field edits are sent in a single request. Returns the updated item."""
        obj = ctx.item(rating_key)
        values = {
            "title": title, "sort_title": sort_title, "original_title": original_title, "summary": summary,
            "tagline": tagline, "studio": studio, "content_rating": content_rating,
            "originally_available_at": originally_available_at, "year": year, "critic_rating": critic_rating,
            "audience_rating": audience_rating, "edition_title": edition_title, "track_number": track_number,
            "disc_number": disc_number, "added_at": added_at,
        }
        changes = {k: v for k, v in values.items() if v is not None}
        if not changes and user_rating is None:
            raise ValueError("No fields to edit were provided.")

        for name in ("originally_available_at", "added_at"):
            if changes.get(name) and not _DATE.match(changes[name]):
                raise ValueError(f"{name} must be YYYY-MM-DD, got {changes[name]!r}.")

        unsupported = [
            name for name in changes
            if not hasattr(obj, FIELD_EDITORS[name][0] or "editField")
        ]
        if unsupported:
            raise ValueError(f"A {obj.TYPE} does not support editing: {', '.join(unsupported)}.")
        if user_rating is not None and not hasattr(obj, "rate"):
            raise ValueError(f"A {obj.TYPE} cannot be rated.")

        if changes:
            obj.batchEdits()
            for name, value in changes.items():
                method, key = FIELD_EDITORS[name]
                if method:
                    getattr(obj, method)(value, locked=lock)
                else:
                    obj.editField(key, value, locked=lock)
            obj.saveEdits()
        if user_rating is not None:
            obj.rate(None if user_rating < 0 else user_rating)

        return {"updated_fields": sorted(changes) + (["user_rating"] if user_rating is not None else []),
                "item": detail(ctx.item(rating_key))}

    @mcp.tool(title="Add or remove tags", annotations=WRITE)
    def edit_tags(
        rating_keys: RatingKeys,
        tag_type: Annotated[TagType, Field(description="Which tag list to edit. 'collection' adds/removes collection membership.")],
        add: Annotated[list[str], Field(description="Tags to add (existing tags are kept).")] = [],  # noqa: B006
        remove: Annotated[list[str], Field(description="Tags to remove.")] = [],  # noqa: B006
        lock: Annotated[bool, Field(description="Lock this tag list so agents don't overwrite it.")] = True,
    ) -> dict[str, Any]:
        """Add and/or remove genres, labels, collections, moods, styles, directors, etc. on one or more items.
        Adding to the 'collection' tag creates the collection automatically if it doesn't exist."""
        if not add and not remove:
            raise ValueError("Provide at least one tag to add or remove.")
        attr = TAG_ATTRS[tag_type]
        method_suffix = {"similar": "SimilarArtist"}.get(tag_type, tag_type.capitalize())
        results, errors = [], []
        for key in rating_keys:
            try:
                obj = ctx.item(key)
                adder, remover = getattr(obj, f"add{method_suffix}", None), getattr(obj, f"remove{method_suffix}", None)
                if adder is None:
                    raise ValueError(f"A {obj.TYPE} has no {tag_type} tags.")
                if add:
                    adder(add, locked=lock)
                if remove:
                    if add:
                        obj = ctx.item(key)
                        remover = getattr(obj, f"remove{method_suffix}")
                    remover(remove, locked=lock)
                refreshed = ctx.item(key)
                results.append({**summarize(refreshed), tag_type: [t.tag for t in getattr(refreshed, attr, []) or []]})
            except Exception as exc:  # noqa: BLE001 - report per-item failures, keep going
                errors.append({"rating_key": key, "error": str(exc)})
        if errors and not results:
            raise RuntimeError(f"All edits failed: {errors}")
        return {"updated": results, "errors": errors}

    @mcp.tool(title="Lock or unlock fields", annotations=WRITE)
    def set_field_locks(
        rating_keys: RatingKeys,
        fields: Annotated[list[LockableField], Field(min_length=1)],
        locked: bool = True,
    ) -> dict[str, Any]:
        """Lock or unlock metadata fields, tag lists, or artwork without changing their values.
        Locked fields are protected from being overwritten when Plex refreshes metadata."""
        results, errors = [], []
        for key in rating_keys:
            try:
                obj = ctx.item(key)
                edits: dict[str, int] = {}
                for name in fields:
                    plex_key = (
                        FIELD_EDITORS[name][1] if name in FIELD_EDITORS
                        else ARTWORK_LOCKS.get(name, name)
                    )
                    edits[f"{plex_key}.locked"] = 1 if locked else 0
                obj.edit(**edits)
                refreshed = ctx.item(key)
                results.append({**summarize(refreshed),
                                "locked_fields": sorted(f.name for f in refreshed.fields if f.locked)})
            except Exception as exc:  # noqa: BLE001
                errors.append({"rating_key": key, "error": str(exc)})
        if errors and not results:
            raise RuntimeError(f"All edits failed: {errors}")
        return {"updated": results, "errors": errors}

    @mcp.tool(title="Set artwork", annotations=WRITE_NON_IDEMPOTENT)
    def set_artwork(
        rating_key: RatingKey,
        kind: Annotated[Literal["poster", "art", "logo", "square_art", "theme"], Field(description="'art' is the background.")] = "poster",
        url: Annotated[str | None, Field(description="Public http(s) URL of an image (or mp3 for theme) to upload.")] = None,
        file_path: Annotated[str | None, Field(description="Path to an image file on the Plex server machine.")] = None,
        option_key: Annotated[str | None, Field(description="A 'key' from list_artwork to select an existing option.")] = None,
        lock: Annotated[bool, Field(description="Lock the artwork so a refresh won't replace it.")] = True,
    ) -> dict[str, Any]:
        """Change an item's poster, background art, logo, square art, or theme music. Provide exactly one of
        url, file_path, or option_key."""
        provided = [v for v in (url, file_path, option_key) if v]
        if len(provided) != 1:
            raise ValueError("Provide exactly one of url, file_path, or option_key.")
        obj = ctx.item(rating_key)
        noun = {"poster": "Poster", "art": "Art", "logo": "Logo", "square_art": "SquareArt", "theme": "Theme"}[kind]
        if not hasattr(obj, f"upload{noun}"):
            raise ValueError(f"A {obj.TYPE} has no {kind} artwork.")
        if url:
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("url must start with http:// or https://")
            getattr(obj, f"upload{noun}")(url=url)
        elif file_path:
            allowed = AUDIO_EXTENSIONS if kind == "theme" else IMAGE_EXTENSIONS
            if os.path.splitext(file_path)[1].lower() not in allowed:
                raise ValueError(f"file_path must be one of: {', '.join(sorted(allowed))}")
            if not os.path.isfile(file_path):
                raise ValueError(f"File not found on the server: {file_path}")
            getattr(obj, f"upload{noun}")(filepath=file_path)
        else:
            plural = {"poster": "posters", "art": "arts", "logo": "logos", "square_art": "squareArts", "theme": "themes"}[kind]
            options = getattr(obj, plural)()
            match = next((o for o in options if o.ratingKey == option_key), None)
            if match is None:
                raise ValueError(f"No {kind} option with key {option_key!r}. Call list_artwork first.")
            getattr(obj, f"set{noun}")(match)
        if lock and hasattr(obj, f"lock{noun}"):
            getattr(obj, f"lock{noun}")()
        return {"item": summarize(obj), "kind": kind, "locked": lock, "status": "artwork updated"}

    @mcp.tool(title="Mark played or unplayed", annotations=WRITE)
    def set_played(rating_keys: RatingKeys, played: bool = True) -> dict[str, Any]:
        """Mark items (or whole shows/seasons/albums) as played or unplayed for the admin account."""
        updated, errors = [], []
        for key in rating_keys:
            try:
                obj = ctx.item(key)
                (obj.markPlayed if played else obj.markUnplayed)()
                updated.append(summarize(obj))
            except Exception as exc:  # noqa: BLE001
                errors.append({"rating_key": key, "error": str(exc)})
        return {"played": played, "updated": updated, "errors": errors}

    @mcp.tool(title="Refresh item metadata", annotations=WRITE_NON_IDEMPOTENT)
    def refresh_item(rating_key: RatingKey) -> dict[str, Any]:
        """Ask Plex to re-download metadata for an item from its agent. Locked fields are kept.
        Runs in the background on the Plex server."""
        obj = ctx.item(rating_key)
        obj.refresh()
        return {"item": summarize(obj), "status": "refresh requested"}

    @mcp.tool(title="Scan library", annotations=WRITE_NON_IDEMPOTENT)
    def scan_library(
        library: Library,
        path: Annotated[str | None, Field(description="Only scan this folder (a path on the Plex server).")] = None,
        refresh_all_metadata: Annotated[
            bool, Field(description="Instead of scanning for new files, force-refresh metadata for the whole library (slow).")
        ] = False,
    ) -> dict[str, Any]:
        """Scan a library for new/removed files, or force a full metadata refresh. Runs in the background."""
        section = ctx.section(library)
        if refresh_all_metadata:
            section.refresh()
            status = "full metadata refresh requested"
        else:
            section.update(path=path)
            status = f"scan requested{f' for {path}' if path else ''}"
        return {"library": section.title, "status": status}

    @mcp.tool(title="Fix match", annotations=WRITE_NON_IDEMPOTENT)
    def fix_match(
        rating_key: RatingKey,
        guid: Annotated[str | None, Field(description="A guid returned by find_matches.")] = None,
        auto: Annotated[bool, Field(description="Use the agent's top match instead of a guid.")] = False,
    ) -> dict[str, Any]:
        """Re-match an item to different metadata (like 'Fix Match' in Plex Web). Use find_matches first."""
        obj = ctx.item(rating_key)
        if not hasattr(obj, "fixMatch"):
            raise ValueError(f"A {obj.TYPE} cannot be matched.")
        if auto:
            obj.fixMatch(auto=True)
        else:
            if not guid:
                raise ValueError("Provide a guid from find_matches, or set auto=true.")
            match = next((m for m in obj.matches() if m.guid == guid), None)
            if match is None:
                # Agents don't always return the same list twice; build a minimal result object.
                from plexapi.media import SearchResult

                match = SearchResult.__new__(SearchResult)
                match.guid, match.name = guid, obj.title
            obj.fixMatch(searchResult=match)
        return {"item": summarize(ctx.item(rating_key)), "status": "match updated; metadata refresh will follow"}

    @mcp.tool(title="Unmatch item", annotations=WRITE_NON_IDEMPOTENT)
    def unmatch_item(rating_key: RatingKey) -> dict[str, Any]:
        """Remove an item's metadata match, leaving it unmatched (like 'Unmatch' in Plex Web)."""
        obj = ctx.item(rating_key)
        if not hasattr(obj, "unmatch"):
            raise ValueError(f"A {obj.TYPE} cannot be unmatched.")
        obj.unmatch()
        return {"item": summarize(obj), "status": "unmatched"}
