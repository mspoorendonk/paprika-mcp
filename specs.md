Prepare for scenarios like this. Where a use talks to an LLM, which does the toolcalls from MCP:

Tell me which grocery lists I have.

Add chocolate to my grocery list.

Remove choco from my grocery list.

There is no choco on your default list, but I do see "chocolate" on it. Do you want to remove that?


Everything can be passed in by name or by ID

## MCP Client Requirements & Transport Protocols

Leverage nginx for authorisation or https and reverse proxy where required.

Different LLM clients and agents connect to MCP servers using specific transport protocols (`stdio` or streaming http). Because the Paprika MCP server can run strictly locally (via `uv`) or as a network service (via Docker/Nginx HTTP), it is important to map out how to connect each supported client.

### 1. Claude Desktop
- **Transport**: `stdio` (native), `sse` (via bridge/proxy or native if supported).
- **Requirements**: By default, Claude Desktop spawns the server as a local child process using `stdio`. To connect to this server remotely via its SSE endpoint, you typically use a bridge like `@browserbasehq/mcp-sse-bridge` deployed via `npx` in the `claude_desktop_config.json`.

### 2. Home Assistant
- **Transport**: `sse`.
- **Requirements**: Home Assistant acts as a network client and requires a persistent HTTP/SSE connection. It can connect internally (`http://<ip>:8000/mcp/sse`) or externally using HTTPS. Supports Basic Authentication.

### 3. Google Antigravity
- **Transport**: `stdio` or `sse`.
- **Requirements**: Supports acting as an MCP client interacting with local commands or remote endpoints. Reaching a remote Dockerized setup would utilize the `sse` URL configuration.

### 4. Gemini CLI
- **Transport**: `stdio` or `sse`.
- **Requirements**: Command-line interfaces typically favor spawning local tools via `stdio`, but modern integrations also allow connecting to a remote context source utilizing `sse` and standard HTTP headers.

### 5. VSCode GitHub Copilot
- **Transport**: `stdio` and `sse`.
- **Requirements**: The GitHub Copilot Chat extension supports integrating MCP servers via the VS Code settings (e.g., `github.copilot.chat.mcpServers`). It supports both local `stdio` subprocesses and remote `sse` endpoints.

### 6. Claude.ai (Web)
- **Transport**: Secure `sse` (HTTPS).
- **Requirements**: Web-based cloud models cannot spawn local subprocesses. To supply context to Claude web, the MCP server must be exposed to the public internet securely (HTTPS via a reverse proxy like Nginx or Cloudflare Tunnels).

### 7. Gemini (Web)
- **Transport**: Secure `sse` (HTTPS).
- **Requirements**: Similar to Claude Web, supplying an external MCP server to Gemini Web requires a resolvable, public-facing HTTPS SSE endpoint that the Google cloud can reach securely.

## Recipe cache

`list_recipes` must return quickly enough for an interactive LLM tool call (well under 10 s, ideally <1 s after warm-up). The Paprika cloud API has no batch "fetch all recipes" endpoint: each recipe body must be retrieved with `GET /sync/recipe/{uid}/`. Doing this sequentially for a real library (40–500+ recipes) blows past any LLM tool-call timeout, and Paprika additionally applies aggressive per-IP rate limiting (multi-minute IP blocks for bursty traffic, as documented by the community in the [reverse-engineered API gist](https://gist.github.com/mattdsteele/7386ec363badfdeaad05a418b9a1f30a)).

The MCP server therefore maintains an in-memory recipe cache and only refetches what has actually changed.

### Data model (in-process state)

- `recipe_cache: dict[uid, recipe_dict]` — full recipe bodies as last seen.
- `recipe_index_fingerprint: str` — SHA-256 over the sorted list of `(uid, hash)` pairs returned by `/sync/recipes`. This is the cheap "did anything change" signal.
- `cache_ready: asyncio.Event` — set after the first successful warm-up so `list_recipes` callers either hit the cache instantly or, if called before warm-up completes, wait once.
- `cache_lock: asyncio.Lock` — serializes refresh so concurrent tool calls don't all stampede the Paprika API.

### Invalidation strategy

Each `list_recipes` invocation runs this minimal protocol:

1. `GET /sync/recipes` — one cheap call returning `[{uid, hash}, …]` for the entire library.
2. Compute the fingerprint over that list and compare to `recipe_index_fingerprint`.
   - If unchanged → return the cached recipe bodies. **Zero per-recipe calls.**
3. If changed, diff against the cache:
   - **drop** any uid no longer in the index (deleted on the server).
   - **stale** = uids whose hash differs from the cached hash, plus uids not yet in the cache.
4. Refetch only the stale recipes via `/sync/recipe/{uid}/`, with a concurrency cap (`asyncio.Semaphore(3)`) and a small jitter (~50 ms) between requests to stay under Paprika's rate limit.
5. Update the cache and the fingerprint.

This mirrors how the official Paprika app keeps in sync (small status check + selective per-recipe pull) and is the explicitly-recommended pattern from the community gist to avoid IP bans.

### Startup warm-up

On server startup the MCP server schedules a background warm-up task that performs a full refresh (every recipe is "stale" the first time) using the same concurrency-capped fetcher. The server itself becomes ready immediately so MCP clients can connect and use grocery tools without delay; only `list_recipes` blocks on `cache_ready` if it is invoked before the warm-up has finished.

If warm-up encounters errors (e.g. Paprika rate-limit, transient network failure), it logs them and leaves the cache partially populated. A subsequent `list_recipes` call will retry the missing recipes.

### Trash and limits

- Recipes with `in_trash == True` are excluded from the returned list (they are still kept in the cache so a subsequent un-trash is detected via the hash diff).
- The `limit` argument to `list_recipes` only truncates the returned list; the cache always covers the whole library.

### Why not `/sync/status`

Paprika exposes `/api/v1/sync/status/` as an even cheaper "anything changed?" counter. The current implementation uses the fingerprint of `/sync/recipes` instead because (a) it costs one call either way once we already need the index for diffing, (b) it works regardless of how the v2 API behaves for `/sync/status`, and (c) it is robust against the counter being bumped by unrelated objects (groceries, meals, …) which would otherwise trigger needless full refetches.
