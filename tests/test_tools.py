"""End-to-end tool tests against the fake Plex server (real plexapi, real MCP client)."""

from __future__ import annotations

import pytest
from mcp import Client

from plex_mcp.server import build_server
from tests.conftest import ToolCaller, ToolFailed

pytestmark = pytest.mark.anyio


# ----------------------------------------------------------------------------- reading
async def test_server_info_and_libraries(call: ToolCaller) -> None:
    info = await call("server_info")
    assert info["plex_server"]["name"] == "Fake Plex"
    assert [lib["title"] for lib in info["libraries"]] == ["Movies", "TV Shows"]
    assert info["mcp_server"]["read_only"] is False

    libs = await call("list_libraries")
    movies = libs["libraries"][0]
    assert movies["item_count"] == 3
    assert movies["folders"] == ["D:\\Movies"]


async def test_search_library_by_title_and_filters(call: ToolCaller) -> None:
    by_title = await call("search_library", library="Movies", query="alien")
    assert {r["title"] for r in by_title["results"]} == {"Alien", "Aliens"}

    by_genre = await call("search_library", library="1", filters={"genre": "Crime"})
    assert [r["title"] for r in by_genre["results"]] == ["Heat"]

    newest = await call("search_library", library="Movies", sort="addedAt:desc", limit=1)
    assert newest["results"][0]["title"] == "Heat"


async def test_search_library_paging_reports_total(call: ToolCaller) -> None:
    first = await call("search_library", library="Movies", sort="addedAt:desc", limit=2)
    assert [r["title"] for r in first["results"]] == ["Heat", "Aliens"]
    assert (first["total"], first["returned"], first["has_more"], first["next_offset"]) == (3, 2, True, 2)

    rest = await call("search_library", library="Movies", sort="addedAt:desc", limit=2, offset=first["next_offset"])
    assert [r["title"] for r in rest["results"]] == ["Alien"]
    assert (rest["total"], rest["has_more"], rest["next_offset"]) == (3, False, None)


async def test_max_results_caps_page_but_paging_still_reaches_everything(make_settings) -> None:
    async with Client(build_server(make_settings(max_results=2))) as client:
        caller = ToolCaller(client)
        page = await caller("search_library", library="Movies", limit=500)
        assert page["limit"] == 2 and page["returned"] == 2
        assert page["total"] == 3 and page["has_more"] is True

        episodes = await caller("list_children", rating_key=201, leaves=True, limit=500)
        assert (episodes["total"], episodes["returned"], episodes["has_more"]) == (2, 2, False)


async def test_list_collections_paging(call: ToolCaller) -> None:
    for title, keys in (("A", [101]), ("B", [102]), ("C", [103])):
        await call("create_collection", library="Movies", title=title, rating_keys=keys)
    first = await call("list_collections", library="Movies", limit=2)
    assert len(first["collections"]) == 2
    assert (first["total"], first["has_more"], first["next_offset"]) == (3, True, 2)
    rest = await call("list_collections", library="Movies", limit=2, offset=2)
    assert [c["title"] for c in rest["collections"]] == ["C"]
    assert rest["has_more"] is False


async def test_search_library_unknown_library(call: ToolCaller) -> None:
    with pytest.raises(ToolFailed, match="No library named"):
        await call("search_library", library="Anime")


async def test_get_item_details(call: ToolCaller) -> None:
    item = await call("get_item", rating_key=101)
    assert item["title"] == "Alien"
    assert item["tags"]["genre"] == ["Horror", "Science Fiction"]
    assert item["media"][0]["files"] == ["D:\\Movies\\Alien (1979).mkv"]


async def test_list_children_and_leaves(call: ToolCaller) -> None:
    seasons = await call("list_children", rating_key=201)
    assert [c["title"] for c in seasons["children"]] == ["Season 1"]
    episodes = await call("list_children", rating_key=201, leaves=True)
    assert [c["title"] for c in episodes["children"]] == ["Pilot", "Diversity Day"]
    assert episodes["children"][0]["show_or_artist"] == "The Office"


async def test_filter_options(call: ToolCaller) -> None:
    opts = await call("list_filter_options", library="Movies", field="genre")
    assert {"key": "genre", "title": "Genre", "type": "string"} in opts["filters"]
    assert "Crime" in opts["choices"]["values"]


async def test_search_all(call: ToolCaller) -> None:
    found = await call("search_all", query="office")
    assert found["results"][0]["title"] == "The Office"


# ----------------------------------------------------------------------------- metadata edits
async def test_edit_metadata_batches_and_locks(call: ToolCaller, fake_plex) -> None:
    result = await call("edit_metadata", rating_key=103, title="Heat (1995)", summary="New summary", year=1996)
    assert result["updated_fields"] == ["summary", "title", "year"]
    assert result["item"]["title"] == "Heat (1995)"
    assert {"title", "summary", "year"} <= set(result["item"]["locked_fields"])
    edit_requests = [p for m, p in fake_plex.state.requests if m == "PUT" and "/library/sections/1/all" in p]
    assert len(edit_requests) == 1, "all field edits should be sent as one request"


async def test_edit_metadata_rejects_bad_date_and_unsupported_field(call: ToolCaller) -> None:
    with pytest.raises(ToolFailed, match="YYYY-MM-DD"):
        await call("edit_metadata", rating_key=101, originally_available_at="May 25 1979")
    with pytest.raises(ToolFailed, match="does not support editing: track_number"):
        await call("edit_metadata", rating_key=101, track_number=3)


async def test_edit_tags_add_and_remove_many(call: ToolCaller, fake_plex) -> None:
    result = await call("edit_tags", rating_keys=[101, 102], tag_type="genre", add=["Classic"], remove=["Science Fiction"])
    assert result["errors"] == []
    state = fake_plex.snapshot()["items"]
    assert state[101]["genre"] == ["Horror", "Classic"]
    assert state[102]["genre"] == ["Action", "Classic"]
    assert "genre" in state[101]["locks"]


async def test_set_field_locks(call: ToolCaller, fake_plex) -> None:
    await call("set_field_locks", rating_keys=[101], fields=["summary", "poster", "genre"], locked=True)
    assert {"summary", "thumb", "genre"} <= fake_plex.snapshot()["items"][101]["locks"]
    await call("set_field_locks", rating_keys=[101], fields=["poster"], locked=False)
    assert "thumb" not in fake_plex.snapshot()["items"][101]["locks"]


async def test_set_played_and_rating(call: ToolCaller, fake_plex) -> None:
    await call("set_played", rating_keys=[101], played=True)
    assert fake_plex.snapshot()["items"][101]["viewCount"] == 1
    await call("edit_metadata", rating_key=101, user_rating=8)
    assert float(fake_plex.snapshot()["items"][101]["userRating"]) == 8


async def test_set_artwork(call: ToolCaller, fake_plex) -> None:
    with pytest.raises(ToolFailed, match="exactly one"):
        await call("set_artwork", rating_key=101)
    with pytest.raises(ToolFailed, match="file_path must be one of"):
        await call("set_artwork", rating_key=101, file_path="C:\\Windows\\win.ini")
    await call("set_artwork", rating_key=101, url="https://example.com/alien.jpg")
    state = fake_plex.snapshot()["items"][101]
    assert state["posters"] == ["https://example.com/alien.jpg"]
    assert "thumb" in state["locks"]
    options = await call("list_artwork", rating_key=101)
    assert options["options"][0]["selected"] is True


async def test_find_and_fix_match(call: ToolCaller, fake_plex) -> None:
    found = await call("find_matches", rating_key=102, title="Aliens", year=1986)
    assert [m["guid"] for m in found["matches"]] == ["plex://movie/abc", "plex://movie/xyz"]
    await call("fix_match", rating_key=102, guid="plex://movie/xyz")
    assert fake_plex.snapshot()["items"][102]["guid"] == "plex://movie/xyz"


# ----------------------------------------------------------------------------- collections
async def test_collection_lifecycle(call: ToolCaller, fake_plex) -> None:
    created = await call(
        "create_collection", library="Movies", title="Alien Saga", rating_keys=[101, 102],
        summary="Xenomorphs", mode="showItems",
    )
    coll = created["collection"]
    assert coll["item_count"] == 2
    assert coll["collection_mode"] == "showItems"
    rk = coll["rating_key"]

    with pytest.raises(ToolFailed, match="already exists"):
        await call("create_collection", library="Movies", title="alien saga", rating_keys=[103])

    listed = await call("list_collections", library="Movies")
    assert [c["title"] for c in listed["collections"]] == ["Alien Saga"]

    updated = await call("update_collection", rating_key=rk, add_rating_keys=[103], title="Sigourney")
    assert updated["collection"]["title"] == "Sigourney"
    assert updated["collection"]["item_count"] == 3

    moved = await call("move_item", container_rating_key=rk, item_rating_key=103)
    assert moved["position"] == 1
    assert fake_plex.snapshot()["collections"][rk]["sort"] == 2  # switched to custom order

    with pytest.raises(ToolFailed, match="does not match"):
        await call("delete_collection", rating_key=rk, confirm_title="wrong")
    await call("delete_collection", rating_key=rk, confirm_title="Sigourney")
    assert rk not in fake_plex.snapshot()["collections"]


async def test_collection_rejects_items_from_other_library(call: ToolCaller) -> None:
    with pytest.raises(ToolFailed, match="must all be in library 'Movies'"):
        await call("create_collection", library="Movies", title="Mixed", rating_keys=[101, 203])


async def test_smart_collection(call: ToolCaller, fake_plex) -> None:
    created = await call(
        "create_collection", library="Movies", title="Sci-Fi",
        smart={"filters": {"genre": "Science Fiction"}, "sort": "year:desc"},
    )
    coll = created["collection"]
    assert coll["smart"] is True
    assert coll["item_count"] == 2
    with pytest.raises(ToolFailed, match="smart collection"):
        await call("update_collection", rating_key=coll["rating_key"], add_rating_keys=[103])


# ----------------------------------------------------------------------------- playlists
async def test_playlist_lifecycle(call: ToolCaller, fake_plex) -> None:
    created = await call("create_playlist", title="Office Binge", rating_keys=[201], summary="All of it")
    pl = created["playlist"]
    assert pl["item_count"] == 2  # show expanded into its episodes
    rk = pl["rating_key"]

    listed = await call("list_playlists", query="binge")
    assert [p["rating_key"] for p in listed["playlists"]] == [rk]

    await call("update_playlist", rating_key=rk, add_rating_keys=[101], title="Friday Night")
    children = await call("list_children", rating_key=rk)
    assert [c["title"] for c in children["children"]] == ["Pilot", "Diversity Day", "Alien"]

    await call("move_item", container_rating_key=rk, item_rating_key=101, after_rating_key=203)
    children = await call("list_children", rating_key=rk)
    assert [c["title"] for c in children["children"]] == ["Pilot", "Alien", "Diversity Day"]

    await call("update_playlist", rating_key=rk, remove_rating_keys=[204])
    assert len(fake_plex.snapshot()["playlists"][rk]["items"]) == 2

    await call("delete_playlist", rating_key=rk, confirm_title="Friday Night")
    assert rk not in fake_plex.snapshot()["playlists"]


# ----------------------------------------------------------------------------- safety settings
async def test_read_only_mode_hides_write_tools(make_settings) -> None:
    async with Client(build_server(make_settings(read_only=True))) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "get_item" in names
    assert not names & {"edit_metadata", "create_collection", "delete_playlist"}


async def test_allow_delete_false_hides_delete_tools(make_settings) -> None:
    async with Client(build_server(make_settings(allow_delete=False))) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "create_collection" in names
    assert not names & {"delete_collection", "delete_playlist"}


async def test_library_allowlist(make_settings) -> None:
    async with Client(build_server(make_settings(allowed_libraries=["TV Shows"]))) as client:
        caller = ToolCaller(client)
        info = await caller("server_info")
        assert [lib["title"] for lib in info["libraries"]] == ["TV Shows"]
        with pytest.raises(ToolFailed, match="not in PLEX_MCP_ALLOWED_LIBRARIES"):
            await caller("search_library", library="Movies")
        with pytest.raises(ToolFailed, match="not allowed"):
            await caller("edit_metadata", rating_key=101, title="nope")
        ok = await caller("edit_metadata", rating_key=203, title="Pilot (Extended)")
        assert ok["item"]["title"] == "Pilot (Extended)"


async def test_tool_annotations(make_settings) -> None:
    async with Client(build_server(make_settings())) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["get_item"].annotations.read_only_hint is True
    assert tools["delete_collection"].annotations.destructive_hint is True
    assert tools["edit_metadata"].annotations.read_only_hint is False
