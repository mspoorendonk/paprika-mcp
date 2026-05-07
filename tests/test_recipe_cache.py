"""Tests for the hash-based recipe cache in PaprikaClient.list_recipes."""
from unittest.mock import AsyncMock, patch

import pytest

from src.paprika_client import PaprikaClient


def _index(*pairs):
    return [{"uid": uid, "hash": h} for uid, h in pairs]


def _recipe(uid, name, hash_, in_trash=False):
    return {"uid": uid, "name": name, "hash": hash_, "in_trash": in_trash}


@pytest.fixture
def client():
    return PaprikaClient("test@example.com", "testpass")


@pytest.mark.asyncio
async def test_list_recipes_warmup_fetches_all(client):
    async def fake_request(method, endpoint, **kwargs):
        if endpoint == "/sync/recipes":
            return {"result": _index(("a", "h1"), ("b", "h2"))}
        if endpoint == "/sync/recipe/a/":
            return {"result": _recipe("a", "Apple pie", "h1")}
        if endpoint == "/sync/recipe/b/":
            return {"result": _recipe("b", "Banana bread", "h2")}
        raise AssertionError(f"unexpected endpoint {endpoint}")

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request) as mock_req:
        recipes = await client.list_recipes()

    names = [r["name"] for r in recipes]
    assert names == ["Apple pie", "Banana bread"]  # sorted
    # 1 index call + 2 per-recipe calls
    assert mock_req.call_count == 3


@pytest.mark.asyncio
async def test_list_recipes_uses_cache_when_fingerprint_unchanged(client):
    calls = {"index": 0, "recipe": 0}

    async def fake_request(method, endpoint, **kwargs):
        if endpoint == "/sync/recipes":
            calls["index"] += 1
            return {"result": _index(("a", "h1"))}
        if endpoint.startswith("/sync/recipe/"):
            calls["recipe"] += 1
            return {"result": _recipe("a", "Apple pie", "h1")}
        raise AssertionError(endpoint)

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request):
        await client.list_recipes()
        await client.list_recipes()
        await client.list_recipes()

    assert calls["index"] == 3   # cheap call always made
    assert calls["recipe"] == 1  # full body fetched only once


@pytest.mark.asyncio
async def test_list_recipes_refetches_only_changed_recipe(client):
    state = {"hash_b": "h2"}

    async def fake_request(method, endpoint, **kwargs):
        if endpoint == "/sync/recipes":
            return {"result": _index(("a", "h1"), ("b", state["hash_b"]))}
        if endpoint == "/sync/recipe/a/":
            return {"result": _recipe("a", "Apple pie", "h1")}
        if endpoint == "/sync/recipe/b/":
            return {"result": _recipe("b", "Banana bread", state["hash_b"])}
        raise AssertionError(endpoint)

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request) as mock_req:
        await client.list_recipes()
        first_count = mock_req.call_count  # 1 index + 2 bodies = 3

        # Simulate that recipe b changed on the server.
        state["hash_b"] = "h2-new"
        await client.list_recipes()

    # Second call: 1 index + 1 body (only b refetched) = 2 more calls.
    assert mock_req.call_count == first_count + 2


@pytest.mark.asyncio
async def test_list_recipes_drops_deleted_recipes(client):
    indexes = [
        _index(("a", "h1"), ("b", "h2")),
        _index(("a", "h1")),  # b removed on server
    ]
    counter = {"i": 0}

    async def fake_request(method, endpoint, **kwargs):
        if endpoint == "/sync/recipes":
            idx = indexes[min(counter["i"], len(indexes) - 1)]
            counter["i"] += 1
            return {"result": idx}
        if endpoint == "/sync/recipe/a/":
            return {"result": _recipe("a", "Apple pie", "h1")}
        if endpoint == "/sync/recipe/b/":
            return {"result": _recipe("b", "Banana bread", "h2")}
        raise AssertionError(endpoint)

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request):
        first = await client.list_recipes()
        second = await client.list_recipes()

    assert {r["uid"] for r in first} == {"a", "b"}
    assert {r["uid"] for r in second} == {"a"}


@pytest.mark.asyncio
async def test_list_recipes_excludes_in_trash(client):
    async def fake_request(method, endpoint, **kwargs):
        if endpoint == "/sync/recipes":
            return {"result": _index(("a", "h1"), ("b", "h2"))}
        if endpoint == "/sync/recipe/a/":
            return {"result": _recipe("a", "Apple pie", "h1")}
        if endpoint == "/sync/recipe/b/":
            return {"result": _recipe("b", "Banana bread", "h2", in_trash=True)}
        raise AssertionError(endpoint)

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request):
        recipes = await client.list_recipes()

    assert [r["uid"] for r in recipes] == ["a"]


@pytest.mark.asyncio
async def test_list_recipes_limit(client):
    async def fake_request(method, endpoint, **kwargs):
        if endpoint == "/sync/recipes":
            return {"result": _index(("a", "h1"), ("b", "h2"), ("c", "h3"))}
        uid = endpoint.split("/")[3]
        return {"result": _recipe(uid, f"Name {uid}", f"h{uid}")}

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request):
        recipes = await client.list_recipes(limit=2)

    assert len(recipes) == 2


@pytest.mark.asyncio
async def test_warm_up_cache_swallows_errors(client):
    with patch.object(
        client,
        "_make_authenticated_request",
        new_callable=AsyncMock,
        side_effect=Exception("boom"),
    ):
        # Must not raise; cache_ready must be set so callers don't hang.
        await client.warm_up_cache()
        assert client._cache_ready.is_set()
