"""Collection tools: create, update, reorder, delete."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from plex_mcp.plex import detail, summarize
from plex_mcp.tools._common import (
    DESTRUCTIVE,
    WRITE,
    WRITE_NON_IDEMPOTENT,
    Filters,
    Library,
    LibType,
    RatingKey,
    Sort,
    ToolDeps,
)

CollectionMode = Literal["default", "hide", "hideItems", "showItems"]
ItemOrder = Literal["release", "alpha", "custom"]


class SmartRules(BaseModel):
    """Rules for a smart collection or playlist. Items are chosen by Plex automatically."""

    libtype: LibType = None
    filters: Filters = None
    sort: Sort = None
    limit: Annotated[int | None, Field(ge=1, description="Maximum number of items.")] = None


class Visibility(BaseModel):
    """Where a collection is promoted. Omit a field to leave it unchanged."""

    library_recommended: bool | None = None
    home: bool | None = Field(default=None, description="Your own Home page.")
    friends_home: bool | None = Field(default=None, description="Home page of users you share with.")


def register(deps: ToolDeps) -> None:
    mcp, ctx, settings = deps.mcp, deps.ctx, deps.settings

    def _check_same_library(section: Any, items: list[Any]) -> None:
        wrong = [i for i in items if i.__dict__.get("librarySectionID") != int(section.key)]
        if wrong:
            names = ", ".join(f"{i.title} ({i.ratingKey}, library {i.librarySectionTitle})" for i in wrong)
            raise ValueError(f"Collection items must all be in library '{section.title}'. Not in it: {names}")

    @mcp.tool(title="Create collection", annotations=WRITE_NON_IDEMPOTENT)
    def create_collection(
        library: Library,
        title: Annotated[str, Field(min_length=1)],
        rating_keys: Annotated[
            list[int] | None,
            Field(description="Items for a regular collection. All must be the same type, in this library."),
        ] = None,
        smart: Annotated[SmartRules | None, Field(description="Rules for a smart collection (instead of rating_keys).")] = None,
        summary: str | None = None,
        sort_title: str | None = None,
        mode: Annotated[
            CollectionMode | None,
            Field(description="Library display: default, hide (hide collection), hideItems (show collection, hide its items), showItems (show both)."),
        ] = None,
        item_order: Annotated[ItemOrder | None, Field(description="Regular collections only: release date, alphabetical, or custom.")] = None,
    ) -> dict[str, Any]:
        """Create a regular collection from specific items, or a smart collection from filter rules.
        Fails if a collection with the same title already exists in the library (use update_collection)."""
        if bool(rating_keys) == bool(smart):
            raise ValueError("Provide either rating_keys (regular collection) or smart rules, not both.")
        section = ctx.section(library)
        existing = [c for c in section.search(title=title, libtype="collection") if c.title.casefold() == title.casefold()]
        if existing:
            raise ValueError(
                f"A collection named '{existing[0].title}' already exists in {section.title} "
                f"(rating_key {existing[0].ratingKey}). Use update_collection to change it."
            )

        if smart:
            collection = section.createCollection(
                title=title, smart=True, libtype=smart.libtype, filters=smart.filters or None,
                sort=smart.sort, limit=smart.limit,
            )
        else:
            items = ctx.items(rating_keys or [])
            _check_same_library(section, items)
            collection = section.createCollection(title=title, items=items)

        _apply_updates(collection, summary=summary, sort_title=sort_title, mode=mode, item_order=item_order)
        return {"status": "created", "collection": detail(ctx.item(collection.ratingKey))}

    @mcp.tool(title="Update collection", annotations=WRITE)
    def update_collection(
        rating_key: RatingKey,
        title: str | None = None,
        sort_title: str | None = None,
        summary: str | None = None,
        content_rating: str | None = None,
        add_rating_keys: Annotated[list[int] | None, Field(description="Regular collections: items to add.")] = None,
        remove_rating_keys: Annotated[list[int] | None, Field(description="Regular collections: items to remove.")] = None,
        smart: Annotated[SmartRules | None, Field(description="Smart collections: replace the filter rules.")] = None,
        mode: CollectionMode | None = None,
        item_order: ItemOrder | None = None,
        visibility: Visibility | None = None,
    ) -> dict[str, Any]:
        """Rename, re-describe, add/remove items, change smart rules, display mode, ordering,
        or Home/Recommended visibility of an existing collection."""
        collection = ctx.collection(rating_key)
        section = collection.section()

        if (add_rating_keys or remove_rating_keys) and collection.smart:
            raise ValueError("This is a smart collection; change its rules with 'smart' instead of adding/removing items.")
        if smart and not collection.smart:
            raise ValueError("This is a regular collection; it has no smart rules.")

        if add_rating_keys:
            items = ctx.items(add_rating_keys)
            _check_same_library(section, items)
            collection.addItems(items)
        if remove_rating_keys:
            collection.removeItems(ctx.items(remove_rating_keys))
        if smart:
            collection.updateFilters(libtype=smart.libtype, filters=smart.filters or None, sort=smart.sort, limit=smart.limit)

        _apply_updates(collection, title=title, summary=summary, sort_title=sort_title,
                       content_rating=content_rating, mode=mode, item_order=item_order)

        if visibility and any(v is not None for v in visibility.model_dump().values()):
            collection.visibility().updateVisibility(
                recommended=visibility.library_recommended, home=visibility.home, shared=visibility.friends_home
            )
        return {"status": "updated", "collection": detail(ctx.item(rating_key))}

    @mcp.tool(title="Reorder collection or playlist item", annotations=WRITE)
    def move_item(
        container_rating_key: Annotated[int, Field(ge=1, description="rating_key of the collection or playlist.")],
        item_rating_key: Annotated[int, Field(ge=1, description="The item to move.")],
        after_rating_key: Annotated[
            int | None, Field(ge=1, description="Place the item after this one. Omit to move it to the top.")
        ] = None,
    ) -> dict[str, Any]:
        """Move an item to a new position in a regular collection or playlist. For collections,
        the item order is switched to 'custom' automatically."""
        container = ctx.item(container_rating_key)
        if container.TYPE not in {"collection", "playlist"}:
            raise ValueError(f"rating_key {container_rating_key} is a {container.TYPE}, not a collection or playlist.")
        if container.smart:
            raise ValueError("Smart collections and playlists can't be reordered manually.")
        members = {i.ratingKey: i for i in container.items()}
        if item_rating_key not in members:
            raise ValueError(f"Item {item_rating_key} is not in '{container.title}'.")
        if after_rating_key is not None and after_rating_key not in members:
            raise ValueError(f"Item {after_rating_key} is not in '{container.title}'.")
        if container.TYPE == "collection" and container.collectionSort != 2:
            container.sortUpdate(sort="custom")
        container.moveItem(members[item_rating_key], after=members.get(after_rating_key) if after_rating_key else None)
        refreshed = ctx.item(container_rating_key)
        order = [i.ratingKey for i in refreshed.items()]
        return {"status": "moved", "container": summarize(refreshed), "position": order.index(item_rating_key) + 1}

    if settings.allow_delete:

        @mcp.tool(title="Delete collection", annotations=DESTRUCTIVE)
        def delete_collection(
            rating_key: RatingKey,
            confirm_title: Annotated[str, Field(description="The collection's exact title, as a safety check.")],
        ) -> dict[str, Any]:
            """Permanently delete a collection. The items in it are NOT deleted."""
            collection = ctx.collection(rating_key)
            if collection.title != confirm_title:
                raise ValueError(
                    f"confirm_title {confirm_title!r} does not match the collection's title {collection.title!r}."
                )
            info = summarize(collection)
            collection.delete()
            return {"status": "deleted", "collection": info}


def _apply_updates(
    collection: Any,
    *,
    title: str | None = None,
    summary: str | None = None,
    sort_title: str | None = None,
    content_rating: str | None = None,
    mode: str | None = None,
    item_order: str | None = None,
) -> None:
    edits = {"editTitle": title, "editSummary": summary, "editSortTitle": sort_title, "editContentRating": content_rating}
    edits = {k: v for k, v in edits.items() if v is not None}
    if edits:
        collection.batchEdits()
        for method, value in edits.items():
            getattr(collection, method)(value)
        collection.saveEdits()
    if mode is not None:
        collection.modeUpdate(mode=mode)
    if item_order is not None:
        if collection.smart:
            raise ValueError("Smart collections are ordered by their sort rule, not item_order.")
        collection.sortUpdate(sort=item_order)
