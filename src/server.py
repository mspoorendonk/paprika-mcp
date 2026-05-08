import asyncio
import logging
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from config import get_config
from paprika_client import (
    AmbiguousMatchError,
    InvalidArgumentError,
    PaprikaAPIError,
    PaprikaClient,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize server
server = Server("paprika-mcp-python")


def _error_result(exc: PaprikaAPIError, tool_name: str) -> CallToolResult:
    """Map a typed PaprikaAPIError to a voice-assistant-friendly tool result.

    The MCP `isError=True` flag tells the LLM this was a failure (so it can
    branch instead of treating the message as success). The stable category
    code goes in `structuredContent.code`; any extra context the exception
    carried (candidates, available_lists, ...) is merged in alongside.
    """
    structured = {"code": exc.code, **exc.extra}
    logger.warning("Tool %s returned %s: %s", tool_name, exc.code, exc)
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=str(exc))],
        structuredContent=structured,
    )


def _require(arguments: dict, *names: str) -> None:
    """Raise InvalidArgumentError if any of `names` is missing or empty."""
    missing = [n for n in names if not arguments.get(n)]
    if missing:
        if len(missing) == 1:
            msg = f"I need a {missing[0]} for that."
        else:
            msg = f"I'm missing some details: {', '.join(missing)}."
        raise InvalidArgumentError(msg, missing=missing)


async def main():
    """Main entry point for the MCP server."""
    paprika_client = None
    try:
        config = get_config()

        # Initialize Paprika client. Authentication is lazy: the first tool
        # call will trigger it. This keeps the MCP server responsive even if
        # Paprika is temporarily down at startup, and lets us surface a
        # friendly `paprika_auth_failed` / `paprika_unreachable` error to the
        # voice assistant instead of crashing.
        paprika_client = PaprikaClient(
            username=config.paprika_username, password=config.paprika_password
        )

        # Define tools
        @server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            """List available tools."""
            return [
                Tool(
                    name="create_recipe",
                    description="Create a new recipe in Paprika",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The name of the recipe",
                            },
                            "ingredients": {
                                "type": "string",
                                "description": "Recipe ingredients, one per line",
                            },
                            "directions": {
                                "type": "string",
                                "description": "Cooking directions/instructions",
                            },
                            "description": {
                                "type": "string",
                                "description": "Optional recipe description",
                                "default": "",
                            },
                            "notes": {
                                "type": "string",
                                "description": "Optional cooking notes",
                                "default": "",
                            },
                            "servings": {
                                "type": "string",
                                "description": "Number of servings",
                                "default": "",
                            },
                            "prep_time": {
                                "type": "string",
                                "description": "Preparation time (e.g., '15 mins')",
                                "default": "",
                            },
                            "cook_time": {
                                "type": "string",
                                "description": "Cooking time (e.g., '30 mins')",
                                "default": "",
                            },
                            "difficulty": {
                                "type": "string",
                                "description": "Difficulty level (Easy, Medium, Hard)",
                                "default": "",
                            },
                        },
                        "required": ["name", "ingredients", "directions"],
                    },
                ),
                Tool(
                    name="update_recipe",
                    description="Update an existing recipe in Paprika",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "uid": {
                                "type": "string",
                                "description": "The UID of the recipe to update",
                            },
                            "name": {
                                "type": "string",
                                "description": "The name of the recipe",
                            },
                            "ingredients": {
                                "type": "string",
                                "description": "Recipe ingredients, one per line",
                            },
                            "directions": {
                                "type": "string",
                                "description": "Cooking directions/instructions",
                            },
                            "description": {
                                "type": "string",
                                "description": "Recipe description",
                                "default": "",
                            },
                            "notes": {
                                "type": "string",
                                "description": "Cooking notes",
                                "default": "",
                            },
                            "servings": {
                                "type": "string",
                                "description": "Number of servings",
                                "default": "",
                            },
                            "prep_time": {
                                "type": "string",
                                "description": "Preparation time",
                                "default": "",
                            },
                            "cook_time": {
                                "type": "string",
                                "description": "Cooking time",
                                "default": "",
                            },
                            "difficulty": {
                                "type": "string",
                                "description": "Difficulty level",
                                "default": "",
                            },
                        },
                        "required": ["uid", "name", "ingredients", "directions"],
                    },
                ),
                Tool(
                    name="update_recipe_partial",
                    description="Partially update an existing recipe in Paprika (only specified fields)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "uid": {
                                "type": "string",
                                "description": "The UID of the recipe to update",
                            },
                            "name": {
                                "type": "string",
                                "description": "The name of the recipe (optional)",
                            },
                            "ingredients": {
                                "type": "string",
                                "description": "Recipe ingredients, one per line (optional)",
                            },
                            "directions": {
                                "type": "string",
                                "description": "Cooking directions/instructions (optional)",
                            },
                            "description": {
                                "type": "string",
                                "description": "Recipe description (optional)",
                            },
                            "notes": {
                                "type": "string",
                                "description": "Cooking notes (optional)",
                            },
                            "servings": {
                                "type": "string",
                                "description": "Number of servings (optional)",
                            },
                            "prep_time": {
                                "type": "string",
                                "description": "Preparation time (optional)",
                            },
                            "cook_time": {
                                "type": "string",
                                "description": "Cooking time (optional)",
                            },
                            "difficulty": {
                                "type": "string",
                                "description": "Difficulty level (optional)",
                            },
                        },
                        "required": ["uid"],
                    },
                ),
                Tool(
                    name="list_recipes",
                    description="List all recipes from Paprika with their basic information",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of recipes to return (default: 50)",
                                "default": 50,
                            }
                        },
                    },
                ),
                Tool(
                    name="get_groceries",
                    description=(
                        "List grocery items currently on the Paprika grocery "
                        "list. By default, only unchecked (not yet purchased) "
                        "items are returned, since the list typically "
                        "accumulates hundreds of checked-off items over time."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "include_purchased": {
                                "type": "boolean",
                                "description": (
                                    "Set to true to also include items that "
                                    "have already been checked off as "
                                    "purchased. Defaults to false."
                                ),
                                "default": False,
                            }
                        },
                    },
                ),
                Tool(
                    name="add_grocery_item",
                    description="Add a new item to the Paprika grocery list",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The display name of the grocery item",
                            },
                            "ingredient": {
                                "type": "string",
                                "description": "The matched ingredient name (usually identical to name)",
                            },
                            "quantity": {
                                "type": "string",
                                "description": "Quantity info (e.g., '1', '500g', '2 cups')",
                                "default": "",
                            },
                            "instruction": {
                                "type": "string",
                                "description": "Additional instructions for the item",
                                "default": "",
                            },
                            "aisle": {
                                "type": "string",
                                "description": "Grocery section/aisle",
                                "default": "",
                            },
                            "list_name_or_id": {
                                "type": "string",
                                "description": "Name or ID of the list to add to (uses default if omitted)",
                                "default": "",
                            },
                        },
                        "required": ["name", "ingredient"],
                    },
                ),
                Tool(
                    name="remove_grocery_item",
                    description="Remove a grocery item from the active shopping list by marking it as purchased (checked off). The item is NOT permanently deleted from Paprika — it stays in the list's purchased/history section so the user can un-check or re-add it later. This is what users mean by \"remove from my shopping list\". Only currently-unpurchased items are considered for matching. Searches across all the user's grocery lists by default; pass `list_name_or_id` to confine the search. The matcher is conservative for safety: it requires an exact UID, case-insensitive exact name, or unambiguous substring match. If multiple items match, the call returns an error listing candidates so you can disambiguate by UID.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "item_name_or_id": {
                                "type": "string",
                                "description": "The UID or name of the grocery item to remove (exact UID, exact name, or unambiguous substring)",
                            },
                            "list_name_or_id": {
                                "type": "string",
                                "description": "Name or ID of the list to confine the search to (searches all lists if omitted)",
                            }
                        },
                        "required": ["item_name_or_id"],
                    },
                ),
            ]

        @server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict[str, Any]
        ):
            """Dispatch a tool call.

            Returns either a list[TextContent] (success) or a CallToolResult
            with isError=True and structuredContent.code populated (failure).
            See specs.md "Scenario 9 — Errors the user should hear in plain
            language" for the contract the LLM relies on.
            """
            try:
                if name == "create_recipe":
                    _require(arguments, "name", "ingredients", "directions")
                    result = await paprika_client.create_recipe(
                        name=arguments["name"],
                        ingredients=arguments["ingredients"],
                        directions=arguments["directions"],
                        description=arguments.get("description", ""),
                        notes=arguments.get("notes", ""),
                        servings=arguments.get("servings", ""),
                        prep_time=arguments.get("prep_time", ""),
                        cook_time=arguments.get("cook_time", ""),
                        difficulty=arguments.get("difficulty", ""),
                    )
                    return [
                        TextContent(
                            type="text",
                            text=f"Successfully created recipe '{result['name']}' with UID: {result['uid']}",
                        )
                    ]

                elif name == "update_recipe":
                    _require(arguments, "uid", "name", "ingredients", "directions")
                    result = await paprika_client.update_recipe(
                        uid=arguments["uid"],
                        name=arguments["name"],
                        ingredients=arguments["ingredients"],
                        directions=arguments["directions"],
                        description=arguments.get("description", ""),
                        notes=arguments.get("notes", ""),
                        servings=arguments.get("servings", ""),
                        prep_time=arguments.get("prep_time", ""),
                        cook_time=arguments.get("cook_time", ""),
                        difficulty=arguments.get("difficulty", ""),
                    )
                    return [
                        TextContent(
                            type="text",
                            text=f"Successfully updated recipe '{result['name']}'",
                        )
                    ]

                elif name == "update_recipe_partial":
                    _require(arguments, "uid")
                    result = await paprika_client.update_recipe_partial(
                        uid=arguments["uid"],
                        **{
                            k: v
                            for k, v in arguments.items()
                            if k != "uid" and v is not None
                        },
                    )
                    return [
                        TextContent(
                            type="text",
                            text=f"Successfully updated recipe '{result['name']}' (partial update)",
                        )
                    ]

                elif name == "list_recipes":
                    limit = arguments.get("limit", 50)
                    recipes = await paprika_client.list_recipes(limit=limit)

                    if not recipes:
                        return [
                            TextContent(
                                type="text",
                                text="No recipes found in your Paprika account.",
                            )
                        ]

                    # Format recipe list
                    recipe_text = f"Found {len(recipes)} recipes:\n\n"
                    for recipe in recipes:
                        # code omitted for brevity
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
                            ingredients_lines = recipe["ingredients"].split("\n")
                            ingredients_preview = "\n    ".join(ingredients_lines)
                            recipe_text += (
                                f"  Ingredients:\n    {ingredients_preview}\n"
                            )
                        recipe_text += "\n"

                    return [TextContent(type="text", text=recipe_text)]

                elif name == "get_groceries":
                    include_purchased = bool(arguments.get("include_purchased", False))
                    groceries = await paprika_client.get_groceries(
                        include_purchased=include_purchased
                    )

                    if not groceries:
                        msg = (
                            "No groceries found in your Paprika account."
                            if include_purchased
                            else "No unchecked groceries on your Paprika list."
                        )
                        return [TextContent(type="text", text=msg)]

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

                    return [TextContent(type="text", text=grocery_text)]

                elif name == "add_grocery_item":
                    _require(arguments, "name", "ingredient")
                    result = await paprika_client.add_grocery_item(
                        name=arguments["name"],
                        ingredient=arguments["ingredient"],
                        quantity=arguments.get("quantity", ""),
                        instruction=arguments.get("instruction", ""),
                        aisle=arguments.get("aisle", ""),
                        list_name_or_id=arguments.get("list_name_or_id")
                    )
                    return [
                        TextContent(
                            type="text",
                            text=f"Successfully created grocery '{result['name']}' with UID: {result['uid']} on list: {result.get('list_uid')}",
                        )
                    ]

                elif name == "remove_grocery_item":
                    _require(arguments, "item_name_or_id")
                    removed = await paprika_client.remove_grocery_item(
                        item_name_or_id=arguments["item_name_or_id"],
                        list_name_or_id=arguments.get("list_name_or_id")
                    )
                    return [
                        TextContent(
                            type="text",
                            text=f"Marked '{removed['name']}' (UID {removed['uid']}) as purchased on list {removed['list_uid']}. The item remains in Paprika's purchased history and can be un-checked or re-added later.",
                        )
                    ]

                else:
                    raise InvalidArgumentError(f"I don't know how to do '{name}'.")

            except PaprikaAPIError as e:
                # All typed errors (auth, unreachable, rate-limited, not-found,
                # ambiguous, invalid-argument, generic paprika_error) flow
                # through here and become structured isError=True results.
                return _error_result(e, name)
            except Exception as e:
                # Last-resort safety net for genuinely unexpected bugs.
                # We do NOT echo the exception text to the user (it could be
                # a stack-trace fragment); a generic message is friendlier.
                logger.exception("Unexpected error in tool %s", name)
                return _error_result(
                    PaprikaAPIError(
                        "Something went wrong on my side. I've logged the details."
                    ),
                    name,
                )

        # Parse HTTP arguments safely
        import os
        import sys
        use_http = "--http" in sys.argv or "--sse" in sys.argv
        host = "0.0.0.0"
        port = 8000
        # Optional public URL prefix for clients reaching us through a reverse
        # proxy that strips a path prefix (e.g. nginx forwarding /paprika/* ->
        # /*). The SSE transport announces an absolute path for its message
        # back-channel, so it must include the public prefix.
        base_path = os.environ.get("MCP_BASE_PATH", "")

        for i, arg in enumerate(sys.argv):
            if arg == "--host" and i + 1 < len(sys.argv):
                host = sys.argv[i + 1]
            elif arg == "--port" and i + 1 < len(sys.argv):
                try:
                    port = int(sys.argv[i + 1])
                except ValueError:
                    pass
            elif arg == "--base-path" and i + 1 < len(sys.argv):
                base_path = sys.argv[i + 1]

        if base_path and not base_path.startswith("/"):
            base_path = "/" + base_path
        base_path = base_path.rstrip("/")

        if use_http:
            import contextlib

            from mcp.server.sse import SseServerTransport
            from mcp.server.streamable_http_manager import (
                StreamableHTTPSessionManager,
            )
            from starlette.applications import Starlette
            from starlette.routing import Mount
            from starlette.types import Receive, Scope, Send

            mcp_server = server

            # Streamable HTTP transport — current MCP standard, used by
            # Claude Code (`--transport http`) and Gemini CLI (`httpUrl`).
            # Stateless mode avoids session affinity behind a reverse proxy.
            session_manager = StreamableHTTPSessionManager(
                app=mcp_server,
                stateless=True,
                json_response=False,
            )

            # Legacy SSE transport — still required by Home Assistant's MCP
            # client integration (it does not yet speak streamable HTTP).
            # The path passed to SseServerTransport is announced verbatim to
            # the client as the message endpoint, so it must reflect the
            # public URL when behind a prefix-stripping proxy.
            sse = SseServerTransport(f"{base_path}/messages/")

            async def handle_sse(scope: Scope, receive: Receive, send: Send):
                async with sse.connect_sse(scope, receive, send) as streams:
                    await mcp_server.run(
                        streams[0],
                        streams[1],
                        InitializationOptions(
                            server_name="paprika-mcp-python",
                            server_version="1.0.0",
                            capabilities=mcp_server.get_capabilities(
                                notification_options=NotificationOptions(),
                                experimental_capabilities={},
                            ),
                        ),
                    )

            async def dispatcher(scope: Scope, receive: Receive, send: Send):
                """Route requests by exact path before Starlette's Mount layer
                gets a chance to issue trailing-slash redirects on /mcp or
                /sse, which would break MCP clients that POST without a
                trailing slash."""
                if scope["type"] == "lifespan":
                    # Forwarded by Starlette to the lifespan handler below.
                    return
                path = scope.get("path", "")
                if path in ("/mcp", "/mcp/"):
                    await session_manager.handle_request(scope, receive, send)
                    return
                if path in ("/sse", "/sse/"):
                    await handle_sse(scope, receive, send)
                    return
                await starlette_app(scope, receive, send)

            @contextlib.asynccontextmanager
            async def lifespan(app):
                # Kick off the recipe cache warm-up in the background so the
                # MCP server is ready immediately for non-recipe tools while
                # the (potentially slow) full-library fetch runs.
                warmup_task = asyncio.create_task(paprika_client.warm_up_cache())
                try:
                    async with session_manager.run():
                        yield
                finally:
                    warmup_task.cancel()
                    try:
                        await warmup_task
                    except (asyncio.CancelledError, Exception):
                        pass

            # Starlette app handles /messages/* (and optionally the
            # base_path-prefixed variant) plus lifespan. Other paths are
            # dispatched directly above.
            routes = [
                Mount("/messages/", app=sse.handle_post_message),
            ]
            if base_path:
                # Also serve the prefixed path so that direct-LAN clients
                # (like Home Assistant) which receive the announced endpoint
                # "/paprika/messages/..." can POST to it on the bare port.
                routes.append(
                    Mount(f"{base_path}/messages/", app=sse.handle_post_message)
                )
            starlette_app = Starlette(
                debug=False,
                routes=routes,
                lifespan=lifespan,
            )

            async def app(scope, receive, send):
                if scope["type"] == "lifespan":
                    await starlette_app(scope, receive, send)
                    return
                await dispatcher(scope, receive, send)

            logger.info(
                f"Starting MCP HTTP server on {host}:{port} "
                f"(base_path={base_path or '(none)'})"
            )
            return app, host, port

        # Run the server (default stdio)
        if not use_http:
            # Warm the recipe cache in the background so list_recipes is fast
            # on the first call without delaying server startup.
            asyncio.create_task(paprika_client.warm_up_cache())
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="paprika-mcp-python",
                        server_version="1.0.0",
                        capabilities=server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )

    except Exception as e:
        logger.error(f"Server startup failed: {str(e)}")
        raise
    finally:
        if paprika_client:
            await paprika_client.close()


def run_server():
    """Synchronous entry point for the console script."""
    try:
        result = asyncio.run(main())
    except Exception:
        # Re-raise so a misconfigured environment (bad credentials, missing
        # config, etc.) produces a non-zero exit and a visible traceback in
        # the systemd journal instead of silently "succeeding".
        logger.exception("Server startup failed")
        raise

    if result:
        import uvicorn
        app, host, port = result
        uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    run_server()
