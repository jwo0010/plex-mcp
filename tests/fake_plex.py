"""A tiny in-memory imitation of the Plex Media Server HTTP API.

It implements just enough XML endpoints for python-plexapi to exercise this project's tools end to end,
so tests run without a real Plex server. It is not a faithful Plex implementation.
"""

from __future__ import annotations

import itertools
import threading
import time
import xml.etree.ElementTree as ET
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

MACHINE_ID = "fake-machine-id"
TYPE_IDS = {"movie": 1, "show": 2, "season": 3, "episode": 4, "collection": 18}
TYPE_NAMES = {v: k for k, v in TYPE_IDS.items()}
TAG_ELEMENTS = {"genre": "Genre", "label": "Label", "collection": "Collection", "director": "Director",
                "writer": "Writer", "country": "Country", "producer": "Producer"}
NOW = int(time.time())


def _movie(rk: int, title: str, year: int, genres: list[str], added_offset: int) -> dict[str, Any]:
    return {
        "ratingKey": rk, "type": "movie", "title": title, "year": year, "section": 1,
        "summary": f"{title} summary", "studio": "Studio", "contentRating": "R",
        "addedAt": NOW - added_offset, "genre": list(genres), "label": [], "collection": [],
        "director": ["Someone"], "writer": [], "country": [], "producer": [], "locks": set(),
        "file": f"D:\\Movies\\{title} ({year}).mkv", "viewCount": 0,
    }


class FakePlexState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[tuple[str, str]] = []
        self.sections = {
            1: {"key": 1, "title": "Movies", "type": "movie", "agent": "tv.plex.agents.movie", "path": "D:\\Movies"},
            2: {"key": 2, "title": "TV Shows", "type": "show", "agent": "tv.plex.agents.series", "path": "D:\\TV"},
        }
        self.items: dict[int, dict[str, Any]] = {
            101: _movie(101, "Alien", 1979, ["Horror", "Science Fiction"], 300),
            102: _movie(102, "Aliens", 1986, ["Action", "Science Fiction"], 200),
            103: _movie(103, "Heat", 1995, ["Crime"], 100),
            201: {"ratingKey": 201, "type": "show", "title": "The Office", "year": 2005, "section": 2,
                  "summary": "Office show", "addedAt": NOW - 50, "genre": ["Comedy"], "label": [],
                  "collection": [], "locks": set(), "children": [202]},
            202: {"ratingKey": 202, "type": "season", "title": "Season 1", "index": 1, "parent": 201,
                  "section": 2, "addedAt": NOW - 50, "locks": set(), "children": [203, 204]},
            203: {"ratingKey": 203, "type": "episode", "title": "Pilot", "index": 1, "parent": 202,
                  "section": 2, "addedAt": NOW - 50, "locks": set(), "label": [], "collection": []},
            204: {"ratingKey": 204, "type": "episode", "title": "Diversity Day", "index": 2, "parent": 202,
                  "section": 2, "addedAt": NOW - 40, "locks": set(), "label": [], "collection": []},
        }
        self.collections: dict[int, dict[str, Any]] = {}
        self.playlists: dict[int, dict[str, Any]] = {}
        self.ids = itertools.count(1000)
        self.playlist_item_ids = itertools.count(5000)

    # --------------------------------------------------------------- xml builders
    def container(self, **attrs: Any) -> ET.Element:
        return ET.Element("MediaContainer", {k: str(v) for k, v in attrs.items()})

    def item_element(self, item: dict[str, Any], full: bool = True) -> ET.Element:
        sec = self.sections[item["section"]]
        tag = "Directory" if item["type"] in {"show", "season"} else "Video"
        attrs = {
            "ratingKey": item["ratingKey"], "key": f"/library/metadata/{item['ratingKey']}", "type": item["type"],
            "title": item["title"], "librarySectionID": sec["key"], "librarySectionTitle": sec["title"],
            "librarySectionKey": f"/library/sections/{sec['key']}", "addedAt": item["addedAt"],
        }
        for k in ("year", "summary", "studio", "contentRating", "index", "viewCount", "titleSort", "tagline", "guid",
                  "originallyAvailableAt", "userRating", "rating"):
            if item.get(k) is not None:
                attrs[k] = item[k]
        if item["type"] in {"show", "season"}:
            attrs["key"] += "/children"
            attrs["leafCount"] = len(self.leaves(item))
            attrs["childCount"] = len(item.get("children", []))
        if item.get("parent"):
            parent = self.items[item["parent"]]
            attrs["parentRatingKey"] = parent["ratingKey"]
            attrs["parentTitle"] = parent["title"]
            attrs["parentIndex"] = parent.get("index", "")
            if parent.get("parent"):
                gp = self.items[parent["parent"]]
                attrs["grandparentRatingKey"] = gp["ratingKey"]
                attrs["grandparentTitle"] = gp["title"]
        el = ET.Element(tag, {k: str(v) for k, v in attrs.items()})
        for tag_type, element in TAG_ELEMENTS.items():
            for i, value in enumerate(item.get(tag_type, [])):
                ET.SubElement(el, element, {"id": str(i + 1), "tag": value})
        if full:
            for name in sorted(item.get("locks", ())):
                ET.SubElement(el, "Field", {"name": name, "locked": "1"})
            if item.get("file"):
                media = ET.SubElement(el, "Media", {"id": "1", "videoResolution": "1080", "videoCodec": "h264",
                                                   "audioCodec": "aac", "container": "mkv", "bitrate": "8000"})
                ET.SubElement(media, "Part", {"id": "1", "file": item["file"]})
        return el

    def leaves(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for child_key in item.get("children", []):
            child = self.items[child_key]
            out.extend(self.leaves(child) if child.get("children") else [child])
        return out

    def collection_element(self, coll: dict[str, Any], prefs: bool = False) -> ET.Element:
        sec = self.sections[coll["section"]]
        el = ET.Element("Directory", {k: str(v) for k, v in {
            "ratingKey": coll["ratingKey"], "key": f"/library/collections/{coll['ratingKey']}/children",
            "type": "collection", "title": coll["title"], "subtype": coll["subtype"],
            "smart": int(coll["smart"]), "childCount": len(self.collection_items(coll)),
            "librarySectionID": sec["key"], "librarySectionTitle": sec["title"],
            "librarySectionKey": f"/library/sections/{sec['key']}", "addedAt": coll["addedAt"],
            "collectionMode": coll["mode"], "collectionSort": coll["sort"], "summary": coll.get("summary", ""),
            "content": coll.get("content", ""), "titleSort": coll.get("titleSort", coll["title"]),
        }.items()})
        for name in sorted(coll["locks"]):
            ET.SubElement(el, "Field", {"name": name, "locked": "1"})
        if prefs:
            p = ET.SubElement(el, "Preferences")
            ET.SubElement(p, "Setting", {"id": "collectionMode", "type": "int", "default": "-1", "value": str(coll["mode"]),
                                         "enumValues": "-1:Library default|0:Hide collection|1:Hide items|2:Show both",
                                         "label": "Collection mode", "summary": "", "hidden": "0", "advanced": "0", "group": ""})
            ET.SubElement(p, "Setting", {"id": "collectionSort", "type": "int", "default": "0", "value": str(coll["sort"]),
                                         "enumValues": "0:Release date|1:Alphabetical|2:Custom",
                                         "label": "Collection sort", "summary": "", "hidden": "0", "advanced": "0", "group": ""})
        return el

    def collection_items(self, coll: dict[str, Any]) -> list[dict[str, Any]]:
        if coll["smart"]:
            names = self.genre_names(coll["section"])
            wanted = [names[int(g) - 1] if g.isdigit() else g for g in coll.get("genres", [])]
            return [i for i in self.items.values() if i["section"] == coll["section"] and i["type"] == coll["subtype"]
                    and all(g in i.get("genre", []) for g in wanted)]
        return [self.items[k] for k in coll["items"]]

    def genre_names(self, sid: int) -> list[str]:
        return sorted({g for i in self.items.values() if i["section"] == sid for g in i.get("genre", [])})

    def playlist_element(self, pl: dict[str, Any]) -> ET.Element:
        return ET.Element("Playlist", {k: str(v) for k, v in {
            "ratingKey": pl["ratingKey"], "key": f"/playlists/{pl['ratingKey']}/items", "type": "playlist",
            "title": pl["title"], "playlistType": pl["playlistType"], "smart": int(pl["smart"]),
            "leafCount": len(pl["items"]), "addedAt": pl["addedAt"], "summary": pl.get("summary", ""),
            "content": pl.get("content", ""),
        }.items()})

    def meta(self, section: dict[str, Any]) -> ET.Element:
        root = self.container(size=0)
        meta = ET.SubElement(root, "Meta")
        libtypes = [section["type"]] + (["season", "episode"] if section["type"] == "show" else [])
        for libtype in libtypes + ["collection"]:
            t = ET.SubElement(meta, "Type", {"key": f"/library/sections/{section['key']}/all?type={TYPE_IDS[libtype]}",
                                             "type": libtype, "title": libtype.title(), "active": "1"})
            ET.SubElement(t, "Filter", {"filter": "genre", "filterType": "string", "type": "filter", "title": "Genre",
                                        "key": f"/library/sections/{section['key']}/genre"})
            for key, title in (("titleSort", "Title"), ("addedAt", "Date Added"), ("year", "Year")):
                ET.SubElement(t, "Sort", {"key": key, "title": title, "defaultDirection": "asc",
                                          "descKey": f"{key}:desc", "firstCharacterKey": ""})
            for key, ftype, title in (("title", "string", "Title"), ("genre", "tag", "Genre"),
                                      ("year", "integer", "Year"), ("unwatched", "boolean", "Unwatched")):
                ET.SubElement(t, "Field", {"key": f"{libtype}.{key}" if libtype != section["type"] else key,
                                           "type": ftype, "title": title})
        for ftype, ops in (("tag", ["=", "!="]), ("integer", ["=", "!=", ">>=", "<<="]),
                           ("string", ["=", "!=", "==", "!=="]), ("boolean", ["=", "!="])):
            ft = ET.SubElement(meta, "FieldType", {"type": ftype})
            for op in ops:
                ET.SubElement(ft, "Operator", {"key": op, "title": op})
        return root

    # --------------------------------------------------------------- request handling
    def handle(self, method: str, raw_path: str, headers: dict[str, str] | None = None) -> tuple[int, ET.Element | None]:
        with self.lock:
            self.requests.append((method, unquote(raw_path)))
            url = urlparse(raw_path)
            path = url.path.rstrip("/") or "/"
            q = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            # plexapi sends paging as headers; accept either form like Plex does.
            for name in ("X-Plex-Container-Start", "X-Plex-Container-Size"):
                if headers and name in headers and name not in q:
                    q[name] = headers[name]
            parts = path.strip("/").split("/")
            return self._route(method, path, parts, q)

    def _route(self, method: str, path: str, parts: list[str], q: dict[str, str]) -> tuple[int, ET.Element | None]:
        if path == "/" and method == "GET":
            return 200, self.container(friendlyName="Fake Plex", machineIdentifier=MACHINE_ID, version="1.41.0",
                                       platform="Windows", platformVersion="11", myPlexUsername="tester")
        if path == "/library" and method == "GET":
            return 200, self.container(size=0, title1="Plex Library")
        if path == "/library/sections" and method == "GET":
            root = self.container(size=len(self.sections))
            for s in self.sections.values():
                d = ET.SubElement(root, "Directory", {"key": str(s["key"]), "title": s["title"], "type": s["type"],
                                                      "agent": s["agent"], "language": "en-US", "scanner": "",
                                                      "updatedAt": str(NOW), "uuid": f"uuid-{s['key']}"})
                ET.SubElement(d, "Location", {"id": str(s["key"]), "path": s["path"]})
            return 200, root
        if parts[:2] == ["library", "sections"] and len(parts) >= 4:
            return self._section_route(method, int(parts[2]), parts[3], q)
        if parts[:2] == ["library", "metadata"] and len(parts) >= 3:
            return self._metadata_route(method, int(parts[2]), parts[3:], q)
        if parts[:2] == ["library", "collections"]:
            return self._collection_route(method, parts[2:], q)
        if parts[0] == "playlists":
            return self._playlist_route(method, parts[1:], q)
        if path == "/:/scrobble":
            self.items[int(q["key"])]["viewCount"] = 1
            return 200, None
        if path == "/:/unscrobble":
            self.items[int(q["key"])]["viewCount"] = 0
            return 200, None
        if path == "/:/rate":
            self.items[int(q["key"])]["userRating"] = q["rating"]
            return 200, None
        if path == "/hubs/search":
            needle = q["query"].casefold()
            root = self.container()
            hub = ET.SubElement(root, "Hub", {"type": "movie", "hubIdentifier": "movie", "title": "Movies", "size": "1"})
            for item in self.items.values():
                if needle in item["title"].casefold():
                    hub.append(self.item_element(item, full=False))
            return 200, root
        return 404, None

    def _section_route(self, method: str, sid: int, action: str, q: dict[str, str]) -> tuple[int, ET.Element | None]:
        section = self.sections[sid]
        if action in {"all", "collections"} and "includeMeta" in q:
            return 200, self.meta(section)
        if action == "genre":
            genres = sorted({g for i in self.items.values() if i["section"] == sid for g in i.get("genre", [])})
            root = self.container()
            for n, g in enumerate(genres):
                ET.SubElement(root, "Directory", {"key": str(n + 1), "title": g, "type": "genre", "fastKey": ""})
            return 200, root
        if action == "all" and method == "PUT":
            for rk in q["id"].split(","):
                self.apply_edit(int(rk), q)
            return 200, None
        if action == "all" and method == "GET":
            libtype = TYPE_NAMES.get(int(q.get("type", 0)), section["type"])
            if libtype == "collection":
                pool: list[tuple[dict, ET.Element]] = [
                    (c, self.collection_element(c)) for c in self.collections.values() if c["section"] == sid]
            else:
                pool = [(i, self.item_element(i, full=False)) for i in self.items.values()
                        if i["section"] == sid and i["type"] == libtype]
            if "title" in q:
                pool = [p for p in pool if q["title"].casefold() in p[0]["title"].casefold()]
            if "genre" in q:
                wanted = set(q["genre"].split(","))
                names = sorted({g for i in self.items.values() if i["section"] == sid for g in i.get("genre", [])})
                wanted_names = {names[int(w) - 1] if w.isdigit() else w for w in wanted}
                pool = [p for p in pool if wanted_names & set(p[0].get("genre", []))]
            if "year>>" in q:
                pool = [p for p in pool if p[0].get("year", 0) > int(q["year>>"])]
            if q.get("sort", "").endswith("addedAt:desc"):
                pool.sort(key=lambda p: p[0]["addedAt"], reverse=True)
            start = int(q.get("X-Plex-Container-Start", 0))
            size = int(q.get("X-Plex-Container-Size", 100))
            page = pool[start:start + size]
            root = self.container(size=len(page), totalSize=len(pool), offset=start,
                                  librarySectionID=sid, librarySectionTitle=section["title"])
            for _, el in page:
                root.append(el)
            return 200, root
        if action == "refresh":
            return 200, None
        return 404, None

    def apply_edit(self, rk: int, q: dict[str, str]) -> None:
        target = self.items.get(rk) or self.collections.get(rk)
        for key, value in q.items():
            if key.endswith(".value"):
                target[key[:-6]] = value
            elif key.endswith(".locked"):
                name = key[:-7]
                (target["locks"].add if value == "1" else target["locks"].discard)(name)
            elif ".tag.tag" in key:
                tag_type = key.split("[")[0]
                if key.endswith("tag.tag-"):
                    removals = {unquote(v) for v in value.split(",")}
                    target[tag_type] = [t for t in target.get(tag_type, []) if t not in removals]
                else:
                    target.setdefault("_pending", {}).setdefault(tag_type, []).append(value)
        for tag_type, values in target.pop("_pending", {}).items():
            target[tag_type] = values

    def _metadata_route(self, method: str, rk: int, rest: list[str], q: dict[str, str]) -> tuple[int, ET.Element | None]:
        if rk in self.collections:
            return self._collection_route(method, [str(rk), *rest], q)
        if rk in self.playlists and not rest:
            root = self.container(size=1)
            root.append(self.playlist_element(self.playlists[rk]))
            return 200, root
        item = self.items.get(rk)
        if item is None:
            return 404, None
        if not rest and method == "GET":
            sec = self.sections[item["section"]]
            root = self.container(size=1, librarySectionID=sec["key"], librarySectionTitle=sec["title"])
            root.append(self.item_element(item))
            return 200, root
        if rest in (["children"], ["allLeaves"]):
            kids = [self.items[k] for k in item.get("children", [])] if rest == ["children"] else self.leaves(item)
            root = self.container(size=len(kids))
            for k in kids:
                root.append(self.item_element(k, full=False))
            return 200, root
        if rest == ["refresh"]:
            return 200, None
        if rest == ["posters"] and method == "POST":
            item.setdefault("posters", []).append(q.get("url", "upload://posters/file"))
            return 200, None
        if rest == ["posters"] and method == "GET":
            root = self.container()
            for n, url in enumerate(item.get("posters", [])):
                ET.SubElement(root, "Photo", {"key": url, "ratingKey": url, "provider": "local",
                                              "selected": "1" if n == len(item["posters"]) - 1 else "0", "thumb": url})
            return 200, root
        if rest == ["matches"] and method == "GET":
            root = self.container()
            ET.SubElement(root, "SearchResult", {"guid": "plex://movie/abc", "name": item["title"], "score": "100",
                                                 "year": str(item.get("year", ""))})
            ET.SubElement(root, "SearchResult", {"guid": "plex://movie/xyz", "name": "Other", "score": "60", "year": "2001"})
            return 200, root
        if rest == ["match"] and method == "PUT":
            item["guid"] = q["guid"]
            return 200, None
        return 404, None

    def _collection_route(self, method: str, rest: list[str], q: dict[str, str]) -> tuple[int, ET.Element | None]:
        if not rest and method == "POST":
            rk = next(self.ids)
            smart = q.get("smart") == "1"
            uri = unquote(q["uri"])
            coll = {"ratingKey": rk, "title": q["title"], "section": int(q["sectionId"]), "smart": smart,
                    "subtype": TYPE_NAMES[int(q["type"])], "mode": -1, "sort": 0, "locks": set(),
                    "addedAt": NOW, "items": [], "genres": []}
            if smart:
                coll["content"] = uri.split("com.plexapp.plugins.library", 1)[1]
                coll["genres"] = [v for k, v in parse_qs(urlparse(coll["content"]).query).items() if k == "genre" for v in v]
            else:
                coll["items"] = [int(k) for k in uri.rsplit("/", 1)[1].split(",")]
                for k in coll["items"]:
                    self.items[k]["collection"].append(q["title"])
            self.collections[rk] = coll
            root = self.container(size=1)
            root.append(self.collection_element(coll))
            return 200, root
        rk = int(rest[0])
        coll = self.collections.get(rk)
        if coll is None:
            return 404, None
        tail = rest[1:]
        if not tail and method == "GET":
            root = self.container(size=1)
            root.append(self.collection_element(coll, prefs="includePreferences" in q))
            return 200, root
        if not tail and method == "DELETE":
            del self.collections[rk]
            return 200, None
        if tail == ["children"]:
            items = self.collection_items(coll)
            root = self.container(size=len(items))
            for i in items:
                root.append(self.item_element(i, full=False))
            return 200, root
        if tail == ["items"] and method == "PUT":
            uri = unquote(q["uri"])
            if coll["smart"]:
                coll["content"] = uri.split("com.plexapp.plugins.library", 1)[1]
                coll["genres"] = [v for v in parse_qs(urlparse(coll["content"]).query).get("genre", [])]
            else:
                coll["items"].extend(int(k) for k in uri.rsplit("/", 1)[1].split(","))
            return 200, None
        if len(tail) == 2 and tail[0] == "items" and method == "DELETE":
            coll["items"].remove(int(tail[1]))
            return 200, None
        if len(tail) == 3 and tail[2] == "move":
            moving = int(tail[1])
            coll["items"].remove(moving)
            pos = coll["items"].index(int(q["after"])) + 1 if "after" in q else 0
            coll["items"].insert(pos, moving)
            return 200, None
        if tail == ["prefs"] and method == "PUT":
            if "collectionMode" in q:
                coll["mode"] = int(q["collectionMode"])
            if "collectionSort" in q:
                coll["sort"] = int(q["collectionSort"])
            return 200, None
        return 404, None

    def _playlist_route(self, method: str, rest: list[str], q: dict[str, str]) -> tuple[int, ET.Element | None]:
        if not rest and method == "GET":
            root = self.container(size=len(self.playlists))
            for pl in self.playlists.values():
                if "playlistType" not in q or q["playlistType"] == pl["playlistType"]:
                    root.append(self.playlist_element(pl))
            return 200, root
        if not rest and method == "POST":
            rk = next(self.ids)
            uri = unquote(q["uri"])
            pl = {"ratingKey": rk, "title": q["title"], "playlistType": q["type"], "smart": q.get("smart") == "1",
                  "addedAt": NOW, "items": []}
            if not pl["smart"]:
                for k in uri.rsplit("/", 1)[1].split(","):
                    self._playlist_add(pl, int(k))
            else:
                pl["content"] = uri.split("com.plexapp.plugins.library", 1)[1]
            self.playlists[rk] = pl
            root = self.container(size=1)
            root.append(self.playlist_element(pl))
            return 200, root
        rk = int(rest[0])
        pl = self.playlists.get(rk)
        if pl is None:
            return 404, None
        tail = rest[1:]
        if not tail and method == "GET":
            root = self.container(size=1)
            root.append(self.playlist_element(pl))
            return 200, root
        if not tail and method == "PUT":
            for key in ("title", "summary"):
                if f"{key}.value" in q:
                    pl[key] = q[f"{key}.value"]
            return 200, None
        if not tail and method == "DELETE":
            del self.playlists[rk]
            return 200, None
        if tail == ["items"] and method == "GET":
            root = self.container(size=len(pl["items"]))
            for pid, item_rk in pl["items"]:
                el = self.item_element(self.items[item_rk], full=False)
                el.set("playlistItemID", str(pid))
                root.append(el)
            return 200, root
        if tail == ["items"] and method == "PUT":
            uri = unquote(q["uri"])
            for k in uri.rsplit("/", 1)[1].split(","):
                self._playlist_add(pl, int(k))
            return 200, None
        if len(tail) == 2 and tail[0] == "items" and method == "DELETE":
            pl["items"] = [p for p in pl["items"] if p[0] != int(tail[1])]
            return 200, None
        if len(tail) == 3 and tail[2] == "move":
            entry = next(p for p in pl["items"] if p[0] == int(tail[1]))
            pl["items"].remove(entry)
            pos = next(i for i, p in enumerate(pl["items"]) if p[0] == int(q["after"])) + 1 if "after" in q else 0
            pl["items"].insert(pos, entry)
            return 200, None
        return 404, None

    def _playlist_add(self, pl: dict[str, Any], rk: int) -> None:
        item = self.items[rk]
        targets = self.leaves(item) if item.get("children") else [item]
        for t in targets:
            pl["items"].append((next(self.playlist_item_ids), t["ratingKey"]))


class FakePlexServer:
    """Runs FakePlexState behind a real HTTP server on 127.0.0.1 in a background thread."""

    def __init__(self) -> None:
        self.state = FakePlexState()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                if self.headers.get("X-Plex-Token") != "test-token":
                    self.send_response(401)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                status, element = state.handle(self.command, self.path, dict(self.headers.items()))
                body = ET.tostring(element, encoding="utf-8") if element is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "text/xml;charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_PUT = do_POST = do_DELETE = _serve

            def log_message(self, *args: Any) -> None:  # silence
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "FakePlexServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def snapshot(self) -> dict[str, Any]:
        with self.state.lock:
            return deepcopy({"items": self.state.items, "collections": self.state.collections,
                             "playlists": self.state.playlists})
