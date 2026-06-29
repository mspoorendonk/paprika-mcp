"""
Paprika MCP server.

Exposes recipe + grocery tools for voice agents (or any MCP client) backed by a
Paprika account:

  - create_recipe / update_recipe / update_recipe_partial
  - list_recipes
  - get_groceries / add_grocery_item / remove_grocery_item
  - GetUsageStats (audit read-back)

Architecture mirrors movie-mcp (see system-administration/mcp-on-lan/specs.md):
high-level FastMCP with `@mcp.tool()` + `@audit.audited(...)` decorators, and a
single process that serves two HTTP listeners (OAuth behind nginx + an optional
unauthenticated LAN listener for Home Assistant). See §5.1.

Transports:
  - stdio (default) for local / MCP Inspector use
  - Streamable HTTP (--http) for remote access behind a reverse proxy

In HTTP mode the primary listener is an OAuth 2.0 Authorization Server
(src/oauth_app.py, Google-delegated login). Add --lan-host/--lan-port for the
no-auth LAN listener; --no-auth makes the primary listener itself unauthenticated.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field

import audit
import oauth_app
from config import get_config
from paprika_client import (
    PaprikaAPIError,
    PaprikaClient,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Whether we run as an HTTP server; decides if FastMCP owns the stdio lifespan
# (data warm-up) or _serve_http() does (so both listeners share one warm-up).
_HTTP_MODE = "--http" in sys.argv

# The Paprika client is created once in run_server() (it needs credentials from
# config) and shared by every tool. Authentication is lazy: the first tool call
# triggers it, so the server stays responsive even if Paprika is down at start.
_client: PaprikaClient | None = None


@contextlib.asynccontextmanager
async def _stdio_lifespan(server):
    """stdio-mode lifespan: warm the recipe cache, close the client on exit."""
    warmup = asyncio.create_task(_client.warm_up_cache())
    try:
        yield
    finally:
        warmup.cancel()
        try:
            await warmup
        except (asyncio.CancelledError, Exception):
            pass
        await _client.close()


mcp = FastMCP("Paprika",
    lifespan=None if _HTTP_MODE else _stdio_lifespan,
)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def _error_result(exc: PaprikaAPIError, tool_name: str) -> CallToolResult:
    """Map a typed PaprikaAPIError to a voice-assistant-friendly tool result.

    `isError=True` tells the LLM this was a failure (so it can branch instead of
    treating the message as success). The stable category code goes in
    `structuredContent.code`; any extra context (candidates, available_lists, …)
    is merged alongside. FastMCP passes a returned CallToolResult through
    unchanged, so this preserves the same contract the low-level server gave.
    """
    structured = {"code": exc.code, **exc.extra}
    logger.warning("Tool %s returned %s: %s", tool_name, exc.code, exc)
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=str(exc))],
        structuredContent=structured,
    )


def _tool_errors(fn):
    """Decorator: turn typed PaprikaAPIErrors into structured isError results.

    Wraps a tool body so any PaprikaAPIError (auth, unreachable, rate-limited,
    not-found, ambiguous, invalid-argument, generic) becomes an isError=True
    result, and any genuinely unexpected exception becomes a generic one without
    leaking a stack trace. See specs.md §9 (error contract).
    """
    from functools import wraps

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except PaprikaAPIError as e:
            return _error_result(e, fn.__name__)
        except Exception:
            logger.exception("Unexpected error in tool %s", fn.__name__)
            return _error_result(
                PaprikaAPIError(
                    "Something went wrong on my side. I've logged the details."
                ),
                fn.__name__,
            )

    return wrapper


def _client_or_raise() -> PaprikaClient:
    if _client is None:  # pragma: no cover - only if a tool runs before startup
        raise PaprikaAPIError("The recipe service isn't ready yet.")
    return _client


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
@audit.audited("create_recipe")
@_tool_errors
async def create_recipe(
    name: Annotated[str, Field(description="The name of the recipe")],
    ingredients: Annotated[str, Field(description="Recipe ingredients, one per line")],
    directions: Annotated[str, Field(description="Cooking directions/instructions")],
    description: Annotated[str, Field(description="Optional recipe description")] = "",
    notes: Annotated[str, Field(description="Optional cooking notes")] = "",
    servings: Annotated[str, Field(description="Number of servings")] = "",
    prep_time: Annotated[str, Field(description="Preparation time (e.g. '15 mins')")] = "",
    cook_time: Annotated[str, Field(description="Cooking time (e.g. '30 mins')")] = "",
    difficulty: Annotated[str, Field(description="Difficulty level (Easy, Medium, Hard)")] = "",
):
    """Create a new recipe in Paprika."""
    result = await _client_or_raise().create_recipe(
        name=name, ingredients=ingredients, directions=directions,
        description=description, notes=notes, servings=servings,
        prep_time=prep_time, cook_time=cook_time, difficulty=difficulty,
    )
    return f"Successfully created recipe '{result['name']}' with UID: {result['uid']}"


@mcp.tool()
@audit.audited("update_recipe")
@_tool_errors
async def update_recipe(
    uid: Annotated[str, Field(description="The UID of the recipe to update")],
    name: Annotated[str, Field(description="The name of the recipe")],
    ingredients: Annotated[str, Field(description="Recipe ingredients, one per line")],
    directions: Annotated[str, Field(description="Cooking directions/instructions")],
    description: Annotated[str, Field(description="Recipe description")] = "",
    notes: Annotated[str, Field(description="Cooking notes")] = "",
    servings: Annotated[str, Field(description="Number of servings")] = "",
    prep_time: Annotated[str, Field(description="Preparation time")] = "",
    cook_time: Annotated[str, Field(description="Cooking time")] = "",
    difficulty: Annotated[str, Field(description="Difficulty level")] = "",
):
    """Update an existing recipe in Paprika (full replace of the given fields)."""
    result = await _client_or_raise().update_recipe(
        uid=uid, name=name, ingredients=ingredients, directions=directions,
        description=description, notes=notes, servings=servings,
        prep_time=prep_time, cook_time=cook_time, difficulty=difficulty,
    )
    return f"Successfully updated recipe '{result['name']}'"


@mcp.tool()
@audit.audited("update_recipe_partial")
@_tool_errors
async def update_recipe_partial(
    uid: Annotated[str, Field(description="The UID of the recipe to update")],
    name: Annotated[Optional[str], Field(description="The name of the recipe")] = None,
    ingredients: Annotated[Optional[str], Field(description="Recipe ingredients, one per line")] = None,
    directions: Annotated[Optional[str], Field(description="Cooking directions/instructions")] = None,
    description: Annotated[Optional[str], Field(description="Recipe description")] = None,
    notes: Annotated[Optional[str], Field(description="Cooking notes")] = None,
    servings: Annotated[Optional[str], Field(description="Number of servings")] = None,
    prep_time: Annotated[Optional[str], Field(description="Preparation time")] = None,
    cook_time: Annotated[Optional[str], Field(description="Cooking time")] = None,
    difficulty: Annotated[Optional[str], Field(description="Difficulty level")] = None,
):
    """Partially update an existing recipe in Paprika (only the fields given)."""
    fields = {
        k: v
        for k, v in {
            "name": name, "ingredients": ingredients, "directions": directions,
            "description": description, "notes": notes, "servings": servings,
            "prep_time": prep_time, "cook_time": cook_time, "difficulty": difficulty,
        }.items()
        if v is not None
    }
    result = await _client_or_raise().update_recipe_partial(uid=uid, **fields)
    return f"Successfully updated recipe '{result['name']}' (partial update)"


@mcp.tool()
@audit.audited("list_recipes")
@_tool_errors
async def list_recipes(
    limit: Annotated[int, Field(description="Maximum number of recipes to return")] = 50,
):
    """List recipes from Paprika with their basic information."""
    recipes = await _client_or_raise().list_recipes(limit=limit)
    if not recipes:
        return "No recipes found in your Paprika account."

    recipe_text = f"Found {len(recipes)} recipes:\n\n"
    for recipe in recipes:
        recipe_text += f"• **{recipe['name']}**\n"
        recipe_text += f"  UID: {recipe['uid']}\n"
        if recipe.get("description"):
            recipe_text += f"  Description: {recipe['description']}\n"
        if recipe.get("servings"):
            recipe_text += f"  Servings: {recipe['servings']}\n"
        if recipe.get("prep_time") or recipe.get("cook_time"):
            times = []
            if recipe.get("prep_time"):
                times.append(f"Prep: {recipe['prep_time']}")
            if recipe.get("cook_time"):
                times.append(f"Cook: {recipe['cook_time']}")
            recipe_text += f"  Time: {', '.join(times)}\n"
        if recipe.get("ingredients"):
            ingredients_preview = "\n    ".join(recipe["ingredients"].split("\n"))
            recipe_text += f"  Ingredients:\n    {ingredients_preview}\n"
        recipe_text += "\n"
    return recipe_text


@mcp.tool()
@audit.audited("get_groceries")
@_tool_errors
async def get_groceries(
    include_purchased: Annotated[bool, Field(
        description="Set true to also include items already checked off as "
                    "purchased. Defaults to false (unchecked items only, since "
                    "the list accumulates hundreds of checked-off items).",
    )] = False,
):
    """List grocery items on the Paprika grocery list (unchecked by default)."""
    groceries = await _client_or_raise().get_groceries(include_purchased=include_purchased)
    if not groceries:
        return (
            "No groceries found in your Paprika account."
            if include_purchased
            else "No unchecked groceries on your Paprika list."
        )

    header = (
        f"Found {len(groceries)} groceries"
        if include_purchased
        else f"Found {len(groceries)} unchecked groceries"
    )
    grocery_text = f"{header}:\n\n"
    for item in groceries:
        purchased_mark = "[x]" if item.get("purchased") else "[ ]"
        grocery_text += f"{purchased_mark} **{item.get('name', 'Unknown')}**\n"
        grocery_text += f"  UID: {item.get('uid')}\n"
        grocery_text += f"  List UID: {item.get('list_uid')}\n"
        if item.get("quantity"):
            grocery_text += f"  Quantity: {item.get('quantity')}\n"
        if item.get("aisle"):
            grocery_text += f"  Aisle: {item.get('aisle')}\n"
        grocery_text += "\n"
    return grocery_text


@mcp.tool()
@audit.audited("add_grocery_item")
@_tool_errors
async def add_grocery_item(
    name: Annotated[str, Field(description="The display name of the grocery item")],
    ingredient: Annotated[str, Field(description="The matched ingredient name (usually identical to name)")],
    quantity: Annotated[str, Field(description="Quantity info (e.g. '1', '500g', '2 cups')")] = "",
    instruction: Annotated[str, Field(description="Additional instructions for the item")] = "",
    aisle: Annotated[str, Field(description="Grocery section/aisle")] = "",
    list_name_or_id: Annotated[str, Field(description="Name or ID of the list to add to (default list if omitted)")] = "",
):
    """Add a new item to the Paprika grocery list."""
    result = await _client_or_raise().add_grocery_item(
        name=name, ingredient=ingredient, quantity=quantity,
        instruction=instruction, aisle=aisle,
        list_name_or_id=list_name_or_id or None,
    )
    return (
        f"Successfully created grocery '{result['name']}' with UID: "
        f"{result['uid']} on list: {result.get('list_uid')}"
    )


@mcp.tool()
@audit.audited("remove_grocery_item")
@_tool_errors
async def remove_grocery_item(
    item_name_or_id: Annotated[str, Field(
        description="The UID or name of the grocery item to remove (exact UID, "
                    "exact name, or unambiguous substring)",
    )],
    list_name_or_id: Annotated[Optional[str], Field(
        description="Name or ID of the list to confine the search to "
                    "(searches all lists if omitted)",
    )] = None,
):
    """Remove a grocery item from the active shopping list by checking it off.

    The item is NOT permanently deleted — it stays in the list's purchased/
    history section so the user can un-check or re-add it later. This is what
    users mean by "remove from my shopping list". Only currently-unpurchased
    items are considered. Searches across all the user's grocery lists by
    default; pass `list_name_or_id` to confine the search. The matcher is
    conservative: it requires an exact UID, case-insensitive exact name, or
    unambiguous substring match. If multiple items match, the call returns an
    error listing candidates so you can disambiguate by UID.
    """
    removed = await _client_or_raise().remove_grocery_item(
        item_name_or_id=item_name_or_id,
        list_name_or_id=list_name_or_id,
    )
    return (
        f"Marked '{removed['name']}' (UID {removed['uid']}) as purchased on list "
        f"{removed['list_uid']}. The item remains in Paprika's purchased history "
        f"and can be un-checked or re-added later."
    )


@mcp.tool()
def GetUsageStats() -> dict:
    """Usage aggregates from the audit log: calls per client, per tool, per day,
    error rate, and last-seen per client."""
    return audit.stats()


# ---------------------------------------------------------------------------
# HTTP serving (dual listener) — see specs.md §5.1
# ---------------------------------------------------------------------------

async def _serve_http() -> None:
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.types import Receive, Scope, Send

    host = "0.0.0.0"
    port = 8000
    lan_host = None
    lan_port = None
    no_auth = "--no-auth" in sys.argv
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
        elif arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass
        elif arg == "--lan-host" and i + 1 < len(sys.argv):
            lan_host = sys.argv[i + 1]
        elif arg == "--lan-port" and i + 1 < len(sys.argv):
            try:
                lan_port = int(sys.argv[i + 1])
            except ValueError:
                pass

    mcp_server = mcp._mcp_server

    # Streamable HTTP transport — single stateless `/mcp` endpoint.
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=True,
        json_response=False,
    )

    # OAuth2 AS with Google-delegated login (src/oauth_app.py). nginx strips the
    # public /paprika prefix, so we serve at root paths. Built only if some
    # listener requires auth.
    primary_auth = not no_auth
    if primary_auth:
        oauth_provider = oauth_app.PersistentOAuthProvider()
        oauth_sub_app = oauth_app.build_oauth_app(oauth_provider)
    else:
        oauth_provider = None
        oauth_sub_app = None

    async def _send_401(send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate",
                 f'Bearer resource_metadata="{oauth_app.PRM_URL}"'.encode()),
                (b"content-type", b"text/plain; charset=utf-8"),
            ],
        })
        await send({"type": "http.response.body", "body": b"Unauthorized - OAuth bearer token required"})

    async def _send_404(send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        })
        await send({"type": "http.response.body", "body": b"Not found"})

    async def _serve_mcp_noauth(scope: Scope, receive: Receive, send: Send) -> None:
        oauth_app.set_identity(None, "LAN (no-auth)")
        await session_manager.handle_request(scope, receive, send)

    async def _serve_mcp_auth(scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            at = await oauth_provider.load_access_token(token)
            if at is not None:
                oauth_app.set_identity(
                    oauth_provider.email_for_token(token),
                    oauth_provider.client_name(at.client_id),
                )
                await session_manager.handle_request(scope, receive, send)
                return
        await _send_401(send)

    async def _handle_lifespan(receive: Receive, send: Send) -> None:
        # Minimal ASGI lifespan: the session manager and cache warm-up are owned
        # by the serve loop below, not per-app, so both listeners share one
        # StreamableHTTPSessionManager and one warm-up.
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    def make_app(require_auth: bool):
        # /mcp is dispatched directly (no Starlette Mount) to avoid trailing-slash
        # redirects that break clients POSTing without one.
        async def app(scope: Scope, receive: Receive, send: Send):
            if scope["type"] == "lifespan":
                await _handle_lifespan(receive, send)
                return

            path = scope.get("path", "")
            is_mcp = path in (oauth_app.MCP_PATH, oauth_app.MCP_PATH + "/")

            if not require_auth:
                if is_mcp:
                    await _serve_mcp_noauth(scope, receive, send)
                else:
                    await _send_404(send)
                return

            if is_mcp:
                await _serve_mcp_auth(scope, receive, send)
                return

            # /.well-known/*, /authorize, /oauth/google/callback, /token, /register, /revoke
            await oauth_sub_app(scope, receive, send)

        return app

    async with contextlib.AsyncExitStack() as stack:
        # One cache warm-up + one session manager for the whole process.
        warmup_task = asyncio.create_task(_client.warm_up_cache())
        stack.callback(warmup_task.cancel)
        stack.push_async_callback(_client.close)
        await stack.enter_async_context(session_manager.run())

        listeners = [(make_app(primary_auth), host, port)]
        if lan_host is not None and lan_port is not None:
            listeners.append((make_app(False), lan_host, lan_port))

        logger.info(
            "Starting MCP HTTP server: primary %s:%d (%s)%s",
            host, port,
            "OAuth" if primary_auth else "no-auth",
            f", LAN no-auth {lan_host}:{lan_port}" if lan_host and lan_port else "",
        )
        servers = [
            uvicorn.Server(uvicorn.Config(a, host=h, port=p, log_level="info"))
            for a, h, p in listeners
        ]
        await asyncio.gather(*(s.serve() for s in servers))


def run_server():
    """Synchronous entry point for the console script (`paprika-mcp`)."""
    global _client
    try:
        config = get_config()
        _client = PaprikaClient(
            username=config.paprika_username, password=config.paprika_password
        )
        if _HTTP_MODE:
            # _serve_http owns the loop: both OAuth and the optional no-auth LAN
            # listener share one event loop, session manager and cache warm-up.
            asyncio.run(_serve_http())
        else:
            # FastMCP owns the stdio loop + lifespan (cache warm-up / client close).
            mcp.run()
    except Exception:
        # Non-zero exit + visible traceback in the journal for a misconfigured
        # environment, instead of silently "succeeding".
        logger.exception("Server startup failed")
        raise


if __name__ == "__main__":
    run_server()
