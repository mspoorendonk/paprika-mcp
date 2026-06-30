"""
tests/test_add_recipe_to_grocery_list.py

Unit tests for PaprikaClient.add_recipe_to_grocery_list.

Covers:
  - Happy path: all ingredients added in a single bulk POST
  - recipe_uid is set on every created item
  - RecipeNotFoundError when name doesn't match
  - InvalidArgumentError when recipe has no ingredients
  - list resolution (default + named)
  - Blank/whitespace lines in the ingredients field are skipped
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.paprika_client import (
    InvalidArgumentError,
    PaprikaClient,
    RecipeNotFoundError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RECIPE_UID = "07975578-DE1A-42AB-B184-6E8FCB9AB753"
LIST_UID = "9E12FCF5-4A89-FC52-EA8E-1C5DA1BDA62A"

RECIPE = {
    "uid": RECIPE_UID,
    "name": "Thai basil chicken",
    "ingredients": "500g chicken thigh\n2 tbsp fish sauce\nThai basil\n\nbird's eye chilli",
    "in_trash": False,
}

RECIPE_NO_INGREDIENTS = {
    "uid": "AABBCCDD-0000-0000-0000-000000000001",
    "name": "Empty recipe",
    "ingredients": "",
    "in_trash": False,
}

DEFAULT_LIST = {"uid": LIST_UID, "name": "My Grocery List", "is_default": True}


@pytest.fixture()
def client():
    """A PaprikaClient with a pre-warmed cache and a mocked HTTP layer."""
    c = PaprikaClient(username="test@example.com", password="secret")
    c._recipe_cache = {
        RECIPE_UID: RECIPE,
        RECIPE_NO_INGREDIENTS["uid"]: RECIPE_NO_INGREDIENTS,
    }
    c._cache_ready.set()
    return c


def _patch_http(client: PaprikaClient, lists=None):
    """Patch _make_authenticated_request to return grocery lists and accept POSTs."""
    if lists is None:
        lists = [DEFAULT_LIST]

    async def fake_request(method, path, **kwargs):
        if method == "GET" and "grocerylists" in path:
            return {"result": lists}
        if method == "POST" and "groceries" in path:
            return {"result": True}
        raise AssertionError(f"Unexpected request: {method} {path}")

    client._make_authenticated_request = fake_request
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_returns_correct_count(client):
    """All non-empty ingredient lines are added and the count is returned."""
    _patch_http(client)
    result = await client.add_recipe_to_grocery_list("Thai basil chicken")

    assert result["recipe_name"] == "Thai basil chicken"
    assert result["recipe_uid"] == RECIPE_UID
    # 4 non-empty lines (blank line between chilli and basil is skipped)
    assert result["item_count"] == 4
    assert len(result["items"]) == 4


@pytest.mark.asyncio
async def test_recipe_uid_set_on_all_items(client):
    """Every grocery item must carry recipe_uid for Paprika grouping."""
    _patch_http(client)
    result = await client.add_recipe_to_grocery_list(RECIPE_UID)  # resolve by UID

    for item in result["items"]:
        assert item["recipe_uid"] == RECIPE_UID


@pytest.mark.asyncio
async def test_separate_false_on_all_items(client):
    """separate must be False (matches official app behaviour)."""
    _patch_http(client)
    result = await client.add_recipe_to_grocery_list("Thai basil chicken")
    for item in result["items"]:
        assert item["separate"] is False


@pytest.mark.asyncio
async def test_aisle_empty_on_all_items(client):
    """aisle must be empty string so Paprika can auto-assign."""
    _patch_http(client)
    result = await client.add_recipe_to_grocery_list("Thai basil chicken")
    for item in result["items"]:
        assert item["aisle"] == ""


@pytest.mark.asyncio
async def test_single_bulk_post(client):
    """All items must be sent in ONE POST, not one per item."""
    post_calls = []

    async def fake_request(method, path, **kwargs):
        if method == "GET" and "grocerylists" in path:
            return {"result": [DEFAULT_LIST]}
        if method == "POST":
            post_calls.append((method, path, kwargs))
            return {"result": True}
        raise AssertionError(f"Unexpected: {method} {path}")

    client._make_authenticated_request = fake_request
    await client.add_recipe_to_grocery_list("Thai basil chicken")

    assert len(post_calls) == 1, (
        f"Expected exactly 1 POST, got {len(post_calls)}"
    )


@pytest.mark.asyncio
async def test_recipe_not_found_raises(client):
    """Unknown recipe name raises RecipeNotFoundError."""
    _patch_http(client)
    with pytest.raises(RecipeNotFoundError):
        # Deliberately unpronounceable to defeat fuzzy matching
        await client.add_recipe_to_grocery_list("zzzxxx_no_such_recipe_qqqwww")


@pytest.mark.asyncio
async def test_empty_ingredients_raises(client):
    """Recipe with no ingredients raises InvalidArgumentError."""
    _patch_http(client)
    with pytest.raises(InvalidArgumentError):
        await client.add_recipe_to_grocery_list("Empty recipe")


@pytest.mark.asyncio
async def test_named_list_resolved(client):
    """list_name_or_id is passed through to list resolution."""
    named_list = {"uid": "NAMED-LIST-UID", "name": "Costco run", "is_default": False}
    _patch_http(client, lists=[DEFAULT_LIST, named_list])

    result = await client.add_recipe_to_grocery_list(
        "Thai basil chicken", list_name_or_id="Costco run"
    )
    assert result["list_uid"] == "NAMED-LIST-UID"


@pytest.mark.asyncio
async def test_blank_lines_skipped(client):
    """Blank / whitespace-only lines in ingredients are not added."""
    _patch_http(client)
    result = await client.add_recipe_to_grocery_list("Thai basil chicken")
    names = [item["name"] for item in result["items"]]
    assert "" not in names
    assert all(n.strip() for n in names)


@pytest.mark.asyncio
async def test_items_have_unique_uids(client):
    """Every grocery item must have a unique UID."""
    _patch_http(client)
    result = await client.add_recipe_to_grocery_list("Thai basil chicken")
    uids = [item["uid"] for item in result["items"]]
    assert len(uids) == len(set(uids))
