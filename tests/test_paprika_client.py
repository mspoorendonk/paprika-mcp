from unittest.mock import AsyncMock, patch

import pytest

from src.paprika_client import PaprikaAPIError, PaprikaClient


class TestPaprikaClient:
    @pytest.fixture
    def client(self):
        return PaprikaClient("test@example.com", "testpass")

    @pytest.mark.asyncio
    async def test_authentication_success(self, client):
        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json.return_value = {"result": {"token": "test_token"}}
            mock_post.return_value.__aenter__.return_value = mock_response
            mock_post.return_value.__aexit__.return_value = None

            token = await client.authenticate()
            assert token == "test_token"
            assert client.token == "test_token"

    @pytest.mark.asyncio
    async def test_authentication_failure(self, client):
        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 401
            mock_post.return_value.__aenter__.return_value = mock_response
            mock_post.return_value.__aexit__.return_value = None

            with pytest.raises(PaprikaAPIError):
                await client.authenticate()

    @pytest.mark.asyncio
    async def test_get_groceries(self, client):
        with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"result": [{"uid": "123", "name": "Apple"}]}
            groceries = await client.get_groceries()
            assert len(groceries) == 1
            assert groceries[0]["name"] == "Apple"
            mock_req.assert_called_once_with("GET", "/sync/groceries")

    @pytest.mark.asyncio
    async def test_add_grocery_item(self, client):
        with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock) as mock_req:
            with patch.object(client, "_resolve_list_uid", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = "list-456"
                mock_req.return_value = {} 
                result = await client.add_grocery_item(name="Banana", ingredient="Banana")
                assert result["name"] == "Banana"
                assert result["list_uid"] == "list-456"
                assert mock_req.called

    @pytest.mark.asyncio
    async def test_remove_grocery_item(self, client):
        with patch.object(client, "get_groceries", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [{"uid": "123", "name": "Apple", "list_uid": "list-456"}]
            with patch.object(client, "get_grocery_lists", new_callable=AsyncMock) as mock_lists:
                mock_lists.return_value = [{"uid": "list-456", "name": "Default"}]
                with patch.object(client, "_make_authenticated_request", new_callable=AsyncMock) as mock_req:
                    await client.remove_grocery_item("123")
                    mock_req.assert_called_once()
