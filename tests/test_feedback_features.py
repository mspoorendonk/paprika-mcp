"""
Unit tests for features implemented from client feedback:
- Immediate cache updates on create_recipe / update_recipe
- Safe & strict recipe resolution (no accidental fuzzy scheduling of wrong recipes)
- Entry IDs in get_plan output
- delete_planned_meal and move_planned_meal tools
- Automatic grocery aisle categorization (never uncategorized)
- update_grocery_item and update_grocery_items (bulk) tools
"""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.paprika_client import (
    AmbiguousMatchError,
    InvalidArgumentError,
    PaprikaClient,
    RecipeNotFoundError,
)
import src.server as server


@pytest.fixture
def client():
    c = PaprikaClient("test@example.com", "pass")
    c.token = "fake-token"
    return c


# ---------------------------------------------------------------------------
# 1. Recipe cache invalidation & immediate update on create_recipe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_recipe_populates_cache_immediately(client):
    async def fake_request(method, endpoint, **kwargs):
        if method == "POST" and endpoint.startswith("/sync/recipe/"):
            return {"result": True}
        raise AssertionError(f"Unexpected: {method} {endpoint}")

    with patch.object(client, "_make_authenticated_request", side_effect=fake_request):
        created = await client.create_recipe(
            name="Restjes",
            ingredients="Restjes pasta\nKaas",
            directions="Opwarmen in pan",
        )

    # Immediately in cache
    assert created["uid"] in client._recipe_cache
    assert client._recipe_cache[created["uid"]]["name"] == "Restjes"
    assert client._cache_ready.is_set()
    assert client._recipe_index_fingerprint == ""


# ---------------------------------------------------------------------------
# 2. Strict matching in plan_meals (prevents "Restjes" -> "Wentelteefjes")
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_meals_rejects_weak_fuzzy_match(client):
    """'Restjes' must NOT match 'Wentelteefjes' when Restjes is missing."""
    client._recipe_cache = {
        "REC-WENTEL": {"uid": "REC-WENTEL", "name": "Wentelteefjes", "in_trash": False},
        "REC-LASAGNA": {"uid": "REC-LASAGNA", "name": "Lasagna Bolognese", "in_trash": False},
    }
    client._cache_ready.set()
    client.refresh_recipe_cache = AsyncMock(return_value=0)

    # Attempting to plan "Restjes" should raise RecipeNotFoundError, not schedule Wentelteefjes
    with pytest.raises(RecipeNotFoundError) as exc_info:
        await client.plan_meals([
            {"recipe_name_or_id": "Restjes", "date": "2026-10-22", "meal_type": "dinner"}
        ])
    assert "I can't find a recipe called 'Restjes'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_plan_meals_finds_exact_match_or_uid(client):
    client._recipe_cache = {
        "REC-RESTJES": {"uid": "REC-RESTJES", "name": "Restjes", "in_trash": False},
    }
    client._cache_ready.set()
    client.refresh_recipe_cache = AsyncMock(return_value=0)
    post_calls = []

    async def fake_request(method, endpoint, **kwargs):
        if method == "POST" and endpoint == "/sync/meals":
            post_calls.append((method, endpoint, kwargs))
            return {"result": True}
        raise AssertionError(endpoint)

    client._make_authenticated_request = fake_request

    # Plan by name
    res1 = await client.plan_meals([
        {"recipe_name_or_id": "restjes", "date": "2026-10-22", "meal_type": "dinner"}
    ])
    assert len(res1) == 1
    assert res1[0]["recipe_name"] == "Restjes"

    # Plan by UID
    res2 = await client.plan_meals([
        {"recipe_name_or_id": "REC-RESTJES", "date": "2026-10-23", "meal_type": "lunch"}
    ])
    assert len(res2) == 1
    assert res2[0]["recipe_name"] == "Restjes"


# ---------------------------------------------------------------------------
# 3. Entry IDs in get_plan output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_plan_server_includes_entry_ids(client):
    mock_meals = [
        {
            "uid": "MEAL-UID-12345",
            "recipe_uid": "REC-1",
            "name": "Kip Kerrie",
            # server.get_plan uses date.today(), so keep this inside the window.
            "date": date.today().strftime("%Y-%m-%d 00:00:00"),
            "type": 2,
            "deleted": False,
            "order_flag": 0,
        }
    ]
    client._make_authenticated_request = AsyncMock(return_value={"result": mock_meals})

    with patch("src.server._client_or_raise", return_value=client):
        output = await server.get_plan(days_back=1, days_ahead=1)
        assert "[id: MEAL-UID-12345]" in output
        assert "Kip Kerrie" in output


# ---------------------------------------------------------------------------
# 4. delete_planned_meal and move_planned_meal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_planned_meal_by_entry_id(client):
    meals = [
        {"uid": "MEAL-1", "recipe_uid": "R1", "name": "Pasta", "date": "2026-10-22 00:00:00", "type": 2, "deleted": False},
        {"uid": "MEAL-2", "recipe_uid": "R2", "name": "Salade", "date": "2026-10-23 00:00:00", "type": 1, "deleted": False},
    ]
    post_calls = []

    async def fake_request(method, endpoint, **kwargs):
        if method == "GET" and endpoint == "/sync/meals":
            return {"result": meals}
        if method == "POST" and endpoint == "/sync/meals":
            post_calls.append((method, endpoint, kwargs))
            return {"result": True}
        raise AssertionError(f"Unexpected: {method} {endpoint}")

    client._make_authenticated_request = fake_request

    result = await client.delete_planned_meal(entry_id="MEAL-1")
    assert result["uid"] == "MEAL-1"
    assert result["name"] == "Pasta"
    assert len(post_calls) == 1


@pytest.mark.asyncio
async def test_delete_planned_meal_by_date_and_type(client):
    meals = [
        {"uid": "MEAL-1", "recipe_uid": "R1", "name": "Pasta", "date": "2026-10-22 00:00:00", "type": 2, "deleted": False},
        {"uid": "MEAL-2", "recipe_uid": "R2", "name": "Salade", "date": "2026-10-22 00:00:00", "type": 1, "deleted": False},
    ]

    async def fake_request(method, endpoint, **kwargs):
        if method == "GET" and endpoint == "/sync/meals":
            return {"result": meals}
        if method == "POST" and endpoint == "/sync/meals":
            return {"result": True}
        raise AssertionError(endpoint)

    client._make_authenticated_request = fake_request

    # Multiple on same date without slot -> ambiguous
    with pytest.raises(AmbiguousMatchError):
        await client.delete_planned_meal(date="2026-10-22")

    # With slot specified -> exact match
    res = await client.delete_planned_meal(date="2026-10-22", meal_type="dinner")
    assert res["uid"] == "MEAL-1"
    assert res["name"] == "Pasta"


@pytest.mark.asyncio
async def test_move_planned_meal(client):
    meals = [
        {"uid": "MEAL-1", "recipe_uid": "R1", "name": "Pasta", "date": "2026-10-22 00:00:00", "type": 2, "deleted": False},
    ]

    async def fake_request(method, endpoint, **kwargs):
        if method == "GET" and endpoint == "/sync/meals":
            return {"result": meals}
        if method == "POST" and endpoint == "/sync/meals":
            return {"result": True}
        raise AssertionError(endpoint)

    client._make_authenticated_request = fake_request

    res = await client.move_planned_meal(entry_id="MEAL-1", to_date="2026-10-25", to_meal_type="lunch")
    assert res["uid"] == "MEAL-1"
    assert res["date"] == "2026-10-25"
    assert res["meal_type"] == "lunch"


# ---------------------------------------------------------------------------
# 5. Grocery auto-categorization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_categorize_grocery_item_keywords(client):
    client._aisle_cache = {}

    assert await client.categorize_grocery_item("bananen") == "Produce"
    assert await client.categorize_grocery_item("kipfilet") == "Meat"
    assert await client.categorize_grocery_item("halfvolle melk") == "Dairy"
    assert await client.categorize_grocery_item("stokbrood") == "Breads and Cereals"
    assert await client.categorize_grocery_item("spaghetti") == "Pasta, Rice and Beans"
    assert await client.categorize_grocery_item("olijfolie") == "Oils and Dressings"
    assert await client.categorize_grocery_item("chocolade") == "Snacks"
    assert await client.categorize_grocery_item("toiletpapier") == "Schoonmaak"
    # Unknown item defaults to Miscellaneous, never empty
    assert await client.categorize_grocery_item("onbekenddingxyz") == "Miscellaneous"


@pytest.mark.asyncio
async def test_add_grocery_item_assigns_aisle_when_empty(client):
    client._aisle_cache = {}
    posted = []

    async def fake_request(method, endpoint, **kwargs):
        if method == "GET" and "grocerylists" in endpoint:
            return {"result": [{"uid": "L1", "name": "Default", "is_default": True}]}
        if method == "POST" and "groceries" in endpoint:
            posted.append(kwargs)
            return {"result": True}
        raise AssertionError(endpoint)

    client._make_authenticated_request = fake_request

    item = await client.add_grocery_item(name="wortels", ingredient="wortels", aisle="")
    assert item["aisle"] == "Produce"


# ---------------------------------------------------------------------------
# 6. update_grocery_item and update_grocery_items
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_grocery_item(client):
    groceries = [
        {"uid": "G1", "name": "Appels", "ingredient": "Appels", "aisle": "", "quantity": "1kg", "list_uid": "L1", "purchased": False},
    ]

    async def fake_request(method, endpoint, **kwargs):
        if method == "GET" and "groceries" in endpoint:
            return {"result": groceries}
        if method == "GET" and "grocerylists" in endpoint:
            return {"result": [{"uid": "L1", "name": "Default", "is_default": True}]}
        if method == "POST" and "groceries" in endpoint:
            return {"result": True}
        raise AssertionError(endpoint)

    client._make_authenticated_request = fake_request

    updated = await client.update_grocery_item(item_name_or_id="G1", aisle="Produce", quantity="2kg")
    assert updated["aisle"] == "Produce"
    assert updated["quantity"] == "2kg"
    assert updated["name"] == "Appels"


@pytest.mark.asyncio
async def test_update_grocery_items_bulk(client):
    groceries = [
        {"uid": "G1", "name": "Appels", "ingredient": "Appels", "aisle": "", "list_uid": "L1", "purchased": False},
        {"uid": "G2", "name": "Biefstuk", "ingredient": "Biefstuk", "aisle": "", "list_uid": "L1", "purchased": False},
    ]

    async def fake_request(method, endpoint, **kwargs):
        if method == "GET" and "groceries" in endpoint:
            return {"result": groceries}
        if method == "GET" and "grocerylists" in endpoint:
            return {"result": [{"uid": "L1", "name": "Default", "is_default": True}]}
        if method == "POST" and "groceries" in endpoint:
            return {"result": True}
        raise AssertionError(endpoint)

    client._make_authenticated_request = fake_request

    results = await client.update_grocery_items([
        {"item_name_or_id": "G1", "aisle": "Produce"},
        {"item_name_or_id": "G2", "aisle": "Meat"},
    ])
    assert len(results) == 2
    assert results[0]["aisle"] == "Produce"
    assert results[1]["aisle"] == "Meat"
