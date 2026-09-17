"""Plex connection management, access control, and object serialization."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Any, Iterable

import requests
from plexapi.exceptions import NotFound
from plexapi.library import LibrarySection
from plexapi.server import PlexServer

from plex_mcp.config import Settings

log = logging.getLogger(__name__)

# Tag types an item can carry, mapped to the plexapi attribute that lists them.
TAG_ATTRS: dict[str, str] = {
    "genre": "genres",
    "label": "labels",
    "collection": "collections",
    "country": "countries",
    "director": "directors",
    "writer": "writers",
    "producer": "producers",
    "mood": "moods",
    "style": "styles",
    "similar": "similar",
}


COLLECTION_MODES = {-1: "default", 0: "hide", 1: "hideItems", 2: "showItems"}
COLLECTION_SORTS = {0: "release", 1: "alpha", 2: "custom"}


class AccessDenied(PermissionError):
    """Raised when a request touches a library outside PLEX_MCP_ALLOWED_LIBRARIES."""


class PlexContext:
    """Owns the PlexServer connection and enforces the library allowlist."""

    def __init__(self, settings: Settings, server: PlexServer | None = None) -> None:
        self.settings = settings
        self._server = server
        self._lock = threading.Lock()
        self._allowed = {entry.casefold() for entry in settings.allowed_libraries}

    # ------------------------------------------------------------------ connection
    @property
    def server(self) -> PlexServer:
        if self._server is None:
            with self._lock:
                if self._server is None:
                    session = requests.Session()
                    session.verify = self.settings.plex_verify_ssl
                    log.info("Connecting to Plex at %s", self.settings.plex_url)
                    self._server = PlexServer(
                        self.settings.plex_url,
                        self.settings.plex_token.get_secret_value(),
                        session=session,
                        timeout=self.settings.plex_timeout,
                    )
        return self._server

    # ------------------------------------------------------------------ access control
    def is_section_allowed(self, section_id: int | str | None, section_title: str | None) -> bool:
        if not self._allowed:
            return True
        candidates = {str(section_id).casefold() if section_id is not None else None,
                      section_title.casefold() if section_title else None}
        return bool(candidates & self._allowed)

    def ensure_item_allowed(self, item: Any) -> None:
        if not self._allowed:
            return
        section_id = item.__dict__.get("librarySectionID")
        if section_id is None:
            # Playlists and other server-level objects have no library.
            return
        title = item.__dict__.get("librarySectionTitle")
        if title is None:
            try:
                title = self.server.library.sectionByID(int(section_id)).title
            except NotFound:
                title = None
        if not self.is_section_allowed(section_id, title):
            raise AccessDenied(
                f"Item {item.__dict__.get('ratingKey')} is in library '{title or section_id}', "
                "which this server is not allowed to access."
            )

    # ------------------------------------------------------------------ lookups
    def sections(self) -> list[LibrarySection]:
        return [s for s in self.server.library.sections() if self.is_section_allowed(s.key, s.title)]

    def section(self, library: str | int) -> LibrarySection:
        """Resolve a library by numeric ID or (case-insensitive) title."""
        text = str(library).strip()
        matches = [
            s for s in self.server.library.sections()
            if str(s.key) == text or s.title.casefold() == text.casefold()
        ]
        if not matches:
            names = ", ".join(f"{s.title} (id {s.key})" for s in self.sections())
            raise NotFound(f"No library named or numbered '{library}'. Available: {names}")
        section = matches[0]
        if not self.is_section_allowed(section.key, section.title):
            raise AccessDenied(f"Library '{section.title}' is not in PLEX_MCP_ALLOWED_LIBRARIES.")
        return section

    def item(self, rating_key: int) -> Any:
        """Fetch a full metadata object (movie, show, episode, collection, playlist, ...)."""
        try:
            obj = self.server.fetchItem(int(rating_key))
        except NotFound as exc:
            raise NotFound(f"No item with rating_key {rating_key}.") from exc
        self.ensure_item_allowed(obj)
        return obj

    def items(self, rating_keys: Iterable[int]) -> list[Any]:
        return [self.item(key) for key in rating_keys]

    def playlist(self, rating_key: int) -> Any:
        obj = self.item(rating_key)
        if getattr(obj, "TYPE", None) != "playlist":
            raise ValueError(f"rating_key {rating_key} is a {obj.TYPE}, not a playlist.")
        return obj

    def collection(self, rating_key: int) -> Any:
        obj = self.item(rating_key)
        if getattr(obj, "TYPE", None) != "collection":
            raise ValueError(f"rating_key {rating_key} is a {obj.TYPE}, not a collection.")
        return obj


# ====================================================================== serialization


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _raw(obj: Any, attr: str, default: Any = None) -> Any:
    """Read an attribute without triggering plexapi's auto-reload (an extra HTTP call)."""
    return obj.__dict__.get(attr, default)


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v not in (None, [], {}, "")}


def summarize(obj: Any) -> dict[str, Any]:
    """A compact, cheap summary suitable for lists of results."""
    kind = _raw(obj, "type") or getattr(obj, "TYPE", None)
    data: dict[str, Any] = {
        "rating_key": _raw(obj, "ratingKey"),
        "type": kind,
        "title": _raw(obj, "title"),
        "year": _raw(obj, "year"),
        "library": _raw(obj, "librarySectionTitle"),
        "library_id": _raw(obj, "librarySectionID"),
    }
    if kind in {"episode", "track"}:
        data["show_or_artist"] = _raw(obj, "grandparentTitle")
        data["season_or_album"] = _raw(obj, "parentTitle")
        data["season_number" if kind == "episode" else "disc_number"] = _raw(obj, "parentIndex")
        data["episode_number" if kind == "episode" else "track_number"] = _raw(obj, "index")
    elif kind in {"season", "album"}:
        data["show_or_artist"] = _raw(obj, "parentTitle")
        data["index"] = _raw(obj, "index")
    if kind in {"collection", "playlist"}:
        data["smart"] = _raw(obj, "smart")
        data["item_count"] = _raw(obj, "leafCount") if kind == "playlist" else _raw(obj, "childCount")
        data["subtype"] = _raw(obj, "subtype") or _raw(obj, "playlistType")
    else:
        data["child_count"] = _raw(obj, "childCount")
        data["leaf_count"] = _raw(obj, "leafCount")
        data["view_count"] = _raw(obj, "viewCount")
    data["added_at"] = _iso(_raw(obj, "addedAt"))
    return _clean(data)


def summarize_many(objs: Iterable[Any]) -> list[dict[str, Any]]:
    return [summarize(o) for o in objs]


def _tags(obj: Any, attr: str) -> list[str]:
    try:
        values = getattr(obj, attr, None) or []
    except Exception:  # noqa: BLE001 - plexapi can raise on odd objects
        return []
    return [getattr(t, "tag", str(t)) for t in values]


def detail(obj: Any) -> dict[str, Any]:
    """Full metadata for a single, fully-loaded object."""
    data = summarize(obj)
    data.update(
        {
            "summary": getattr(obj, "summary", None),
            "tagline": getattr(obj, "tagline", None),
            "sort_title": getattr(obj, "titleSort", None),
            "original_title": getattr(obj, "originalTitle", None),
            "edition_title": getattr(obj, "editionTitle", None),
            "studio": getattr(obj, "studio", None),
            "content_rating": getattr(obj, "contentRating", None),
            "originally_available_at": _iso(getattr(obj, "originallyAvailableAt", None)),
            "critic_rating": getattr(obj, "rating", None),
            "audience_rating": getattr(obj, "audienceRating", None),
            "user_rating": getattr(obj, "userRating", None),
            "duration_ms": getattr(obj, "duration", None),
            "last_viewed_at": _iso(getattr(obj, "lastViewedAt", None)),
            "updated_at": _iso(getattr(obj, "updatedAt", None)),
            "guid": getattr(obj, "guid", None),
            "external_ids": [g.id for g in (getattr(obj, "guids", None) or [])],
        }
    )

    tags = {name: _tags(obj, attr) for name, attr in TAG_ATTRS.items()}
    roles = getattr(obj, "roles", None) or []
    tags["actor"] = [
        f"{r.tag} as {r.role}" if getattr(r, "role", None) else r.tag for r in roles[:20]
    ]
    data["tags"] = _clean(tags)

    fields = getattr(obj, "fields", None) or []
    data["locked_fields"] = sorted(f.name for f in fields if getattr(f, "locked", False))

    media = []
    for m in (getattr(obj, "media", None) or [])[:5]:
        media.append(
            _clean(
                {
                    "resolution": getattr(m, "videoResolution", None),
                    "video_codec": getattr(m, "videoCodec", None),
                    "audio_codec": getattr(m, "audioCodec", None),
                    "audio_channels": getattr(m, "audioChannels", None),
                    "container": getattr(m, "container", None),
                    "bitrate_kbps": getattr(m, "bitrate", None),
                    "files": [p.file for p in (getattr(m, "parts", None) or [])],
                }
            )
        )
    data["media"] = media

    kind = data.get("type")
    if kind == "collection":
        data["content_type"] = getattr(obj, "subtype", None)
        data["collection_mode"] = COLLECTION_MODES.get(getattr(obj, "collectionMode", None))
        data["item_order"] = COLLECTION_SORTS.get(getattr(obj, "collectionSort", None))
        if getattr(obj, "smart", False):
            data["smart_filters"] = _safe(lambda: obj.filters())
    elif kind == "playlist":
        data["playlist_type"] = getattr(obj, "playlistType", None)
        if getattr(obj, "smart", False):
            data["smart_filters"] = _safe(lambda: obj.filters())
    return _clean(data)


def _safe(fn: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return f"<unavailable: {exc}>"
