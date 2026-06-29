# Paprika MCP Python Server

A Model Context Protocol (MCP) server that integrates Paprika Recipe Manager with agents, enabling natural language recipe management through AI conversation.

Supported agents:
- Claude Desktop
- Claude Code (CLI)
- Home Assistant
- Google Antigravity
- Gemini CLI
- VS Code GitHub Copilot
- Claude.ai (web)
- Gemini (web)

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
uv pip install -e .
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

If you want the MCP server to be centrally accessible by multiple agents across your network (e.g., Home Assistant OS on one machine and Claude Code on another), run the server in HTTP mode. It exposes a single Streamable HTTP endpoint at `/mcp` — the current MCP standard, used by all supported clients (Claude Desktop, Claude Code, Home Assistant, Gemini CLI, VS Code Copilot, Antigravity, Claude.ai web, Gemini web).

### 1. Start the Server in HTTP Mode on Linux

Run the server on your dedicated Linux machine, binding to your LAN IP address:

```bash
# Keep this running, e.g., using systemd or tmux
python src/server.py --http --host 0.0.0.0 --port 8000
```

### 2. Configure Home Assistant (HAOS)

In Home Assistant: **Settings → Devices & Services → Add Integration → "Model Context Protocol"**. Point it at the `/mcp` endpoint:

- **Server URL:** `http://<linux-machine-ip>:8000/mcp`

Over a public HTTPS reverse proxy, point it at the proxied URL (`https://example.com/<prefix>/mcp`); the integration runs the OAuth flow (see [section 4](#4-authentication--oauth2-with-google-login)).

### 3. Reverse-Proxy Notes

When fronting the server with nginx (or similar):

- Strip the public prefix before forwarding (e.g. rewrite `^/paprika(/.*)$ $1`).
- Disable buffering for streaming: `proxy_buffering off; proxy_cache off; chunked_transfer_encoding off;` and a long `proxy_read_timeout`.
- Set `proxy_http_version 1.1` and `proxy_set_header Connection '';`.

### 4. Authentication — OAuth2 with Google login

Authentication is OAuth 2.0 — the server is its own Authorization Server and
brokers the human login to Google (OIDC), allow-listing by email. HTTP
Basic-Auth at nginx is **retired**; clients no longer need per-client
passwords. See [`src/oauth_app.py`](src/oauth_app.py) for the implementation.

Server-side config lives in `.env` (gitignored): `OAUTH_ISSUER_URL`,
`OAUTH_PATH_PREFIX`, the Google OAuth client credentials, and the email
allowlist. MCP clients only need the endpoint URL — they run the OAuth flow
automatically and log in with Google in the browser. Access is revoked by
removing an email from the allowlist.

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
| `list_recipes` | List all recipes with ingredients and details. Served from a hash-validated in-memory cache so repeated calls are fast and stay under Paprika's per-IP rate limit (see `specs.md` → "Recipe cache"). The cache is warmed in the background at server startup. |
| `get_groceries` | List unchecked grocery items on the Paprika grocery list (set `include_purchased=true` to include checked items) |
| `add_grocery_item` | Add a new item to the Paprika grocery list |
| `remove_grocery_item` | Remove an item from Paprika groceries. Searches all lists by default; strict matching (exact UID, exact name, or unambiguous substring). Returns the removed item's name, UID, and list UID. Ambiguous matches return an error listing candidates. |

### Error contract

Failures are surfaced via the MCP `isError=true` flag, with a stable category in `structuredContent.code` and a TTS-friendly message in the text content. The LLM should branch on the **code**, not parse the prose. See `specs.md` → "Scenario 9 — Errors the user should hear in plain language" for example dialogues.

| Code | When | Extra fields on `structuredContent` |
|------|------|--------------------------------------|
| `paprika_unreachable` | Network failure reaching paprikaapp.com (DNS, TCP, TLS, timeout). | – |
| `paprika_auth_failed` | Paprika rejected the saved credentials (HTTP 401/403). | – |
| `paprika_rate_limited` | Paprika is throttling us (HTTP 429/503). `list_recipes` falls back to stale cache when possible and does **not** error. | – |
| `paprika_error` | Any other unexpected non-2xx Paprika response. The raw body is logged but never returned to the user. | – |
| `invalid_argument` | Required argument missing or malformed. | `missing: [str]` |
| `grocery_not_found` | No active grocery item matches the query. | – |
| `grocery_ambiguous` | Multiple grocery items match. The LLM must ask the user to disambiguate. | `candidates: [{uid, name, list_uid, list_name}]` |
| `grocery_list_not_found` | The named grocery list doesn't exist. | `available_lists: [str]` |
| `recipe_not_found` | The given recipe UID doesn't exist (or has been deleted). | – |

User-visible messages never contain UIDs, HTTP status codes, or raw response bodies — those go to the server log only.

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

#### Live (read-only) tests against the real Paprika cloud

`tests/test_paprika_live.py` exercises the real Paprika API using credentials
from `.env`. They are skipped by default and **strictly read-only** — they
only call `authenticate`, `list_recipes`, `get_groceries`, and
`get_grocery_lists`; they never create, update, or delete anything.

```bash
PAPRIKA_LIVE_TESTS=1 pytest tests/test_paprika_live.py -v
```

Note: Paprika applies aggressive per-IP rate limiting. Running the live suite
back-to-back may produce transient connection-reset errors; rerun individual
tests in isolation.

### Adding New Features

1. Create a feature branch: `git checkout -b feature/new-feature`
2. Add your changes with tests
3. Ensure all tests pass: `pytest`
4. Format code: `black src/ tests/`
5. Commit and push your changes

### Pre-commit secret scanning

The repo ships with a [pre-commit](https://pre-commit.com) config that runs
[gitleaks](https://github.com/gitleaks/gitleaks) on every commit. Custom
rules in `.gitleaks.toml` catch the patterns that have actually leaked from
this repo before — `Authorization: Basic <base64>` headers, URLs with
embedded `user:password@`, and hard-coded `PAPRIKA_*` / `MCPUSER_*` literal
values.

Set up once per clone:

```bash
pip install pre-commit       # or: uv tool install pre-commit
pre-commit install           # registers .git/hooks/pre-commit

# Optionally scan the existing tree and full history:
pre-commit run --all-files
gitleaks detect --source .
```

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
- Reinstall dependencies: `uv pip install -e .`
- Check Python version compatibility (3.12+)

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

### v1.2.0 (2026-05-08)
- Voice-friendly error contract: typed exception hierarchy with stable `structuredContent.code` values (`paprika_unreachable`, `paprika_auth_failed`, `paprika_rate_limited`, `grocery_not_found`, `grocery_ambiguous`, `grocery_list_not_found`, `recipe_not_found`, `invalid_argument`, `paprika_error`) and TTS-friendly user-visible messages. See `specs.md` Scenario 9.
- Lazy authentication: a startup auth blip no longer kills the server; it surfaces as a friendly error on the first tool call.
- Removed legacy SSE transport. All clients (including Home Assistant) now connect to the single Streamable HTTP `/mcp` endpoint.

### v1.1.0 (2026-05-06)
- Added Streamable HTTP transport at `/mcp` (current MCP standard) for Claude Code, Gemini CLI, VS Code Copilot, Antigravity, and web clients
- Updated agent configuration examples for all supported clients

### v1.0.0 (2025-09-27)
- Initial release
- Recipe creation, updating, and listing functionality
- Full and partial update capabilities

---

**Note**: This is an unofficial integration and is not affiliated with or endorsed by Paprika Recipe Manager or Anthropic.
### Configure VS Code GitHub Copilot

Add the MCP server to your VS Code `settings.json`:

```json
{
  "github.copilot.chat.mcpServers": {
    "paprika-local": {
      "command": "/path/to/paprika-mcp-python-server/.venv/bin/paprika-mcp-python-server"
    },
    "paprika-remote": {
      "url": "https://<your-domain>/<prefix>/mcp"
    }
  }
}
```

### Configure Claude Code (CLI)

Use the streamable HTTP transport. The endpoint uses OAuth (see
[section 4](#4-authentication--oauth2-with-google-login)); Claude Code runs the
OAuth flow and opens a browser for the Google login on first connect:

```bash
claude mcp add --transport http paprika https://<your-domain>/paprika/mcp
```

For a local stdio install: `claude mcp add paprika -- /path/to/paprika-mcp-python-server/.venv/bin/paprika-mcp-python-server`.

### Configure Google Antigravity

Use the built-in MCP connection configuration with the streamable HTTP URL:

```json
{
  "mcpServers": {
    "paprika": {
      "httpUrl": "https://<your-domain>/<prefix>/mcp"
    }
  }
}
```

### Configure Gemini CLI

Gemini CLI's `settings.json` distinguishes streamable HTTP (`httpUrl`) from SSE (`url`):

```json
{
  "mcpServers": {
    "paprika-local": {
      "command": "/path/to/paprika-mcp-python-server/.venv/bin/paprika-mcp-python-server"
    },
    "paprika-remote": {
      "httpUrl": "https://<your-domain>/paprika/mcp"
    }
  }
}
```

or from the CLI: 
```cli
gemini mcp add --transport http --scope user paprika https://<your-domain>/paprika/mcp
```

The endpoint uses OAuth (see [section 4](#4-authentication--oauth2-with-google-login)); Gemini runs the OAuth flow and opens a browser for the Google login on first connect.


### Web Clients (Claude.ai & Gemini Web)

For cloud / web clients to interact with your local Paprika deployment, give them your public HTTPS streamable HTTP URL (`https://<your-domain>/<prefix>/mcp`) as a custom connector. They complete the OAuth flow (see [section 4](#4-authentication--oauth2-with-google-login)) with a Google login — no per-client credentials to share.
