"""Tests for the typed-error contract documented in specs.md Scenario 9.

These tests pin down each PaprikaAPIError subclass to its stable code
string and verify that the resolver attaches structured candidate /
available-list data without leaking UIDs into the user-visible message.
"""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from src.paprika_client import (
    AmbiguousMatchError,
    GroceryListNotFoundError,
    GroceryNotFoundError,
    InvalidArgumentError,
    PaprikaAPIError,
    PaprikaAuthError,
    PaprikaClient,
    PaprikaRateLimitedError,
    PaprikaUnreachableError,
    RecipeNotFoundError,
)


class TestErrorCodes:
    """Each typed error must carry the code from the specs.md contract."""

    def test_paprika_api_error_default_code(self):
        assert PaprikaAPIError("boom").code == "paprika_error"

    def test_unreachable_code(self):
        assert PaprikaUnreachableError("x").code == "paprika_unreachable"

    def test_auth_code(self):
        assert PaprikaAuthError("x").code == "paprika_auth_failed"

    def test_rate_limited_code(self):
        assert PaprikaRateLimitedError("x").code == "paprika_rate_limited"

    def test_invalid_argument_code(self):
        assert InvalidArgumentError("x").code == "invalid_argument"

    def test_grocery_not_found_code(self):
        assert GroceryNotFoundError("x").code == "grocery_not_found"

    def test_grocery_list_not_found_code(self):
        assert GroceryListNotFoundError("x").code == "grocery_list_not_found"

    def test_recipe_not_found_code(self):
        assert RecipeNotFoundError("x").code == "recipe_not_found"

    def test_ambiguous_code(self):
        err = AmbiguousMatchError("x", candidates=[])
        assert err.code == "grocery_ambiguous"


class TestExtraStructuredContent:
    def test_ambiguous_carries_candidates_in_extra(self):
        cand = [{"uid": "1", "name": "a"}]
        err = AmbiguousMatchError("x", candidates=cand)
        assert err.extra["candidates"] == cand

    def test_grocery_list_not_found_carries_available(self):
        err = GroceryListNotFoundError("x", available_lists=["A", "B"])
        assert err.extra["available_lists"] == ["A", "B"]


class TestHttpStatusMapping:
    """_parse_response must map status codes to the right typed error."""

    @pytest.fixture
    def client(self):
        return PaprikaClient("test@example.com", "testpass")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_status_maps_to_auth_error(self, client, status):
        response = AsyncMock()
        response.status = status
        response.text = AsyncMock(return_value="<html>nope</html>")
        with pytest.raises(PaprikaAuthError):
            await client._parse_response(response, "/sync/groceries")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 503])
    async def test_rate_limit_status_maps_to_rate_limited(self, client, status):
        response = AsyncMock()
        response.status = status
        response.text = AsyncMock(return_value="slow down")
        with pytest.raises(PaprikaRateLimitedError):
            await client._parse_response(response, "/sync/groceries")

    @pytest.mark.asyncio
    async def test_other_5xx_maps_to_generic(self, client):
        response = AsyncMock()
        response.status = 500
        response.text = AsyncMock(return_value="boom")
        with pytest.raises(PaprikaAPIError) as exc_info:
            await client._parse_response(response, "/sync/groceries")
        # Generic, not the auth/rate-limited subclasses.
        assert exc_info.value.code == "paprika_error"
        assert not isinstance(exc_info.value, PaprikaAuthError)
        assert not isinstance(exc_info.value, PaprikaRateLimitedError)
        # Raw body must NOT bleed into the user-facing message.
        assert "boom" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_network_error_maps_to_unreachable(self, client):
        # Force aiohttp to raise a ClientError when the request is dispatched.
        with patch("aiohttp.ClientSession.request") as mock_request:
            mock_request.side_effect = aiohttp.ClientConnectorError(
                connection_key=AsyncMock(), os_error=OSError("dns fail")
            )
            client.token = "fake-token"  # skip the auth step
            with pytest.raises(PaprikaUnreachableError):
                await client._make_authenticated_request("GET", "/sync/groceries")


class TestAmbiguousIsTtsSafe:
    @pytest.fixture
    def client(self):
        return PaprikaClient("test@example.com", "testpass")

    def test_message_lists_names_and_lists_not_uids(self, client):
        items = [
            {"uid": "uid-AAAA-1111", "name": "appel groen", "list_uid": "L1"},
            {"uid": "uid-BBBB-2222", "name": "appel rood", "list_uid": "L2"},
        ]
        list_names = {"L1": "Default", "L2": "Costco run"}
        with pytest.raises(AmbiguousMatchError) as exc_info:
            client._resolve_strict("appel", items, list_names=list_names)
        msg = str(exc_info.value)
        # Names + list names spoken aloud
        assert "appel groen" in msg
        assert "appel rood" in msg
        assert "Default" in msg
        assert "Costco run" in msg
        # UIDs absolutely must not be spoken
        assert "uid-AAAA-1111" not in msg
        assert "uid-BBBB-2222" not in msg
        # But structured candidates DO carry the UIDs for the LLM
        candidates = exc_info.value.candidates
        assert {c["uid"] for c in candidates} == {"uid-AAAA-1111", "uid-BBBB-2222"}
        assert {c["list_name"] for c in candidates} == {"Default", "Costco run"}


class TestListNotFoundIncludesAvailable:
    @pytest.mark.asyncio
    async def test_available_lists_attached(self):
        client = PaprikaClient("test@example.com", "testpass")
        with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
            mock_lists.return_value = [
                {"uid": "L1", "name": "Default"},
                {"uid": "L2", "name": "Costco run"},
            ]
            with pytest.raises(GroceryListNotFoundError) as exc_info:
                await client._resolve_list_uid_strict("Trader Joe's")
            assert exc_info.value.extra["available_lists"] == ["Default", "Costco run"]
            assert "Default" in str(exc_info.value)
            assert "Costco run" in str(exc_info.value)


class TestUpdateRecipePartialNotFound:
    @pytest.mark.asyncio
    async def test_missing_uid_raises_recipe_not_found(self):
        client = PaprikaClient("test@example.com", "testpass")
        with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock) as mock_req:
            # Paprika returned a 200 with empty result (the documented "not found" shape).
            mock_req.return_value = {"result": {}}
            with pytest.raises(RecipeNotFoundError):
                await client.update_recipe_partial(uid="nope", name="x")
