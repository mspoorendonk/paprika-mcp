import asyncio
import logging
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from config import get_config
from paprika_client import PaprikaClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize server
server = Server("paprika-mcp-python")


async def main():
    """Main entry point for the MCP server."""
    paprika_client = None
    try:
        config = get_config()

        # Initialize Paprika client
        paprika_client = PaprikaClient(
            username=config.paprika_username, password=config.paprika_password
        )
        await paprika_client.authenticate()
        logger.info("Successfully authenticated with Paprika")

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
                    description="List all grocery items currently on the Paprika grocery list",
                    inputSchema={
                        "type": "object",
                        "properties": {},
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
                    description="Remove a grocery item from Paprika using its name (robust fuzzy) or UID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "item_name_or_id": {
                                "type": "string",
                                "description": "The UID or Name of the grocery item to remove (supports substring and fuzzy matching)",
                            },
                            "list_name_or_id": {
                                "type": "string",
                                "description": "Name or ID of the list to remove from (restricts search space)",
                            }
                        },
                        "required": ["item_name_or_id"],
                    },
                ),
            ]

        @server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[TextContent]:
            """Handle tool calls."""
            try:
                if name == "create_recipe":
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
                    groceries = await paprika_client.get_groceries()

                    if not groceries:
                        return [
                            TextContent(
                                type="text",
                                text="No groceries found in your Paprika account.",
                            )
                        ]

                    grocery_text = f"Found {len(groceries)} groceries:\n\n"
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
                    await paprika_client.remove_grocery_item(
                        item_name_or_id=arguments["item_name_or_id"],
                        list_name_or_id=arguments.get("list_name_or_id")
                    )
                    return [
                        TextContent(
                            type="text",
                            text=f"Successfully removed grocery item matching: {arguments['item_name_or_id']}",
                        )
                    ]

                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]

            except Exception as e:
                logger.error(f"Error in tool {name}: {str(e)}")
                return [
                    TextContent(type="text", text=f"Error executing {name}: {str(e)}")
                ]

        # Parse SSE arguments safely
        import sys
        use_sse = "--sse" in sys.argv
        host = "0.0.0.0"
        port = 8000
        
        for i, arg in enumerate(sys.argv):
            if arg == "--host" and i + 1 < len(sys.argv):
                host = sys.argv[i+1]
            elif arg == "--port" and i + 1 < len(sys.argv):
                try:
                    port = int(sys.argv[i+1])
                except ValueError:
                    pass

        if use_sse:
            from mcp.server.sse import SseServerTransport
            from starlette.applications import Starlette
            from starlette.routing import Mount
            import uvicorn

            mcp_server = server
            sse = SseServerTransport("/mcp/messages")
            
            async def handle_sse(scope, receive, send):
                async with sse.connect_sse(
                    scope, receive, send
                ) as streams:
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
                        )
                    )

            async def handle_messages(scope, receive, send):
                await sse.handle_post_message(scope, receive, send)
            
            app = Starlette(
                debug=True,
                routes=[
                    Mount("/mcp/sse", app=handle_sse),
                    Mount("/mcp/messages", app=handle_messages),
                ],
            )
            
            # Start Uvicorn
            logger.info(f"Starting SSE Server on {host}:{port}")
            return app, host, port

        # Run the server (default stdio)
        if not use_sse:
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


if __name__ == "__main__":
    result = None
    try:
        result = asyncio.run(main())
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        
    if result:
        import uvicorn
        app, host, port = result
        uvicorn.run(app, host=host, port=port)
