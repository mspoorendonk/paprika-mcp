import gzip
import hashlib
import json
import logging
import uuid
import difflib
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class PaprikaAPIError(Exception):
    """Custom exception for Paprika API errors."""

    pass


class PaprikaClient:
    """Client for interacting with the Paprika Recipe Manager API."""

    BASE_URL = "https://paprikaapp.com/api"

    def __init__(self, username: str, password: str):
        """
        Initialize the Paprika client.

        Args:
            username: Paprika account email
            password: Paprika account password
        """
        self.username = username
        self.password = password
        self.token: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session

    async def authenticate(self) -> Optional[str]:
        """
        Authenticate with Paprika API and get access token.

        Returns:
            Authentication token

        Raises:
            PaprikaAPIError: If authentication fails
        """
        session = await self._get_session()

        login_data = {"email": self.username, "password": self.password}

        try:
            # Use v1 API for authentication (v2 returns "Unrecognized client")
            async with session.post(
                f"{self.BASE_URL}/v1/account/login",
                data=login_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status != 200:
                    raise PaprikaAPIError(f"Login failed with status {response.status}")

                result = await response.json()

                if "result" not in result or "token" not in result["result"]:
                    raise PaprikaAPIError("Invalid login response format")

                self.token = result["result"]["token"]
                logger.info("Successfully authenticated with Paprika API")
                return self.token

        except aiohttp.ClientError as e:
            raise PaprikaAPIError(f"Network error during authentication: {str(e)}")
        except json.JSONDecodeError:
            raise PaprikaAPIError("Invalid JSON response from login endpoint")

    async def _make_authenticated_request(
        self, method: str, endpoint: str, **kwargs
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            **kwargs: Additional arguments for aiohttp request

        Returns:
            JSON response data

        Raises:
            PaprikaAPIError: If request fails
        """
        if not self.token:
            await self.authenticate()

        session = await self._get_session()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"

        try:
            async with session.request(
                method, f"{self.BASE_URL}/v2{endpoint}", headers=headers, **kwargs
            ) as response:
                if response.status == 401:
                    # Token might be expired, re-authenticate
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self.token}"

                    async with session.request(
                        method,
                        f"{self.BASE_URL}/v2{endpoint}",
                        headers=headers,
                        **kwargs,
                    ) as retry_response:
                        if retry_response.status != 200:
                            raise PaprikaAPIError(
                                f"Request failed with status {retry_response.status}"
                            )

                        return await retry_response.json()

                elif response.status != 200:
                    error_text = await response.text()
                    raise PaprikaAPIError(
                        f"Request failed with status {response.status}: {error_text}"
                    )

                return await response.json()

        except aiohttp.ClientError as e:
            raise PaprikaAPIError(f"Network error: {str(e)}")

    def _generate_uuid(self) -> str:
        """Generate a new uppercase UUID."""
        return str(uuid.uuid4()).upper()

    def _calculate_hash(self, recipe_dict: Dict[str, Any]) -> str:
        """
        Calculate SHA256 hash for a recipe object.

        Args:
            recipe_dict: Recipe data dictionary

        Returns:
            Hex-encoded SHA256 hash
        """
        # Remove hash field and sort keys for consistent hashing
        data = {k: v for k, v in recipe_dict.items() if k != "hash"}
        json_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def _gzip_json(self, data: Dict[str, Any]) -> bytes:
        """
        Compress JSON data with gzip.

        Args:
            data: Data to compress

        Returns:
            Gzipped JSON bytes
        """
        json_str = json.dumps(data)
        return gzip.compress(json_str.encode("utf-8"))

    def _create_recipe_object(
        self,
        name: str,
        ingredients: str,
        directions: str,
        description: str = "",
        notes: str = "",
        servings: str = "",
        prep_time: str = "",
        cook_time: str = "",
        difficulty: str = "",
        uid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a recipe object with all required fields.

        Args:
            name: Recipe name
            ingredients: Recipe ingredients
            directions: Cooking directions
            description: Recipe description
            notes: Additional notes
            servings: Number of servings
            prep_time: Preparation time
            cook_time: Cooking time
            difficulty: Difficulty level
            uid: Recipe UID (generated if not provided)

        Returns:
            Complete recipe object
        """
        recipe = {
            "uid": uid or self._generate_uuid(),
            "name": name,
            "ingredients": ingredients,
            "directions": directions,
            "description": description,
            "notes": notes,
            "servings": servings,
            "prep_time": prep_time,
            "cook_time": cook_time,
            "total_time": "",
            "difficulty": difficulty,
            "source": "",
            "source_url": "",
            "categories": [],
            "rating": 0,
            "nutritional_info": "",
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "in_trash": False,
            "is_pinned": False,
            "on_favorites": False,
            "on_grocery_list": False,
            "image_url": "",
            "photo": "",
            "photo_hash": "",
            "photo_large": None,
            "photo_url": None,
            "scale": None,
        }

        # Calculate and set hash
        recipe["hash"] = self._calculate_hash(recipe)
        return recipe

    async def create_recipe(
        self,
        name: str,
        ingredients: str,
        directions: str,
        description: str = "",
        notes: str = "",
        servings: str = "",
        prep_time: str = "",
        cook_time: str = "",
        difficulty: str = "",
    ) -> Dict[str, Any]:
        """
        Create a new recipe in Paprika.

        Args:
            name: Recipe name
            ingredients: Recipe ingredients (one per line)
            directions: Cooking directions
            description: Recipe description
            notes: Additional notes
            servings: Number of servings
            prep_time: Preparation time
            cook_time: Cooking time
            difficulty: Difficulty level

        Returns:
            Created recipe data

        Raises:
            PaprikaAPIError: If creation fails
        """
        recipe = self._create_recipe_object(
            name=name,
            ingredients=ingredients,
            directions=directions,
            description=description,
            notes=notes,
            servings=servings,
            prep_time=prep_time,
            cook_time=cook_time,
            difficulty=difficulty,
        )

        # Gzip the recipe data
        gzipped_data = self._gzip_json(recipe)

        # Create multipart form data
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")

        try:
            await self._make_authenticated_request(
                "POST", f"/sync/recipe/{recipe['uid']}/", data=data
            )

            logger.info(f"Successfully created recipe: {name}")
            return recipe

        except Exception as e:
            logger.error(f"Failed to create recipe {name}: {str(e)}")
            raise PaprikaAPIError(f"Failed to create recipe: {str(e)}")

    async def update_recipe(
        self,
        uid: str,
        name: str,
        ingredients: str,
        directions: str,
        description: str = "",
        notes: str = "",
        servings: str = "",
        prep_time: str = "",
        cook_time: str = "",
        difficulty: str = "",
    ) -> Dict[str, Any]:
        """
        Update an existing recipe in Paprika.

        Args:
            uid: Recipe UID to update
            name: Recipe name
            ingredients: Recipe ingredients
            directions: Cooking directions
            description: Recipe description
            notes: Additional notes
            servings: Number of servings
            prep_time: Preparation time
            cook_time: Cooking time
            difficulty: Difficulty level

        Returns:
            Updated recipe data

        Raises:
            PaprikaAPIError: If update fails
        """
        recipe = self._create_recipe_object(
            name=name,
            ingredients=ingredients,
            directions=directions,
            description=description,
            notes=notes,
            servings=servings,
            prep_time=prep_time,
            cook_time=cook_time,
            difficulty=difficulty,
            uid=uid,
        )

        # Gzip the recipe data
        gzipped_data = self._gzip_json(recipe)

        # Create multipart form data
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")

        try:
            await self._make_authenticated_request(
                "POST", f"/sync/recipe/{uid}/", data=data
            )

            logger.info(f"Successfully updated recipe: {name}")
            return recipe

        except Exception as e:
            logger.error(f"Failed to update recipe {name}: {str(e)}")
            raise PaprikaAPIError(f"Failed to update recipe: {str(e)}")

    async def update_recipe_partial(self, uid: str, **kwargs) -> Dict[str, Any]:
        """
        Partially update an existing recipe in Paprika.
        Only updates the fields that are provided.

        Args:
            uid: Recipe UID to update
            **kwargs: Fields to update (name, ingredients, directions, etc.)

        Returns:
            Updated recipe data

        Raises:
            PaprikaAPIError: If update fails
        """
        try:
            # First, get the existing recipe
            response = await self._make_authenticated_request(
                "GET", f"/sync/recipe/{uid}/"
            )
            existing_recipe = response.get("result", {})

            if not existing_recipe:
                raise PaprikaAPIError(f"Recipe with UID {uid} not found")

            # Update only the provided fields
            for field, value in kwargs.items():
                if value is not None and value != "":
                    existing_recipe[field] = value

            # Recalculate hash and update timestamp
            existing_recipe["created"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing_recipe["hash"] = self._calculate_hash(existing_recipe)

            # Gzip the recipe data
            gzipped_data = self._gzip_json(existing_recipe)

            # Create multipart form data
            data = aiohttp.FormData()
            data.add_field(
                "data", gzipped_data, content_type="application/octet-stream", filename="data.gz"
            )

            await self._make_authenticated_request(
                "POST", f"/sync/recipe/{uid}/", data=data
            )

            logger.info(
                f"Successfully partially updated recipe: {existing_recipe['name']}"
            )
            return existing_recipe

        except Exception as e:
            logger.error(f"Failed to partially update recipe {uid}: {str(e)}")
            raise PaprikaAPIError(f"Failed to partially update recipe: {str(e)}")

    async def list_recipes(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        List recipes from Paprika.

        Args:
            limit: Maximum number of recipes to return

        Returns:
            List of recipe data

        Raises:
            PaprikaAPIError: If listing fails
        """
        try:
            # First get the list of recipe UIDs
            response = await self._make_authenticated_request("GET", "/sync/recipes")
            recipe_list = response.get("result", [])

            if not recipe_list:
                return []

            # Limit the number of recipes to fetch
            recipe_list = recipe_list[:limit]

            # Fetch full recipe data for each UID
            recipes = []
            for recipe_info in recipe_list:
                uid = recipe_info["uid"]

                try:
                    recipe_response = await self._make_authenticated_request(
                        "GET", f"/sync/recipe/{uid}/"
                    )

                    recipe_data = recipe_response.get("result", {})

                    # Skip recipes in trash
                    if recipe_data.get("in_trash", False):
                        continue

                    recipes.append(recipe_data)

                except Exception as e:
                    logger.warning(f"Failed to fetch recipe {uid}: {str(e)}")
                    continue

            logger.info(f"Successfully fetched {len(recipes)} recipes")
            return recipes

        except Exception as e:
            logger.error(f"Failed to list recipes: {str(e)}")
            raise PaprikaAPIError(f"Failed to list recipes: {str(e)}")

    def _resolve_fuzzy(self, query: str, items: List[Dict[str, Any]], name_key: str = "name", id_key: str = "uid") -> Optional[Dict[str, Any]]:
        """Resolve a query string to an item using ID, exact name, substring, or fuzzy matching."""
        if not query:
            return None
            
        # 1. Exact ID match
        for item in items:
            if item.get(id_key) == query:
                return item
                
        # 2. Exact name match (case insensitive)
        query_lower = query.lower()
        for item in items:
            if item.get(name_key, "").lower() == query_lower:
                return item
                
        # 3. Substring match
        for item in items:
            name_lower = item.get(name_key, "").lower()
            if query_lower in name_lower or name_lower in query_lower:
                return item
                
        # 4. Fuzzy match
        best_item = None
        best_score = 0.0
        for item in items:
            name_lower = item.get(name_key, "").lower()
            score = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
            if score > best_score:
                best_score = score
                best_item = item
                
        if best_score > 0.4:  # reasonable threshold for "choko" ~ "chocolade"
            return best_item
            
        return None

    async def get_grocery_lists(self) -> List[Dict[str, Any]]:
        """Fetch all grocery lists from Paprika."""
        try:
            response = await self._make_authenticated_request("GET", "/sync/grocerylists")
            return response.get("result", [])
        except Exception as e:
            logger.error(f"Failed to list grocery lists: {str(e)}")
            raise PaprikaAPIError(f"Failed to list grocery lists: {str(e)}")

    async def _resolve_list_uid(self, list_query: Optional[str]) -> str:
        """Resolve a target list UID by name or ID. Falls back to default list."""
        lists = await self.get_grocery_lists()
        
        if list_query:
            matched_list = self._resolve_fuzzy(list_query, lists)
            if matched_list:
                return matched_list["uid"]
                
        # Fall back to default list
        for lst in lists:
            if lst.get("is_default"):
                return lst["uid"]
                
        # Fall back to first list if no default is found
        if lists:
            return lists[0]["uid"]
            
        return self._generate_uuid().lower()

    async def get_groceries(self) -> List[Dict[str, Any]]:
        """
        Fetch all groceries from Paprika.

        Returns:
            List of grocery items

        Raises:
            PaprikaAPIError: If fetching fails
        """
        try:
            response = await self._make_authenticated_request("GET", "/sync/groceries")
            groceries = response.get("result", [])
            logger.info(f"Successfully fetched {len(groceries)} groceries")
            return groceries
        except Exception as e:
            logger.error(f"Failed to list groceries: {str(e)}")
            raise PaprikaAPIError(f"Failed to list groceries: {str(e)}")

    async def add_grocery_item(
        self,
        name: str,
        ingredient: str,
        quantity: str = "",
        instruction: str = "",
        aisle: str = "",
        list_name_or_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add a grocery item to Paprika.

        Args:
            name: Display name
            ingredient: Ingredient name
            quantity: Quantity string
            instruction: Optional instructions
            aisle: Optional aisle name
            list_name_or_id: Target list via Name or UID (uses a default if not provided)

        Returns:
            The created grocery item

        Raises:
            PaprikaAPIError: If creation fails
        """
        resolved_list_uid = await self._resolve_list_uid(list_name_or_id)
            
        uid = self._generate_uuid().lower()
        
        grocery_obj = {
            "uid": uid,
            "name": name,
            "ingredient": ingredient,
            "quantity": quantity,
            "instruction": instruction,
            "list_uid": resolved_list_uid,
            "aisle": aisle,
            "order_flag": 0,
            "purchased": False,
            "recipe_uid": None,
        }
        
        gzipped_data = self._gzip_json([grocery_obj])
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")

        try:
            await self._make_authenticated_request("POST", "/sync/groceries", data=data)
            logger.info(f"Successfully created grocery: {name}")
            return grocery_obj
        except Exception as e:
            logger.error(f"Failed to create grocery {name}: {str(e)}")
            raise PaprikaAPIError(f"Failed to create grocery: {str(e)}")

    async def remove_grocery_item(self, item_name_or_id: str, list_name_or_id: Optional[str] = None) -> None:
        """
        Remove a grocery item from Paprika by marking it as deleted.

        Args:
            item_name_or_id: The ID or name of the grocery item to remove
            list_name_or_id: Target list via Name or UID. If provided, confines search space.

        Raises:
            PaprikaAPIError: If deletion fails
        """
        # To ensure we only send deleted=True and the original item data, we need to fetch it first
        try:
            groceries = await self.get_groceries()
            
            # If list provided, filter groceries; else only look in the default list
            target_list_uid = await self._resolve_list_uid(list_name_or_id)
            if target_list_uid:
                groceries = [g for g in groceries if g.get("list_uid") == target_list_uid]
                
            item = self._resolve_fuzzy(item_name_or_id, groceries, name_key="name", id_key="uid")
            
            if not item:
                raise PaprikaAPIError(f"Grocery '{item_name_or_id}' not found in the target list.")
                
            item["deleted"] = True
            deleted_uid = item["uid"]
            
            gzipped_data = self._gzip_json([item])
            data = aiohttp.FormData()
            data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")
            
            await self._make_authenticated_request("POST", "/sync/groceries", data=data)
            logger.info(f"Successfully deleted grocery: {deleted_uid}")
            
        except Exception as e:
            logger.error(f"Failed to delete grocery {item_name_or_id}: {str(e)}")
            raise PaprikaAPIError(f"Failed to delete grocery: {str(e)}")

    async def close(self):
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
