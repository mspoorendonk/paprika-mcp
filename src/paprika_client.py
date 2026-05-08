import asyncio
import gzip
import hashlib
import json
import logging
import random
import uuid
import difflib
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Concurrency cap and inter-request jitter for refetching individual recipe
# bodies. Paprika applies aggressive per-IP rate limiting (multi-minute IP
# blocks for bursty traffic), so the cache refresh fans out gently.
RECIPE_FETCH_CONCURRENCY = 3
RECIPE_FETCH_JITTER_SECONDS = 0.05


class PaprikaAPIError(Exception):
    """Base class for all Paprika MCP errors.

    Every subclass carries a stable ``code`` string (see specs.md
    "Scenario 9 — Errors the user should hear in plain language") that the
    LLM can branch on, plus a TTS-friendly message in ``str(self)``. Extra
    structured data (candidate lists, available list names, etc.) is
    attached as attributes and surfaced to MCP clients via
    ``structuredContent``.
    """

    code: str = "paprika_error"

    def __init__(self, message: str, **extra: Any):
        super().__init__(message)
        # Anything passed as kwargs becomes part of structuredContent.
        self.extra: Dict[str, Any] = dict(extra)


class PaprikaUnreachableError(PaprikaAPIError):
    """Network failure reaching paprikaapp.com (DNS, TCP, TLS, timeout)."""

    code = "paprika_unreachable"


class PaprikaAuthError(PaprikaAPIError):
    """Paprika rejected the credentials (401/403)."""

    code = "paprika_auth_failed"


class PaprikaRateLimitedError(PaprikaAPIError):
    """Paprika is throttling us (HTTP 429 or temporary IP block)."""

    code = "paprika_rate_limited"


class InvalidArgumentError(PaprikaAPIError):
    """Caller-supplied arguments are missing or malformed."""

    code = "invalid_argument"


class GroceryNotFoundError(PaprikaAPIError):
    """No grocery item matches the query."""

    code = "grocery_not_found"


class GroceryListNotFoundError(PaprikaAPIError):
    """No grocery list matches the query.

    ``available_lists`` (list[str]) is attached so the assistant can read
    the user's actual list names aloud.
    """

    code = "grocery_list_not_found"


class RecipeNotFoundError(PaprikaAPIError):
    """No recipe matches the query / UID."""

    code = "recipe_not_found"


class AmbiguousMatchError(PaprikaAPIError):
    """Multiple grocery items match a query; LLM must disambiguate.

    ``candidates`` is a list of ``{uid, name, list_uid, list_name}`` dicts
    suitable for inclusion in MCP ``structuredContent``.
    """

    code = "grocery_ambiguous"

    def __init__(self, message: str, candidates: List[Dict[str, Any]]):
        super().__init__(message, candidates=candidates)
        self.candidates = candidates


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

        # Recipe cache (see specs.md "Recipe cache").
        self._recipe_cache: Dict[str, Dict[str, Any]] = {}
        self._recipe_index_fingerprint: Optional[str] = None
        self._cache_lock = asyncio.Lock()
        self._cache_ready = asyncio.Event()

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
            PaprikaAuthError: If credentials are rejected.
            PaprikaUnreachableError: If the network call fails.
            PaprikaAPIError: For any other unexpected failure.
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
                if response.status in (401, 403):
                    raise PaprikaAuthError(
                        "Paprika rejected my login. The saved username or "
                        "password is probably wrong."
                    )
                if response.status != 200:
                    body = await response.text()
                    logger.error(
                        "Paprika login failed: status=%s body=%s",
                        response.status, body[:500],
                    )
                    raise PaprikaAPIError(
                        "Paprika returned an unexpected error while signing in."
                    )

                try:
                    result = await response.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError) as e:
                    logger.error("Login response was not valid JSON: %s", e)
                    raise PaprikaAPIError(
                        "Paprika sent back an invalid login response."
                    )

                if "result" not in result or "token" not in result["result"]:
                    logger.error("Login response missing token: %s", result)
                    raise PaprikaAuthError(
                        "Paprika rejected my login. The saved username or "
                        "password is probably wrong."
                    )

                self.token = result["result"]["token"]
                logger.info("Successfully authenticated with Paprika API")
                return self.token

        except aiohttp.ClientError as e:
            logger.error("Network error during authentication: %s", e)
            raise PaprikaUnreachableError(
                "I can't reach the Paprika service right now. Please try "
                "again in a moment."
            )
        except asyncio.TimeoutError:
            logger.error("Timeout during authentication")
            raise PaprikaUnreachableError(
                "I can't reach the Paprika service right now. Please try "
                "again in a moment."
            )

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
            PaprikaAuthError: If authentication fails (401/403 even after retry).
            PaprikaRateLimitedError: If Paprika throttles us (429/503).
            PaprikaUnreachableError: For network-level failures.
            PaprikaAPIError: For any other non-2xx response.
        """
        if not self.token:
            await self.authenticate()

        session = await self._get_session()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"

        async def _do_request(req_headers: Dict[str, str]) -> aiohttp.ClientResponse:
            return await session.request(
                method, f"{self.BASE_URL}/v2{endpoint}",
                headers=req_headers, **kwargs,
            )

        try:
            async with await _do_request(headers) as response:
                # Re-auth path: token might be expired.
                if response.status == 401:
                    logger.info("Got 401, re-authenticating and retrying")
                    self.token = None
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self.token}"
                    async with await _do_request(headers) as retry_response:
                        return await self._parse_response(retry_response, endpoint)

                return await self._parse_response(response, endpoint)

        except aiohttp.ClientError as e:
            logger.error("Network error on %s %s: %s", method, endpoint, e)
            raise PaprikaUnreachableError(
                "I can't reach the Paprika service right now. Please try "
                "again in a moment."
            )
        except asyncio.TimeoutError:
            logger.error("Timeout on %s %s", method, endpoint)
            raise PaprikaUnreachableError(
                "I can't reach the Paprika service right now. Please try "
                "again in a moment."
            )

    async def _parse_response(
        self, response: aiohttp.ClientResponse, endpoint: str
    ) -> Dict[str, Any]:
        """Map an HTTP response to JSON or a typed exception.

        Body content is logged but never returned in the user-visible
        message — voice agents must not read raw HTML or JSON aloud.
        """
        status = response.status
        if status == 200:
            try:
                return await response.json()
            except (json.JSONDecodeError, aiohttp.ContentTypeError) as e:
                logger.error("Invalid JSON from %s: %s", endpoint, e)
                raise PaprikaAPIError(
                    "Paprika returned an unexpected error. I've logged the "
                    "details."
                )

        body = await response.text()
        logger.error(
            "Paprika error on %s: status=%s body=%s",
            endpoint, status, body[:500],
        )

        if status in (401, 403):
            raise PaprikaAuthError(
                "Paprika rejected my login. The saved username or password "
                "is probably wrong."
            )
        if status in (429, 503):
            raise PaprikaRateLimitedError(
                "Paprika is rate-limiting us. Please try again in a couple "
                "of minutes."
            )
        raise PaprikaAPIError(
            "Paprika returned an unexpected error. I've logged the details."
        )

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

        await self._make_authenticated_request(
            "POST", f"/sync/recipe/{recipe['uid']}/", data=data
        )
        logger.info(f"Successfully created recipe: {name}")
        return recipe

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

        await self._make_authenticated_request(
            "POST", f"/sync/recipe/{uid}/", data=data
        )
        logger.info(f"Successfully updated recipe: {name}")
        return recipe

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
            RecipeNotFoundError: If no recipe exists with the given UID.
            PaprikaAPIError (and subclasses): On other Paprika failures.
        """
        # First, get the existing recipe
        response = await self._make_authenticated_request(
            "GET", f"/sync/recipe/{uid}/"
        )
        existing_recipe = response.get("result", {})

        if not existing_recipe:
            raise RecipeNotFoundError(
                "I can't find a recipe with that ID. It may have been deleted."
            )

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

    @staticmethod
    def _fingerprint_index(index: List[Dict[str, Any]]) -> str:
        """Compute a stable fingerprint over the (uid, hash) pairs of the
        recipe index. Used as the cheap "did anything change?" signal."""
        pairs = sorted(
            (item.get("uid", ""), item.get("hash", "")) for item in index
        )
        blob = json.dumps(pairs, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    async def _fetch_recipe_index(self) -> List[Dict[str, Any]]:
        """Fetch the lightweight `[{uid, hash}, ...]` index of all recipes."""
        response = await self._make_authenticated_request("GET", "/sync/recipes")
        return response.get("result", []) or []

    async def _fetch_full_recipe(
        self, uid: str, semaphore: asyncio.Semaphore
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single recipe body, gated by the shared semaphore."""
        async with semaphore:
            # Small jitter so we don't hammer the API in lockstep.
            await asyncio.sleep(random.uniform(0, RECIPE_FETCH_JITTER_SECONDS))
            try:
                resp = await self._make_authenticated_request(
                    "GET", f"/sync/recipe/{uid}/"
                )
                return resp.get("result") or None
            except Exception as e:
                logger.warning(f"Failed to fetch recipe {uid}: {e}")
                return None

    async def refresh_recipe_cache(self, force: bool = False) -> int:
        """Refresh the in-memory recipe cache against the Paprika server.

        Strategy (see specs.md "Recipe cache"):
          1. GET /sync/recipes (cheap index of {uid, hash}).
          2. If the index fingerprint matches the cached one and not
             ``force``, no per-recipe fetches are performed.
          3. Otherwise diff: drop deleted uids, refetch new or hash-changed
             uids in parallel under a small concurrency cap.

        Returns the number of recipe bodies (re)fetched in this call.
        """
        async with self._cache_lock:
            # Let typed PaprikaAPIError subclasses bubble through unchanged
            # so the caller (list_recipes / warm_up_cache) can decide what
            # to do (e.g. serve stale cache vs surface a friendly error).
            index = await self._fetch_recipe_index()

            fingerprint = self._fingerprint_index(index)
            if (
                not force
                and fingerprint == self._recipe_index_fingerprint
                and self._cache_ready.is_set()
            ):
                logger.debug(
                    "Recipe cache is up to date (%d recipes)",
                    len(self._recipe_cache),
                )
                return 0

            remote_uids = {item["uid"]: item.get("hash") for item in index if item.get("uid")}

            # Drop recipes that disappeared on the server.
            deleted = set(self._recipe_cache).difference(remote_uids)
            for uid in deleted:
                self._recipe_cache.pop(uid, None)

            # Stale = new uid, or hash differs from our cached copy.
            stale_uids = [
                uid
                for uid, remote_hash in remote_uids.items()
                if force
                or uid not in self._recipe_cache
                or self._recipe_cache[uid].get("hash") != remote_hash
            ]

            fetched = 0
            if stale_uids:
                semaphore = asyncio.Semaphore(RECIPE_FETCH_CONCURRENCY)
                results = await asyncio.gather(
                    *(self._fetch_full_recipe(uid, semaphore) for uid in stale_uids)
                )
                for uid, recipe in zip(stale_uids, results):
                    if recipe is None:
                        # Leave any prior cached copy in place; we'll retry next
                        # refresh. Don't update fingerprint either.
                        continue
                    self._recipe_cache[uid] = recipe
                    fetched += 1

            # Only commit the new fingerprint if every stale recipe was fetched
            # successfully; otherwise the next call will retry the misses.
            if fetched == len(stale_uids):
                self._recipe_index_fingerprint = fingerprint

            self._cache_ready.set()
            logger.info(
                "Recipe cache refresh: %d total, %d (re)fetched, %d deleted",
                len(self._recipe_cache), fetched, len(deleted),
            )
            return fetched

    async def warm_up_cache(self) -> None:
        """Populate the recipe cache from scratch. Intended for startup.

        Logs and swallows errors so a transient Paprika failure does not
        prevent the MCP server from coming up.
        """
        try:
            await self.refresh_recipe_cache(force=True)
        except Exception as e:
            logger.warning(f"Initial recipe cache warm-up failed: {e}")
            # Still mark ready so callers don't block forever; they'll get an
            # empty list the first time and a real refresh attempt next call.
            self._cache_ready.set()

    async def list_recipes(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        List recipes from Paprika, served from a hash-validated cache.

        Args:
            limit: Maximum number of recipes to return.

        Returns:
            List of recipe data (excluding recipes in trash).

        Raises:
            PaprikaAPIError: If the lightweight index call fails.
        """
        try:
            await self.refresh_recipe_cache()
        except PaprikaAPIError:
            # If the index call itself fails but we have a populated cache,
            # serve stale data rather than failing the LLM tool call.
            if not self._recipe_cache:
                raise
            logger.warning("Serving recipes from stale cache after refresh failure")

        # Wait once for the very first warm-up if a caller raced the server.
        if not self._cache_ready.is_set():
            await self._cache_ready.wait()

        recipes = [
            r for r in self._recipe_cache.values()
            if not r.get("in_trash", False)
        ]
        # Stable order so LLM output is reproducible.
        recipes.sort(key=lambda r: (r.get("name") or "").lower())
        return recipes[:limit]

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
        response = await self._make_authenticated_request("GET", "/sync/grocerylists")
        return response.get("result", [])

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

    async def _resolve_list_uid_strict(self, list_query: str) -> str:
        """Resolve a target list UID strictly. Raises if not found."""
        lists = await self.get_grocery_lists()
        matched_list = self._resolve_fuzzy(list_query, lists)
        if matched_list:
            return matched_list["uid"]
        available = [lst.get("name", "") for lst in lists if lst.get("name")]
        available_str = ", ".join(available) if available else "none"
        raise GroceryListNotFoundError(
            f"You don't have a grocery list called '{list_query}'. "
            f"Your lists are: {available_str}.",
            available_lists=available,
        )

    def _resolve_strict(
        self,
        query: str,
        items: List[Dict[str, Any]],
        name_key: str = "name",
        id_key: str = "uid",
        list_names: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Resolve a query to an item using strict matching only (safe for destructive ops).

        Matching order: exact UID → case-insensitive exact name → unambiguous substring.
        Raises AmbiguousMatchError if multiple items match (with structured
        candidates including list names where available).
        Raises GroceryNotFoundError if no items match.
        """
        list_names = list_names or {}

        def make_candidates(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [
                {
                    "uid": m.get(id_key),
                    "name": m.get(name_key),
                    "list_uid": m.get("list_uid"),
                    "list_name": list_names.get(m.get("list_uid", ""), "unknown list"),
                }
                for m in matches
            ]

        def ambiguous(matches: List[Dict[str, Any]]) -> AmbiguousMatchError:
            spoken = ", ".join(
                f"{m.get(name_key)} on {list_names.get(m.get('list_uid', ''), 'unknown list')}"
                for m in matches
            )
            return AmbiguousMatchError(
                f"Multiple items match '{query}': {spoken}. Which one?",
                candidates=make_candidates(matches),
            )

        if not query or not items:
            raise GroceryNotFoundError(
                f"There's nothing called '{query}' on your active grocery lists."
            )

        # 1. Exact ID match
        for item in items:
            if item.get(id_key) == query:
                return item

        # 2. Exact name match (case insensitive)
        query_lower = query.lower()
        exact_matches = [item for item in items if item.get(name_key, "").lower() == query_lower]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise ambiguous(exact_matches)

        # 3. Substring match (query must be at least 3 chars)
        substring_matches = []
        if len(query_lower) >= 3:
            for item in items:
                name_lower = item.get(name_key, "").lower()
                if query_lower in name_lower or name_lower in query_lower:
                    substring_matches.append(item)

        if len(substring_matches) == 1:
            return substring_matches[0]
        if len(substring_matches) > 1:
            raise ambiguous(substring_matches)

        raise GroceryNotFoundError(
            f"There's nothing called '{query}' on your active grocery lists."
        )

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

    async def get_groceries(
        self, include_purchased: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch groceries from Paprika.

        Args:
            include_purchased: If False (default), filter out items already
                marked as purchased. The Paprika grocery list can accumulate
                hundreds of checked-off items, which are rarely useful to a
                caller asking "what's on my list".

        Returns:
            List of grocery items

        Raises:
            PaprikaAPIError (and subclasses): If the underlying call fails.
        """
        response = await self._make_authenticated_request("GET", "/sync/groceries")
        groceries = response.get("result", [])
        total = len(groceries)
        if not include_purchased:
            groceries = [g for g in groceries if not g.get("purchased")]
        logger.info(
            f"Successfully fetched {len(groceries)} groceries "
            f"(filtered from {total}, include_purchased={include_purchased})"
        )
        return groceries

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

        await self._make_authenticated_request("POST", "/sync/groceries", data=data)
        logger.info(f"Successfully created grocery: {name}")
        return grocery_obj

    async def remove_grocery_item(self, item_name_or_id: str, list_name_or_id: Optional[str] = None) -> Dict[str, Any]:
        """
        "Remove" a grocery item from the active shopping list by marking it as
        purchased. The item is NOT deleted from Paprika — it stays in the
        list's history (where the Paprika app shows it under the checked-off
        section) so the user can un-check it later or re-add it with one tap.

        This matches what users mean colloquially by "remove this from my
        shopping list": once they've bought it (or no longer want it on the
        active list), it should disappear from the unchecked view but not be
        permanently destroyed.

        By default only unpurchased items are considered for matching, since
        the active shopping list is what the user is referring to. Searches
        across all grocery lists unless list_name_or_id is provided.

        Uses strict matching (exact UID, exact name, or unambiguous substring)
        to prevent accidentally checking off the wrong item.

        Args:
            item_name_or_id: The UID or name of the grocery item to mark purchased
            list_name_or_id: Name or UID of list to confine search. If omitted, searches all lists.

        Returns:
            The updated item dict (uid, name, list_uid)

        Raises:
            GroceryNotFoundError: If no item matches.
            AmbiguousMatchError: If multiple items match the query.
            GroceryListNotFoundError: If a specified list doesn't exist.
            PaprikaAPIError (and subclasses): On other Paprika failures.
        """
        # Only consider items that are still on the active shopping list.
        # Already-purchased items have effectively already been "removed".
        groceries = await self.get_groceries(include_purchased=False)

        # Only filter by list when explicitly provided
        if list_name_or_id:
            target_list_uid = await self._resolve_list_uid_strict(list_name_or_id)
            groceries = [g for g in groceries if g.get("list_uid") == target_list_uid]

        # Build a uid->list-name map so candidate errors can be spoken aloud
        # without exposing UIDs to the user.
        list_names = {
            lst["uid"]: lst.get("name", "unknown list")
            for lst in await self.get_grocery_lists()
        }
        item = self._resolve_strict(
            item_name_or_id, groceries,
            name_key="name", id_key="uid",
            list_names=list_names,
        )

        item["purchased"] = True
        updated_uid = item["uid"]

        gzipped_data = self._gzip_json([item])
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")

        await self._make_authenticated_request("POST", "/sync/groceries", data=data)
        logger.info(f"Marked grocery as purchased: {updated_uid}")

        return {"uid": item["uid"], "name": item["name"], "list_uid": item.get("list_uid", "unknown")}

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
