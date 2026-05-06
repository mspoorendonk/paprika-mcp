# Paprika MCP Python Server

A Model Context Protocol (MCP) server that integrates Paprika Recipe Manager with agents, enabling natural language recipe management through AI conversation.

Supported agents:
- Claude desktop
- Home Assistant
- Google Antigravity
- VSCode github copilot

## Features

- **Recipe Creation**: Create new recipes with natural language descriptions
- **Recipe Updates**: Full and partial recipe updates while preserving existing data
- **Recipe Listing**: Browse all recipes with complete ingredient lists and details
- **Grocery Management**: Add, remove, and list items on your Paprika grocery lists

## Demo

### Creating Recipes
![Creating a recipe with natural language](images/demo-create-recipe.png)

### Recipe Recommendations  
![Getting recipe recommendations](images/demo-list-recipes.png)

## Prerequisites

- Python 3.8 or higher
- [Paprika Recipe Manager 3](https://www.paprikaapp.com/) with cloud sync enabled
- [Claude Desktop](https://claude.ai/download) application
- Valid Paprika account credentials

## Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/sandordaroczi/paprika-mcp-python-server.git
cd paprika-mcp-python-server

# Install uv (if you don't have it already)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies using uv
uv venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
```

### 2. Configuration

Copy the example environment file and configure your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your Paprika credentials:
```env
PAPRIKA_USERNAME=your_email@example.com
PAPRIKA_PASSWORD=your_paprika_password
```

### 3. Test the Server

Verify your setup by testing authentication:

```bash
python src/server.py --username your_email --password your_password
```

### 4. Configure Claude Desktop

Edit your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the MCP server configuration:

```json
{
  "mcpServers": {
    "paprika": {
      "command": "/path/to/your/.venv/bin/python",
      "args": ["/path/to/paprika-mcp-python-server/src/server.py"],
      "env": {
        "PAPRIKA_USERNAME": "your_email@example.com",
        "PAPRIKA_PASSWORD": "your_password"
      }
    }
  }
}
```

**Important**: Use absolute paths and ensure you're pointing to your virtual environment's Python interpreter.

### 5. Restart Claude Desktop

Completely quit and restart Claude Desktop for the changes to take effect.

## LAN Deployment (Run as a Network Service)

If you want the MCP server to be centrally accessible by multiple agents across your network (e.g., Home Assistant OS on one machine and Claude Desktop on another), you should run the server using Server-Sent Events (SSE).

### 1. Start the Server in SSE Mode on Linux

Run the server on your dedicated Linux machine, binding to your LAN IP address:

```bash
# Keep this running, e.g., using systemd or tmux
python src/server.py --sse --host 0.0.0.0 --port 8000
```

### 2. Configure Home Assistant (HAOS)

On your separate Home Assistant instance, connect to the remote server by adding the SSE URL to your `configuration.yaml` (or configuring it via the HA MCP UI integration):

```yaml
mcp:
  servers:
    paprika:
      type: sse
      url: "http://<linux-machine-ip>:8000/mcp/sse" # Home Assistant supports local HTTP
```

### 3. Configure Claude Desktop to use the Remote Server

Claude Desktop requires a secure connection (`https://`). To achieve this, you need to expose your local server through an HTTPS reverse proxy (like Caddy, Nginx, Cloudflare Tunnels, or ngrok).

Once you have your HTTPS URL, use a generic SSE-to-stdio bridge (requires Node.js/npx on the Claude machine) to connect:

```json
{
  "mcpServers": {
    "paprika-remote": {
      "command": "npx",
      "args": ["-y", "@browserbasehq/mcp-sse-bridge", "https://<your-secure-domain>/mcp/sse"]
    }
  }
}
```

## Usage Examples

Once configured, you can interact with your Paprika recipes through natural language in Claude:

### Create a New Recipe
```
Create a new recipe called "Spaghetti Carbonara" with these ingredients:
- 400g spaghetti
- 4 large eggs
- 100g pancetta
- 50g Parmesan cheese
- Black pepper and salt

Instructions:
1. Cook spaghetti according to package directions
2. Fry pancetta until crispy
3. Beat eggs with Parmesan
4. Combine hot pasta with pancetta
5. Add egg mixture and toss quickly
6. Season and serve immediately

Set servings to 4 people, prep time 10 minutes, cook time 15 minutes.
```

### List Your Recipes
```
Show me all my recipes from Paprika
```

### Update a Recipe
```
Update the Spaghetti Carbonara recipe to serve 6 people instead of 4, and add "Use room temperature eggs to prevent scrambling" to the notes.
```

### Partial Updates
```
For the recipe with UID [recipe-uid], just change the prep time to 15 minutes and add salt to the ingredients list.
```

### Manage Groceries
```
Add chocolate to my grocery list.

Remove choco from my grocery list.
```

## Available Tools

| Tool | Description |
|------|-------------|
| `create_recipe` | Create a new recipe with all details |
| `update_recipe` | Complete recipe update (all fields) |
| `update_recipe_partial` | Update only specified fields |
| `list_recipes` | List all recipes with ingredients and details |
| `get_groceries` | List all grocery items currently on the Paprika grocery list |
| `add_grocery_item` | Add a new item to the Paprika grocery list |
| `remove_grocery_item` | Remove an item from the Paprika grocery list by name or ID |

### Components

- **MCP Server** (`src/server.py`): Handles MCP protocol communication and tool routing
- **Paprika Client** (`src/paprika_client.py`): Manages Paprika API authentication and operations
- **Configuration** (`src/config.py`): Handles environment variables and CLI arguments

## Development

### Running Tests

```bash
# Install development dependencies
pip install pytest pytest-asyncio

# Run tests
pytest tests/ -v
```

### Adding New Features

1. Create a feature branch: `git checkout -b feature/new-feature`
2. Add your changes with tests
3. Ensure all tests pass: `pytest`
4. Format code: `black src/ tests/`
5. Commit and push your changes

## Troubleshooting

### Common Issues

#### Authentication Failed
- Verify your Paprika credentials work at [paprikaapp.com](https://paprikaapp.com)
- Ensure cloud sync is enabled in your Paprika app
- Check that credentials are correctly set in environment variables

#### Claude Connection Issues
- Verify Python path in Claude config points to your virtual environment
- Check that server.py path is absolute and correct
- Ensure all required packages are installed in the virtual environment

#### Import Errors
- Confirm virtual environment is activated
- Reinstall dependencies: `pip install -r requirements.txt`
- Check Python version compatibility (3.8+)

## API Rate Limiting

The Paprika API enforces rate limiting to protect their service. When using this server it is advised to avoid making excessive or bulky requests in short periods of time.

## Contributing

Contributions are welcome! Whether you want to report bugs, suggest new features, improve documentation, or submit code changes, your input helps make this project better. Feel free to open an issue for discussions or submit a pull request with your improvements.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Anthropic](https://www.anthropic.com/) for the Model Context Protocol
- [Paprika Recipe Manager](https://www.paprikaapp.com/) for the excellent recipe management app
- The MCP community for documentation and examples

## Changelog

### v1.0.0 (2025-09-27)
- Initial release
- Recipe creation, updating, and listing functionality
- Full and partial update capabilities

---

**Note**: This is an unofficial integration and is not affiliated with or endorsed by Paprika Recipe Manager or Anthropic.