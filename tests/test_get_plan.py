"""Unit tests for the get_plan (meal planner history and calendar) tool and client method."""
from datetime import date
import pytest
from unittest.mock import AsyncMock, patch

from src.paprika_client import PaprikaClient, InvalidArgumentError
import src.server as server


MOCK_MEALS = [
    # 40 days ago (outside default 30 days)
    {
        "uid": "MEAL-OLD",
        "recipe_uid": "REC-1",
        "name": "Old Lasagna",
        "date": "2026-08-24 00:00:00",
        "type": 2,
        "deleted": False,
        "order_flag": 0,
    },
    # 10 days ago (in past window)
    {
        "uid": "MEAL-PAST",
        "recipe_uid": "REC-2",
        "name": "Kip Kerrie",
        "date": "2026-09-23 00:00:00",
        "type": 2,
        "deleted": False,
        "order_flag": 0,
    },
    # Today
    {
        "uid": "MEAL-TODAY-DINNER",
        "recipe_uid": "REC-3",
        "name": "Spaghetti Carbonara",
        "date": "2026-10-03 00:00:00",
        "type": 2,
        "deleted": False,
        "order_flag": 0,
    },
    {
        "uid": "MEAL-TODAY-LUNCH",
        "recipe_uid": "REC-4",
        "name": "Tosti Ham Kaas",
        "date": "2026-10-03 00:00:00",
        "type": 1,
        "deleted": False,
        "order_flag": 0,
    },
    # 5 days in future (in upcoming window)
    {
        "uid": "MEAL-FUTURE",
        "recipe_uid": "REC-5",
        "name": "Courgettesoep",
        "date": "2026-10-08 00:00:00",
        "type": 2,
        "deleted": False,
        "order_flag": 0,
    },
    # 20 days in future (outside default 14 days)
    {
        "uid": "MEAL-FAR-FUTURE",
        "recipe_uid": "REC-6",
        "name": "Sushi Bowl",
        "date": "2026-10-23 00:00:00",
        "type": 2,
        "deleted": False,
        "order_flag": 0,
    },
    # Deleted item (should be skipped)
    {
        "uid": "MEAL-DELETED",
        "recipe_uid": "REC-7",
        "name": "Cancelled Dinner",
        "date": "2026-10-03 00:00:00",
        "type": 2,
        "deleted": True,
        "order_flag": 0,
    },
]


@pytest.fixture
def client():
    c = PaprikaClient("test@example.com", "pass")
    c.token = "fake-token"
    return c


@pytest.mark.asyncio
async def test_get_plan_default_range(client):
    """Default range (30 days back, 14 days ahead) includes the expected meals and excludes out-of-range ones."""
    client._make_authenticated_request = AsyncMock(return_value={"result": MOCK_MEALS})
    ref_date = date(2026, 10, 3)

    meals = await client.get_plan(days_back=30, days_ahead=14, reference_date=ref_date)
    names = [m["name"] for m in meals]

    # In range: Kip Kerrie (-10d), Tosti (today lunch), Spaghetti (today dinner), Courgettesoep (+5d)
    assert names == ["Kip Kerrie", "Tosti Ham Kaas", "Spaghetti Carbonara", "Courgettesoep"]
    # Excluded: Old Lasagna (-40d), Sushi Bowl (+20d), Cancelled Dinner (deleted)
    assert "Old Lasagna" not in names
    assert "Sushi Bowl" not in names
    assert "Cancelled Dinner" not in names

    # Meal type strings mapped correctly
    assert meals[1]["meal_type"] == "lunch"
    assert meals[2]["meal_type"] == "dinner"


@pytest.mark.asyncio
async def test_get_plan_past_only(client):
    """days_ahead=0 only returns meals up to reference_date."""
    client._make_authenticated_request = AsyncMock(return_value={"result": MOCK_MEALS})
    ref_date = date(2026, 10, 3)

    meals = await client.get_plan(days_back=30, days_ahead=0, reference_date=ref_date)
    names = [m["name"] for m in meals]

    assert names == ["Kip Kerrie", "Tosti Ham Kaas", "Spaghetti Carbonara"]
    assert "Courgettesoep" not in names


@pytest.mark.asyncio
async def test_get_plan_future_only(client):
    """days_back=0 only returns meals from reference_date forward."""
    client._make_authenticated_request = AsyncMock(return_value={"result": MOCK_MEALS})
    ref_date = date(2026, 10, 3)

    meals = await client.get_plan(days_back=0, days_ahead=14, reference_date=ref_date)
    names = [m["name"] for m in meals]

    assert names == ["Tosti Ham Kaas", "Spaghetti Carbonara", "Courgettesoep"]
    assert "Kip Kerrie" not in names


@pytest.mark.asyncio
async def test_get_plan_negative_days_raises(client):
    """Negative days_back or days_ahead raises InvalidArgumentError."""
    with pytest.raises(InvalidArgumentError):
        await client.get_plan(days_back=-1)

    with pytest.raises(InvalidArgumentError):
        await client.get_plan(days_ahead=-1)


@pytest.mark.asyncio
async def test_get_plan_server_tool_formatting(client):
    """server.get_plan formats plain text with past and upcoming sections."""
    client._make_authenticated_request = AsyncMock(return_value={"result": MOCK_MEALS})
    
    with patch("src.server._client_or_raise", return_value=client):
        # Call tool directly
        output = await server.get_plan(days_back=30, days_ahead=14)
        assert "Meal planner entries" in output
        assert "Kip Kerrie" in output
        assert "Spaghetti Carbonara" in output
        assert "Courgettesoep" in output
