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

# Meal type mapping for the Paprika meal planner.
# Source: API_REFERENCE.md §Meal Plans (verified live 2026-06-29).
MEAL_TYPE_MAP: dict[str, int] = {
    "breakfast": 0,
    "lunch": 1,
    "dinner": 2,
    "snack": 3,
}
INV_MEAL_TYPE_MAP: dict[int, str] = {v: k for k, v in MEAL_TYPE_MAP.items()}

# Keyword mapping for grocery auto-categorization into Paprika aisles.
# Supports both Dutch and English common items.
DEFAULT_AISLE_KEYWORDS: dict[str, list[str]] = {
    "Produce": [
        "appel", "appels", "peer", "peren", "banaan", "bananen", "tomaat", "tomaten", "cherrytomaat", "cherrytomaatjes",
        "ui", "uien", "bosui", "bosuitjes", "rode ui", "sjalot", "sjalotten", "knoflook", "courgette", "aubergine",
        "paprika", "komkommer", "sla", "ijsbergsla", "rucola", "veldsla", "spinazie", "boerenkool",
        "broccoli", "bloemkool", "wortel", "wortels", "worteltjes", "aardappel", "aardappelen", "aardappels",
        "zoete aardappel", "champignon", "champignons", "paddenstoel", "paddenstoelen", "citroen", "citroenen",
        "limoen", "limoenen", "avocado", "avocados", "gember", "peper", "rode peper", "chilipeper", "peterselie",
        "koriander", "basilicum", "munt", "rozemarijn", "tijm", "dille", "bieslook", "prei", "bleekselderij",
        "venkel", "asperges", "mango", "aardbei", "aardbeien", "blauwe bessen", "frambozen", "sinaasappel",
        "mandarijn", "druiven", "radijs", "fruit", "groente", "groenten", "salade", "lime", "lemon", "onion", "garlic",
    ],
    "Meat": [
        "kip", "kipfilet", "kippendij", "kippendijen", "kippenvleugels", "rund", "rundergehakt",
        "gehakt", "biefstuk", "varken", "varkenshaas", "spek", "spekjes", "bacon", "worst", "braadworst",
        "kalkoen", "lamsvlees", "ham", "carpaccio", "vis", "zalm", "kabeljauw", "tonijn", "garnalen",
        "scampi", "mosselen", "meat", "chicken", "beef", "pork", "fish", "salmon", "shrimp",
    ],
    "Dairy": [
        "melk", "halfvolle melk", "volle melk", "karnemelk", "boter", "roomboter", "kaas", "oude kaas",
        "jonge kaas", "belegen kaas", "parmezaan", "parmigiano", "mozzarella", "feta", "geitenkaas",
        "cheddar", "geraspte kaas", "yoghurt", "kwark", "room", "slagroom", "kookroom", "creme fraiche",
        "crème fraîche", "mascarpone", "ricotta", "ei", "eieren", "dairy", "milk", "cheese", "butter", "cream", "eggs", "yogurt",
    ],
    "Breads and Cereals": [
        "brood", "witbrood", "bruinbrood", "volkorenbrood", "stokbrood", "ciabatta", "baguette",
        "bolletjes", "pistolet", "pistolets", "croissant", "croissants", "wraps", "tortilla",
        "pita", "pitabroodjes", "naan", "havermout", "muesli", "granola", "cruesli", "ontbijtgranen",
        "cornflakes", "bread", "toast", "cereal",
    ],
    "Pasta, Rice and Beans": [
        "pasta", "spaghetti", "penne", "fusilli", "tagliatelle", "macaroni", "lasagnebladen",
        "rijst", "basmati", "zilvervliesrijst", "jasmijnrijst", "risottorijst", "noedels", "noodles",
        "couscous", "bulgur", "quinoa", "linzen", "kikkererwten", "witte bonen", "bruine bonen",
        "zwarte bonen", "kidneybonen", "rice", "beans", "lentils",
    ],
    "Baking Goods": [
        "bloem", "tarwebloem", "zelfrijzend bakmeel", "gist", "bakpoeder", "baksoda", "vanille",
        "vanillesuiker", "suiker", "kristalsuiker", "basterdsuiker", "poedersuiker", "cacao",
        "cacaopoeder", "flour", "sugar", "yeast", "baking powder",
    ],
    "Canned and Jar Goods": [
        "tomatenblokjes", "gepelde tomaten", "passata", "tomatenpuree", "kokosmelk", "mais",
        "ansjovis", "olijven", "kappers", "kappertjes", "augurken", "ingeblikt", "blik",
    ],
    "Oils and Dressings": [
        "olijfolie", "zonnebloemolie", "sesamolie", "kokosolie", "arachideolie", "azijn",
        "balsamico", "wittewijnazijn", "rodewijnazijn", "appelazijn", "mayonaise", "fritessaus",
        "vinaigrette", "dressing", "oil", "olive oil", "vinegar",
    ],
    "Sauces and Condiments": [
        "sojasaus", "ketjap", "ketjap manis", "vissaus", "oestersaus", "sriracha", "sambal",
        "chilisaus", "ketchup", "mosterd", "currypasta", "pesto", "mayo", "worcestershire",
        "pindakaas", "jam", "honing", "nutella", "sauce",
    ],
    "Spices and Seasonings": [
        "zout", "zeezout", "zwarte peper", "witte peper", "paprikapoeder", "gerookt paprikapoeder",
        "komijn", "komijnpoeder", "kurkuma", "korianderpoeder", "kerrie", "kerriepoeder", "kaneel",
        "nootmuskaat", "kruidnagel", "oregano", "tijm", "chilivlokken", "bouillon",
        "bouillonblokje", "bouillonblokjes", "kippenbouillon", "groentebouillon", "runderbouillon",
        "spice", "seasoning", "salt", "pepper", "cinnamon",
    ],
    "Snacks": [
        "chocola", "chocolade", "chips", "tortillachips", "noten", "cashewnoten", "walnoten",
        "amandelen", "pinda's", "borrelnootjes", "koek", "koekjes", "crackers", "popcorn", "snoep",
        "snack", "chocolate", "cookies",
    ],
    "Beverages": [
        "koffie", "koffiebonen", "thee", "frisdrank", "cola", "sinas", "water", "bruiswater",
        "appelsap", "sinaasappelsap", "sap", "tonic", "smoothie", "coffee", "tea", "juice", "soda",
    ],
    "Beer, Wine and Spirits": [
        "bier", "pils", "speciaalbier", "wijn", "rode wijn", "witte wijn", "rosé", "prosecco",
        "champagne", "gin", "rum", "whisky", "vodka", "beer", "wine",
    ],
    "Frozen Foods": [
        "diepvries", "diepvrieserwtjes", "doperwten", "ijs", "roomijs", "diepvriespizza",
        "bladerdeeg", "frozen",
    ],
    "Schoonmaak": [
        "afwasmiddel", "vaatwastabletten", "allesreiniger", "bleek", "wasmiddel", "wasverzachter",
        "keukenrol", "toiletpapier", "wc-papier", "vuilniszakken", "schoonmaakazijn", "spons", "sponsjes",
    ],
}


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

        # Aisle mapping cache
        self._aisle_cache: Optional[Dict[str, str]] = None

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
        self._recipe_cache[recipe["uid"]] = recipe
        self._recipe_index_fingerprint = ""
        self._cache_ready.set()
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
        self._recipe_cache[recipe["uid"]] = recipe
        self._recipe_index_fingerprint = ""
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
        self._recipe_cache[existing_recipe["uid"]] = existing_recipe
        self._recipe_index_fingerprint = ""
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

    async def list_recipes(self, limit: int = 50, query: str = "") -> List[Dict[str, Any]]:
        """
        List recipes from Paprika, served from a hash-validated cache.

        Args:
            limit: Maximum number of recipes to return (applied after filtering).
            query: Case-insensitive substring filter. When non-empty, only recipes
                whose ``name`` or ``ingredients`` contain the query string are
                returned. Matches against both fields so "chicken" finds recipes
                named "Chicken tikka" as well as recipes that merely list chicken
                in their ingredients.

        Returns:
            List of recipe data (excluding recipes in trash), sorted alphabetically.

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

        if query:
            q = query.lower()
            recipes = [
                r for r in recipes
                if q in (r.get("name") or "").lower()
                or q in (r.get("ingredients") or "").lower()
            ]

        # Stable order so LLM output is reproducible.
        recipes.sort(key=lambda r: (r.get("name") or "").lower())
        return recipes[:limit]

    def _resolve_recipe(self, query: str, recipes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Resolve a query to a recipe strictly and safely:
        1. Exact UID
        2. Case-insensitive exact name
        3. Unambiguous substring (query at least 3 chars)
        4. High-confidence typo match (SequenceMatcher score >= 0.85)

        Raises RecipeNotFoundError or AmbiguousMatchError.
        """
        if not query or not recipes:
            raise RecipeNotFoundError(
                f"I can't find a recipe called '{query}'. "
                "Try listing your recipes to find the exact name."
            )

        # 1. Exact UID
        for r in recipes:
            if r.get("uid") == query:
                return r

        # 2. Case-insensitive exact name
        query_lower = query.lower().strip()
        exact = [r for r in recipes if (r.get("name") or "").lower().strip() == query_lower]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            names = ", ".join(f"'{r.get('name')}'" for r in exact)
            raise AmbiguousMatchError(
                f"Multiple recipes match '{query}': {names}. Which one?",
                candidates=[{"uid": r["uid"], "name": r.get("name")} for r in exact],
            )

        # 3. Unambiguous substring match
        if len(query_lower) >= 3:
            subs = [r for r in recipes if query_lower in (r.get("name") or "").lower()]
            if len(subs) == 1:
                return subs[0]
            if len(subs) > 1:
                names = ", ".join(f"'{r.get('name')}'" for r in subs)
                raise AmbiguousMatchError(
                    f"Multiple recipes match '{query}': {names}. Which one?",
                    candidates=[{"uid": r["uid"], "name": r.get("name")} for r in subs],
                )

        # 4. High-confidence typo match
        candidates: List[tuple[float, Dict[str, Any]]] = []
        for r in recipes:
            name = (r.get("name") or "").lower()
            score = difflib.SequenceMatcher(None, query_lower, name).ratio()
            if score >= 0.85:
                candidates.append((score, r))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            if len(candidates) == 1 or (candidates[0][0] - candidates[1][0] >= 0.08):
                logger.info(
                    f"Resolved typo in recipe name '{query}' -> '{candidates[0][1].get('name')}' "
                    f"(score={candidates[0][0]:.2f})"
                )
                return candidates[0][1]
            names = ", ".join(f"'{c[1].get('name')}'" for c in candidates)
            raise AmbiguousMatchError(
                f"Multiple recipes match '{query}': {names}. Which one?",
                candidates=[{"uid": c[1]["uid"], "name": c[1].get("name")} for c in candidates],
            )

        raise RecipeNotFoundError(
            f"I can't find a recipe called '{query}'. "
            "Try listing your recipes to find the exact name."
        )

    async def categorize_grocery_item(self, name: str, ingredient: str = "") -> str:
        """
        Determine the most appropriate aisle/category for a grocery item.
        First checks user's past grocery history from Paprika, then keyword matching,
        and finally defaults to 'Miscellaneous'. Never returns an empty string.
        """
        text = f"{name} {ingredient}".lower().strip()
        if not text:
            return "Miscellaneous"

        # 1. Populate aisle cache from past groceries if not yet loaded
        if self._aisle_cache is None:
            self._aisle_cache = {}
            try:
                past_groceries = await self.get_groceries(include_purchased=True)
                for g in past_groceries:
                    aisle = g.get("aisle")
                    if aisle:
                        g_name = (g.get("name") or "").lower().strip()
                        g_ing = (g.get("ingredient") or "").lower().strip()
                        if g_name:
                            self._aisle_cache[g_name] = aisle
                        if g_ing:
                            self._aisle_cache[g_ing] = aisle
            except Exception as e:
                logger.debug(f"Could not load past groceries for aisle cache: {e}")

        # Exact match in cache
        if self._aisle_cache:
            n_clean = name.lower().strip()
            if n_clean in self._aisle_cache:
                return self._aisle_cache[n_clean]
            i_clean = ingredient.lower().strip()
            if i_clean in self._aisle_cache:
                return self._aisle_cache[i_clean]

            # Word match from cache
            import re
            words = set(re.findall(r"\b\w{3,}\b", text))
            for word in words:
                if word in self._aisle_cache:
                    return self._aisle_cache[word]

        # 2. Check predefined keyword mappings
        import re
        words = set(re.findall(r"\b\w+\b", text))
        for aisle, keywords in DEFAULT_AISLE_KEYWORDS.items():
            for kw in keywords:
                kw_lower = kw.lower()
                if " " in kw_lower:
                    if kw_lower in text:
                        return aisle
                else:
                    if kw_lower in words:
                        return aisle

        # 3. Fallback to Miscellaneous so items are never uncategorized
        return "Miscellaneous"

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
        list_name_or_id: Optional[str] = None,
        recipe_uid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a grocery item to Paprika.

        Args:
            name: Display name
            ingredient: Ingredient name
            quantity: Quantity string
            instruction: Optional instructions
            aisle: Optional aisle name (leave empty for Paprika auto-assignment)
            list_name_or_id: Target list via Name or UID (uses a default if not provided)
            recipe_uid: Optional UID of the recipe this ingredient belongs to. Paprika
                uses this to group grocery items by recipe in its shopping view.

        Returns:
            The created grocery item

        Raises:
            PaprikaAPIError: If creation fails
        """
        resolved_list_uid = await self._resolve_list_uid(list_name_or_id)

        if not aisle:
            aisle = await self.categorize_grocery_item(name=name, ingredient=ingredient)

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
            "separate": False,
            "recipe_uid": recipe_uid,
            "recipe": None,
        }

        gzipped_data = self._gzip_json([grocery_obj])
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")

        await self._make_authenticated_request("POST", "/sync/groceries", data=data)
        logger.info(f"Successfully created grocery: {name} (aisle={aisle}, recipe_uid={recipe_uid})")
        return grocery_obj

    async def add_recipe_to_grocery_list(
        self,
        recipe_name_or_id: str,
        list_name_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Backwards compatibility alias for adding a single recipe's ingredients.
        """
        res = await self.add_recipes_to_grocery_list(
            [recipe_name_or_id], list_name_or_id=list_name_or_id
        )
        res["recipe_name"] = res["recipe_names"][0]
        if res.get("items"):
            res["recipe_uid"] = res["items"][0]["recipe_uid"]
        return res

    async def add_recipes_to_grocery_list(
        self,
        recipe_names_or_ids: List[str],
        list_name_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add all ingredients of multiple recipes to the grocery list in a single bulk POST.

        Resolves the recipes from the cache by UID, exact name, or substring match.
        Each non-empty line of the recipes' ``ingredients`` field becomes one grocery
        item with ``recipe_uid`` set so the Paprika app groups them under the recipe
        name in its shopping view.

        All items are sent in one multipart POST (gzip-compressed JSON array) which
        mirrors how the official Paprika app adds a recipe's ingredients to the list.

        Args:
            recipe_names_or_ids: List of recipe names or UIDs to look up in the cache.
            list_name_or_id: Target grocery list name or UID. Uses the account's
                default list when omitted.

        Returns:
            Dict with keys:
                ``recipe_names`` (list of str), ``item_count`` (int),
                ``list_uid`` (str), ``items`` (list of created grocery dicts).

        Raises:
            RecipeNotFoundError: If any recipe cannot be resolved.
            GroceryListNotFoundError: If the target list doesn't exist.
            PaprikaAPIError: On network / auth failures.
        """
        # Refresh recipe cache before resolving
        try:
            await self.refresh_recipe_cache()
        except PaprikaAPIError:
            if not self._recipe_cache:
                raise

        # Ensure cache is ready (warm-up may still be running at startup).
        if not self._cache_ready.is_set():
            await self._cache_ready.wait()

        # --- Resolve recipes ---
        recipes_cache = [
            r for r in self._recipe_cache.values()
            if not r.get("in_trash", False)
        ]
        
        recipe_names = []
        ingredient_lines_per_recipe = []

        for query in recipe_names_or_ids:
            recipe = self._resolve_recipe(query, recipes_cache)

            recipe_uid = recipe["uid"]
            recipe_name = recipe.get("name", recipe_uid)
            recipe_names.append(recipe_name)

            raw_ingredients: str = recipe.get("ingredients", "") or ""
            lines = [line.strip() for line in raw_ingredients.splitlines() if line.strip()]

            if not lines:
                raise InvalidArgumentError(
                    f"The recipe '{recipe_name}' has no ingredients listed."
                )
            
            ingredient_lines_per_recipe.append((recipe_uid, recipe_name, lines))

        # --- Resolve list ---
        resolved_list_uid = await self._resolve_list_uid(list_name_or_id)

        # --- Build grocery objects ---
        grocery_items = []
        for recipe_uid, recipe_name, lines in ingredient_lines_per_recipe:
            for line in lines:
                item_aisle = await self.categorize_grocery_item(name=line, ingredient=line)
                grocery_items.append({
                    "uid": self._generate_uuid().lower(),
                    "name": line,
                    "ingredient": line,
                    "quantity": "",
                    "instruction": "",
                    "list_uid": resolved_list_uid,
                    "aisle": item_aisle,
                    "order_flag": 0,
                    "purchased": False,
                    "separate": False,
                    "recipe_uid": recipe_uid,
                    "recipe": recipe_name,
                })

        # --- Single bulk POST ---
        if grocery_items:
            gzipped_data = self._gzip_json(grocery_items)
            form_data = aiohttp.FormData()
            form_data.add_field(
                "data", gzipped_data,
                content_type="application/octet-stream",
                filename="data.gz",
            )
            await self._make_authenticated_request("POST", "/sync/groceries", data=form_data)

        logger.info(
            "Added %d ingredients for %d recipes to list %s",
            len(grocery_items), len(recipe_names), resolved_list_uid,
        )
        return {
            "recipe_names": recipe_names,
            "item_count": len(grocery_items),
            "list_uid": resolved_list_uid,
            "items": grocery_items,
        }

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

    async def plan_meals(
        self,
        requests: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Add one or more recipes to the Paprika meal planner.

        Args:
            requests: List of dicts, each with keys:
                - ``recipe_name_or_id`` (str)
                - ``date`` (str in YYYY-MM-DD format)
                - ``meal_type`` (optional str, defaults to "dinner")

        Returns:
            List of dicts with keys ``uid``, ``recipe_name``, ``recipe_uid``,
            ``date``, ``meal_type``.

        Raises:
            RecipeNotFoundError: If any recipe cannot be resolved.
            InvalidArgumentError: If ``date`` or ``meal_type`` is invalid.
            PaprikaAPIError: On network / auth failures.
        """
        if not requests:
            return []

        # Refresh recipe cache before resolving
        try:
            await self.refresh_recipe_cache()
        except PaprikaAPIError:
            if not self._recipe_cache:
                raise

        # Ensure recipe cache is warm
        if not self._cache_ready.is_set():
            await self._cache_ready.wait()

        # Resolve recipe from cache
        recipes = [
            r for r in self._recipe_cache.values()
            if not r.get("in_trash", False)
        ]
        
        meal_objs = []
        results = []

        for req in requests:
            recipe_query = req.get("recipe_name_or_id", "")
            date_str = req.get("date", "")
            meal_type = req.get("meal_type", "dinner").lower().strip()

            if meal_type not in MEAL_TYPE_MAP:
                raise InvalidArgumentError(
                    f"Unknown meal type '{meal_type}'. "
                    f"Use one of: {', '.join(MEAL_TYPE_MAP)}."
                )
            type_int = MEAL_TYPE_MAP[meal_type]

            try:
                from datetime import datetime as _dt
                _dt.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                raise InvalidArgumentError(
                    f"Invalid date '{date_str}'. Use YYYY-MM-DD format (e.g. 2026-07-05)."
                )

            recipe = self._resolve_recipe(recipe_query, recipes)

            recipe_uid = recipe["uid"]
            recipe_name = recipe.get("name", recipe_uid)
            meal_uid = self._generate_uuid()

            meal_objs.append({
                "uid": meal_uid,
                "recipe_uid": recipe_uid,
                "date": f"{date_str} 00:00:00",
                "type": type_int,
                "name": recipe_name,
                "order_flag": 0,
                "type_uid": "",
                "scale": None,
                "is_ingredient": False,
                "deleted": False,
            })
            results.append({
                "uid": meal_uid,
                "recipe_name": recipe_name,
                "recipe_uid": recipe_uid,
                "date": date_str,
                "meal_type": meal_type,
            })

        # Single bulk POST
        gzipped_data = self._gzip_json(meal_objs)
        form_data = aiohttp.FormData()
        form_data.add_field(
            "data", gzipped_data,
            content_type="application/octet-stream",
            filename="data.gz",
        )
        await self._make_authenticated_request("POST", "/sync/meals", data=form_data)

        logger.info(f"Planned {len(meal_objs)} meals.")
        return results

    async def get_plan(
        self,
        days_back: int = 30,
        days_ahead: int = 14,
        reference_date: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get meals from the Paprika meal planner within a date window around today.

        Args:
            days_back: Number of past days to include (default 30).
            days_ahead: Number of future days to include (default 14).
            reference_date: Optional reference date (defaults to today).

        Returns:
            List of meal dicts with keys: ``uid``, ``recipe_uid``, ``name``,
            ``date`` (YYYY-MM-DD), ``meal_type`` (str), sorted chronologically.
        """
        if days_back < 0:
            raise InvalidArgumentError("days_back must be greater than or equal to 0.")
        if days_ahead < 0:
            raise InvalidArgumentError("days_ahead must be greater than or equal to 0.")

        res = await self._make_authenticated_request("GET", "/sync/meals")
        raw_meals = res.get("result", [])

        from datetime import datetime, date, timedelta
        ref = reference_date if reference_date is not None else date.today()
        start_date = ref - timedelta(days=days_back)
        end_date = ref + timedelta(days=days_ahead)

        filtered_meals = []
        for meal in raw_meals:
            if meal.get("deleted", False):
                continue

            date_raw = meal.get("date", "")
            try:
                meal_date = datetime.strptime(date_raw[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            if start_date <= meal_date <= end_date:
                type_int = meal.get("type", 2)
                meal_type_str = INV_MEAL_TYPE_MAP.get(type_int, "dinner")
                filtered_meals.append({
                    "uid": meal.get("uid"),
                    "recipe_uid": meal.get("recipe_uid"),
                    "name": meal.get("name", "Unnamed meal"),
                    "date": meal_date.strftime("%Y-%m-%d"),
                    "meal_type": meal_type_str,
                    "order_flag": meal.get("order_flag", 0),
                })

        type_order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
        filtered_meals.sort(key=lambda m: (m["date"], type_order.get(m["meal_type"], 99), m["order_flag"]))

        return filtered_meals

    async def delete_planned_meal(
        self,
        entry_id: Optional[str] = None,
        date: Optional[str] = None,
        meal_type: Optional[str] = None,
        recipe_name_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Delete a planned meal from the Paprika meal planner.

        Args:
            entry_id: UID of the planned meal entry (from get_plan).
            date: Date in YYYY-MM-DD format to match planned meals.
            meal_type: Optional meal type (breakfast, lunch, dinner, snack).
            recipe_name_or_id: Optional recipe name or UID to narrow down matching.

        Returns:
            Dict with deleted meal info: uid, name, date, meal_type, recipe_uid.

        Raises:
            InvalidArgumentError: If arguments are missing or invalid, or no match found.
            AmbiguousMatchError: If multiple planned meals match the criteria.
            PaprikaAPIError: On network or auth failures.
        """
        if not entry_id and not date:
            raise InvalidArgumentError(
                "Please provide either 'entry_id' or 'date' to identify the planned meal to delete."
            )

        res = await self._make_authenticated_request("GET", "/sync/meals")
        raw_meals = res.get("result", [])
        active_meals = [m for m in raw_meals if not m.get("deleted", False)]

        matched_meal = None
        if entry_id:
            for m in active_meals:
                if m.get("uid") == entry_id:
                    matched_meal = m
                    break
            if not matched_meal:
                raise InvalidArgumentError(f"No active planned meal found with entry ID '{entry_id}'.")
        else:
            try:
                from datetime import datetime as _dt
                _dt.strptime(date, "%Y-%m-%d")
            except (ValueError, TypeError):
                raise InvalidArgumentError(
                    f"Invalid date '{date}'. Use YYYY-MM-DD format (e.g. 2026-07-05)."
                )

            candidates = [m for m in active_meals if (m.get("date") or "")[:10] == date]

            if meal_type:
                mt_clean = meal_type.lower().strip()
                if mt_clean not in MEAL_TYPE_MAP:
                    raise InvalidArgumentError(
                        f"Unknown meal type '{meal_type}'. Use one of: {', '.join(MEAL_TYPE_MAP)}."
                    )
                type_int = MEAL_TYPE_MAP[mt_clean]
                candidates = [m for m in candidates if m.get("type") == type_int]

            if recipe_name_or_id:
                r_query = recipe_name_or_id.lower().strip()
                candidates = [
                    m for m in candidates
                    if m.get("recipe_uid") == recipe_name_or_id
                    or r_query in (m.get("name") or "").lower()
                ]

            if not candidates:
                descr = f"on {date}"
                if meal_type:
                    descr += f" ({meal_type})"
                if recipe_name_or_id:
                    descr += f" for '{recipe_name_or_id}'"
                raise InvalidArgumentError(f"No active planned meal found {descr}.")

            if len(candidates) > 1:
                summary = ", ".join(
                    f"'{m.get('name')}' (id: {m.get('uid')}, type: {INV_MEAL_TYPE_MAP.get(m.get('type', 2), 'dinner')})"
                    for m in candidates
                )
                raise AmbiguousMatchError(
                    f"Multiple planned meals found on {date}: {summary}. Please specify the entry_id or meal_type.",
                    candidates=[
                        {
                            "entry_id": m.get("uid"),
                            "name": m.get("name"),
                            "meal_type": INV_MEAL_TYPE_MAP.get(m.get("type", 2), "dinner"),
                        }
                        for m in candidates
                    ],
                )

            matched_meal = candidates[0]

        matched_meal["deleted"] = True
        gzipped_data = self._gzip_json([matched_meal])
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")
        await self._make_authenticated_request("POST", "/sync/meals", data=data)

        m_date = (matched_meal.get("date") or "")[:10]
        m_type = INV_MEAL_TYPE_MAP.get(matched_meal.get("type", 2), "dinner")
        logger.info(f"Deleted planned meal {matched_meal.get('uid')} ({matched_meal.get('name')} on {m_date})")

        return {
            "uid": matched_meal.get("uid"),
            "name": matched_meal.get("name"),
            "date": m_date,
            "meal_type": m_type,
            "recipe_uid": matched_meal.get("recipe_uid"),
        }

    async def move_planned_meal(
        self,
        to_date: str,
        entry_id: Optional[str] = None,
        date: Optional[str] = None,
        meal_type: Optional[str] = None,
        to_meal_type: Optional[str] = None,
        recipe_name_or_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Move or reschedule a planned meal to another date or meal slot.
        """
        try:
            from datetime import datetime as _dt
            _dt.strptime(to_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            raise InvalidArgumentError(
                f"Invalid destination date '{to_date}'. Use YYYY-MM-DD format (e.g. 2026-07-05)."
            )

        new_type_int = None
        if to_meal_type:
            tmt_clean = to_meal_type.lower().strip()
            if tmt_clean not in MEAL_TYPE_MAP:
                raise InvalidArgumentError(
                    f"Unknown destination meal type '{to_meal_type}'. Use one of: {', '.join(MEAL_TYPE_MAP)}."
                )
            new_type_int = MEAL_TYPE_MAP[tmt_clean]

        if not entry_id and not date:
            raise InvalidArgumentError(
                "Please provide either 'entry_id' or 'date' to identify the planned meal to move."
            )

        res = await self._make_authenticated_request("GET", "/sync/meals")
        raw_meals = res.get("result", [])
        active_meals = [m for m in raw_meals if not m.get("deleted", False)]

        matched_meal = None
        if entry_id:
            for m in active_meals:
                if m.get("uid") == entry_id:
                    matched_meal = m
                    break
            if not matched_meal:
                raise InvalidArgumentError(f"No active planned meal found with entry ID '{entry_id}'.")
        else:
            try:
                from datetime import datetime as _dt
                _dt.strptime(date, "%Y-%m-%d")
            except (ValueError, TypeError):
                raise InvalidArgumentError(
                    f"Invalid source date '{date}'. Use YYYY-MM-DD format (e.g. 2026-07-05)."
                )

            candidates = [m for m in active_meals if (m.get("date") or "")[:10] == date]
            if meal_type:
                mt_clean = meal_type.lower().strip()
                if mt_clean not in MEAL_TYPE_MAP:
                    raise InvalidArgumentError(
                        f"Unknown meal type '{meal_type}'. Use one of: {', '.join(MEAL_TYPE_MAP)}."
                    )
                type_int = MEAL_TYPE_MAP[mt_clean]
                candidates = [m for m in candidates if m.get("type") == type_int]

            if recipe_name_or_id:
                r_query = recipe_name_or_id.lower().strip()
                candidates = [
                    m for m in candidates
                    if m.get("recipe_uid") == recipe_name_or_id
                    or r_query in (m.get("name") or "").lower()
                ]

            if not candidates:
                descr = f"on {date}"
                if meal_type:
                    descr += f" ({meal_type})"
                if recipe_name_or_id:
                    descr += f" for '{recipe_name_or_id}'"
                raise InvalidArgumentError(f"No active planned meal found {descr}.")

            if len(candidates) > 1:
                summary = ", ".join(
                    f"'{m.get('name')}' (id: {m.get('uid')}, type: {INV_MEAL_TYPE_MAP.get(m.get('type', 2), 'dinner')})"
                    for m in candidates
                )
                raise AmbiguousMatchError(
                    f"Multiple planned meals found on {date}: {summary}. Please specify the entry_id or meal_type.",
                    candidates=[
                        {
                            "entry_id": m.get("uid"),
                            "name": m.get("name"),
                            "meal_type": INV_MEAL_TYPE_MAP.get(m.get("type", 2), "dinner"),
                        }
                        for m in candidates
                    ],
                )

            matched_meal = candidates[0]

        matched_meal["date"] = f"{to_date} 00:00:00"
        if new_type_int is not None:
            matched_meal["type"] = new_type_int
        matched_meal["deleted"] = False

        gzipped_data = self._gzip_json([matched_meal])
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")
        await self._make_authenticated_request("POST", "/sync/meals", data=data)

        final_type = INV_MEAL_TYPE_MAP.get(matched_meal.get("type", 2), "dinner")
        logger.info(f"Moved planned meal {matched_meal.get('uid')} to {to_date} ({final_type})")

        return {
            "uid": matched_meal.get("uid"),
            "name": matched_meal.get("name"),
            "date": to_date,
            "meal_type": final_type,
            "recipe_uid": matched_meal.get("recipe_uid"),
        }

    async def update_grocery_item(
        self,
        item_name_or_id: str,
        aisle: Optional[str] = None,
        name: Optional[str] = None,
        quantity: Optional[str] = None,
        instruction: Optional[str] = None,
        list_name_or_id: Optional[str] = None,
        purchased: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing grocery item in Paprika (aisle, name, quantity, instruction, list, purchased).

        Args:
            item_name_or_id: UID or name of the grocery item to update.
            aisle: New aisle/category name.
            name: New display name.
            quantity: New quantity string.
            instruction: New instruction string.
            list_name_or_id: Target grocery list to move the item to.
            purchased: Mark purchased (True) or active (False).

        Returns:
            Dict of the updated item.
        """
        groceries = await self.get_groceries(include_purchased=True)
        list_names = {
            lst["uid"]: lst.get("name", "unknown list")
            for lst in await self.get_grocery_lists()
        }
        item = self._resolve_strict(
            item_name_or_id, groceries,
            name_key="name", id_key="uid",
            list_names=list_names,
        )

        if list_name_or_id:
            target_list_uid = await self._resolve_list_uid_strict(list_name_or_id)
            item["list_uid"] = target_list_uid
        if name is not None:
            item["name"] = name
            item["ingredient"] = name
        if aisle is not None:
            item["aisle"] = aisle
        if quantity is not None:
            item["quantity"] = quantity
        if instruction is not None:
            item["instruction"] = instruction
        if purchased is not None:
            item["purchased"] = purchased

        gzipped_data = self._gzip_json([item])
        data = aiohttp.FormData()
        data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")
        await self._make_authenticated_request("POST", "/sync/groceries", data=data)

        if aisle and self._aisle_cache is not None:
            self._aisle_cache[(item.get("name") or "").lower()] = aisle

        logger.info(f"Updated grocery item: {item['uid']} ({item.get('name')})")
        return {
            "uid": item["uid"],
            "name": item.get("name"),
            "aisle": item.get("aisle"),
            "quantity": item.get("quantity"),
            "instruction": item.get("instruction"),
            "list_uid": item.get("list_uid"),
            "purchased": item.get("purchased"),
        }

    async def update_grocery_items(
        self,
        updates: List[Dict[str, Any]],
        list_name_or_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Bulk update multiple grocery items in a single POST request.

        Args:
            updates: List of dicts, each specifying:
                - ``item_name_or_id`` (or ``uid``/``name``): str
                - ``aisle``: Optional[str]
                - ``name``: Optional[str]
                - ``quantity``: Optional[str]
                - ``instruction``: Optional[str]
                - ``purchased``: Optional[bool]
                - ``list_name_or_id``: Optional[str]
            list_name_or_id: Optional list name or UID applied to all updates that don't specify one.

        Returns:
            List of updated item dicts.
        """
        if not updates:
            return []

        groceries = await self.get_groceries(include_purchased=True)
        list_names = {
            lst["uid"]: lst.get("name", "unknown list")
            for lst in await self.get_grocery_lists()
        }

        default_target_list_uid = None
        if list_name_or_id:
            default_target_list_uid = await self._resolve_list_uid_strict(list_name_or_id)

        updated_items = []
        results = []

        for u in updates:
            query = u.get("item_name_or_id") or u.get("uid") or u.get("name")
            if not query:
                continue
            item = self._resolve_strict(
                query, groceries,
                name_key="name", id_key="uid",
                list_names=list_names,
            )

            if u.get("list_name_or_id"):
                item["list_uid"] = await self._resolve_list_uid_strict(u["list_name_or_id"])
            elif default_target_list_uid:
                item["list_uid"] = default_target_list_uid

            if u.get("name") is not None:
                item["name"] = u["name"]
                item["ingredient"] = u["name"]
            if u.get("aisle") is not None:
                item["aisle"] = u["aisle"]
            if u.get("quantity") is not None:
                item["quantity"] = u["quantity"]
            if u.get("instruction") is not None:
                item["instruction"] = u["instruction"]
            if u.get("purchased") is not None:
                item["purchased"] = u["purchased"]

            updated_items.append(item)
            results.append({
                "uid": item["uid"],
                "name": item.get("name"),
                "aisle": item.get("aisle"),
                "quantity": item.get("quantity"),
                "instruction": item.get("instruction"),
                "list_uid": item.get("list_uid"),
                "purchased": item.get("purchased"),
            })

            if item.get("aisle") and self._aisle_cache is not None:
                self._aisle_cache[(item.get("name") or "").lower()] = item["aisle"]

        if updated_items:
            gzipped_data = self._gzip_json(updated_items)
            data = aiohttp.FormData()
            data.add_field("data", gzipped_data, content_type="application/octet-stream", filename="data.gz")
            await self._make_authenticated_request("POST", "/sync/groceries", data=data)
            logger.info(f"Bulk updated {len(updated_items)} grocery items.")

        return results

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
