"""Live, read-only smoke tests against the real Paprika cloud API.

These tests are skipped by default and only run when the env var
``PAPRIKA_LIVE_TESTS=1`` is set. They use the credentials from ``.env``
(``PAPRIKA_USERNAME`` / ``PAPRIKA_PASSWORD``).

NON-DESTRUCTIVE: this file deliberately exercises only read endpoints
(``authenticate``, ``list_recipes``, ``get_groceries``, ``get_grocery_lists``).
No recipes, groceries, or lists are ever created, modified, or deleted.

Run with:
    PAPRIKA_LIVE_TESTS=1 python -m pytest tests/test_paprika_live.py -v
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from src.paprika_client import PaprikaClient

load_dotenv()

LIVE = os.environ.get("PAPRIKA_LIVE_TESTS") == "1"
USERNAME = os.environ.get("PAPRIKA_USERNAME")
PASSWORD = os.environ.get("PAPRIKA_PASSWORD")

pytestmark = [
    pytest.mark.skipif(
        not LIVE,
        reason="Set PAPRIKA_LIVE_TESTS=1 to run live (read-only) tests against Paprika.",
    ),
    pytest.mark.skipif(
        not (USERNAME and PASSWORD),
        reason="PAPRIKA_USERNAME and PAPRIKA_PASSWORD must be set for live tests.",
    ),
]


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def client():
    # One client (and one Paprika login) is shared across the whole live
    # module. Paprika rate-limits per IP, so re-authenticating in every test
    # is enough to trigger connection resets.
    c = PaprikaClient(USERNAME, PASSWORD)
    try:
        yield c
    finally:
        await c.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_live_authenticate(client):
    token = await client.authenticate()
    assert isinstance(token, str) and len(token) > 10


@pytest.mark.asyncio(loop_scope="module")
async def test_live_list_recipes_warmup(client):
    """First call performs the full warm-up via /sync/recipes + /sync/recipe/{uid}/."""
    recipes = await client.list_recipes(limit=5)
    assert isinstance(recipes, list)
    # If the account has any recipes at all, validate basic shape.
    if recipes:
        r = recipes[0]
        for required in ("uid", "name", "hash"):
            assert required in r, f"recipe missing field {required}: {list(r)[:10]}"


@pytest.mark.asyncio(loop_scope="module")
async def test_live_list_recipes_cache_hit(client):
    """Second call with no server-side change must not refetch any recipe bodies."""
    await client.list_recipes(limit=5)  # warm-up
    cached_count = len(client._recipe_cache)
    fp_before = client._recipe_index_fingerprint

    fetched = await client.refresh_recipe_cache()
    assert fetched == 0, "cache should not refetch unchanged recipes"
    assert client._recipe_index_fingerprint == fp_before
    assert len(client._recipe_cache) == cached_count


@pytest.mark.asyncio(loop_scope="module")
async def test_live_get_grocery_lists(client):
    lists = await client.get_grocery_lists()
    assert isinstance(lists, list)
    for lst in lists:
        assert "uid" in lst and "name" in lst


@pytest.mark.asyncio(loop_scope="module")
async def test_live_get_groceries(client):
    items = await client.get_groceries(include_purchased=False)
    assert isinstance(items, list)
    for item in items:
        assert "uid" in item and "name" in item
