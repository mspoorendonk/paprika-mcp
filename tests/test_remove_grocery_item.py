from unittest.mock import AsyncMock, patch

import pytest

from src.paprika_client import (
    AmbiguousMatchError,
    GroceryListNotFoundError,
    GroceryNotFoundError,
    PaprikaAPIError,
    PaprikaClient,
)


class TestRemoveGroceryItem:
    @pytest.fixture
    def client(self):
        return PaprikaClient("test@example.com", "testpass")

    @pytest.fixture
    def groceries_multi_list(self):
        """Groceries spread across two lists."""
        return [
            {"uid": "item-1", "name": "appels", "list_uid": "list-default", "purchased": False},
            {"uid": "item-2", "name": "rabarber of aardbeien ong 5 ons", "list_uid": "list-other", "purchased": False},
            {"uid": "item-3", "name": "wc-rollen", "list_uid": "list-default", "purchased": False},
            {"uid": "item-4", "name": "chocolade", "list_uid": "list-default", "purchased": False},
        ]

    @pytest.fixture
    def grocery_lists(self):
        return [
            {"uid": "list-default", "name": "Default", "is_default": True},
            {"uid": "list-other", "name": "Week menu", "is_default": False},
        ]

    @pytest.mark.asyncio
    async def test_remove_grocery_item_searches_all_lists_by_default(self, client, groceries_multi_list, grocery_lists):
        """Item on a non-default list is found and removed when no list specified."""
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries_multi_list
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = grocery_lists
                with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock) as mock_req:
                    result = await client.remove_grocery_item("rabarber")
                    assert result["uid"] == "item-2"
                    assert result["name"] == "rabarber of aardbeien ong 5 ons"
                    assert result["list_uid"] == "list-other"
                    mock_req.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_grocery_item_with_list_name_or_id_confines_search(self, client, groceries_multi_list, grocery_lists):
        """Explicit list scope works — only items on that list are candidates."""
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries_multi_list
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = grocery_lists
                with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock) as mock_req:
                    result = await client.remove_grocery_item("appels", list_name_or_id="Default")
                    assert result["uid"] == "item-1"
                    assert result["list_uid"] == "list-default"

    @pytest.mark.asyncio
    async def test_remove_grocery_item_invalid_list_raises(self, client, groceries_multi_list, grocery_lists):
        """Passing a non-existent list name raises a clear error (no silent fallback)."""
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries_multi_list
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = grocery_lists
                with pytest.raises(GroceryListNotFoundError, match="Nonexistent List"):
                    await client.remove_grocery_item("appels", list_name_or_id="Nonexistent List")

    @pytest.mark.asyncio
    async def test_remove_grocery_item_no_match_raises(self, client, groceries_multi_list, grocery_lists):
        """When nothing matches, a GroceryNotFoundError is raised."""
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries_multi_list
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = grocery_lists
                with pytest.raises(GroceryNotFoundError, match="nothing called"):
                    await client.remove_grocery_item("zzzzz_no_such_item")

    @pytest.mark.asyncio
    async def test_remove_grocery_item_ambiguous_match_raises_with_candidates(self, client):
        """Multiple items pass the matcher → error listing them."""
        groceries = [
            {"uid": "item-a", "name": "appel groen", "list_uid": "list-1", "purchased": False},
            {"uid": "item-b", "name": "appel rood", "list_uid": "list-2", "purchased": False},
        ]
        lists = [
            {"uid": "list-1", "name": "List One"},
            {"uid": "list-2", "name": "List Two"},
        ]
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = lists
                with pytest.raises(AmbiguousMatchError) as exc_info:
                    await client.remove_grocery_item("appel")
                assert len(exc_info.value.candidates) == 2
                uids = {c["uid"] for c in exc_info.value.candidates}
                assert uids == {"item-a", "item-b"}
                # User-facing message must NOT contain UIDs (TTS-safe).
                assert "item-a" not in str(exc_info.value)
                assert "appel" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_remove_grocery_item_returns_actual_removed_item(self, client, groceries_multi_list, grocery_lists):
        """The returned object reflects what was actually deleted, not the input string."""
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries_multi_list
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = grocery_lists
                with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock):
                    result = await client.remove_grocery_item("chocolade")
                    assert result["uid"] == "item-4"
                    assert result["name"] == "chocolade"
                    assert result["list_uid"] == "list-default"

    @pytest.mark.asyncio
    async def test_remove_grocery_item_by_uid(self, client, groceries_multi_list, grocery_lists):
        """Exact UID match works regardless of list."""
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = groceries_multi_list
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = grocery_lists
                with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock):
                    result = await client.remove_grocery_item("item-2")
                    assert result["uid"] == "item-2"
                    assert result["name"] == "rabarber of aardbeien ong 5 ons"


class TestResolveStrict:
    """Test the _resolve_strict method directly."""

    @pytest.fixture
    def client(self):
        return PaprikaClient("test@example.com", "testpass")

    def test_exact_uid_match(self, client):
        items = [{"uid": "abc-123", "name": "Apples"}]
        result = client._resolve_strict("abc-123", items)
        assert result["uid"] == "abc-123"

    def test_case_insensitive_exact_name(self, client):
        items = [{"uid": "1", "name": "Chocolade"}]
        result = client._resolve_strict("chocolade", items)
        assert result["uid"] == "1"

    def test_substring_match_unique(self, client):
        items = [
            {"uid": "1", "name": "rabarber of aardbeien ong 5 ons", "list_uid": "x"},
            {"uid": "2", "name": "wc-rollen", "list_uid": "x"},
        ]
        result = client._resolve_strict("rabarber", items)
        assert result["uid"] == "1"

    def test_no_match_raises(self, client):
        items = [{"uid": "1", "name": "banaan", "list_uid": "x"}]
        with pytest.raises(GroceryNotFoundError, match="nothing called"):
            client._resolve_strict("zzzzz", items)

    def test_fuzzy_dissimilar_names_not_matched(self, client):
        """Ensure the strict matcher does NOT accept dissimilar names like the old fuzzy did."""
        items = [
            {"uid": "1", "name": "wc-rollen", "list_uid": "x"},
            {"uid": "2", "name": "appels", "list_uid": "x"},
        ]
        # "rabarber" should NOT match "wc-rollen" (the old fuzzy could match at ~0.46)
        with pytest.raises(GroceryNotFoundError, match="nothing called"):
            client._resolve_strict("rabarber", items)

    def test_short_query_no_substring(self, client):
        """Queries shorter than 3 chars skip substring matching."""
        items = [{"uid": "1", "name": "ab extra", "list_uid": "x"}]
        with pytest.raises(GroceryNotFoundError, match="nothing called"):
            client._resolve_strict("ab", items)

    def test_ambiguous_substring_raises(self, client):
        items = [
            {"uid": "1", "name": "appel groen", "list_uid": "list-1"},
            {"uid": "2", "name": "appel rood", "list_uid": "list-2"},
        ]
        with pytest.raises(AmbiguousMatchError) as exc_info:
            client._resolve_strict("appel", items)
        assert len(exc_info.value.candidates) == 2
