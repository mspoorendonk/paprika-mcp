# Specification for Paprika grocerylist and recipe MCP server

## Usages

This server is designed to be driven by a voice agent: the user speaks in natural language, the LLM picks the right MCP tool, and the server returns short, speakable results. Everything that takes a list or item identifier accepts either a name or a UID — the server resolves names case-insensitively and falls back to unambiguous substring matches. When a request is ambiguous, the server returns a structured error with the candidates so the LLM can ask the user to disambiguate rather than silently picking one.

**"Removing" a grocery item means marking it as purchased**, not deleting it. When a user says *"remove chocolate from my list"*, *"take the chicken off"*, or *"I bought the eggs"*, the intent is the same: the item should disappear from the active (unchecked) shopping view. The MCP server implements this by setting `purchased=true` on the item via Paprika's sync endpoint. The item stays in Paprika's history (visible under the checked-off section in the app) so the user can un-check or re-add it later. The server never permanently destroys grocery items.

The scenarios below show the expected flow: user utterance → tool call(s) the LLM should make → server response → spoken reply.

### Scenario 1 — Browsing grocery lists

> **User:** "Tell me which grocery lists I have."

- LLM calls `get_groceries` with no arguments.
- Server returns the unchecked items grouped by list (the response includes the list name/UID for each item).
- **Assistant:** "You have two lists: *Default* with 7 items, and *Costco run* with 3 items."

> **User:** "What's on the Costco list?"

- LLM calls `get_groceries` with `include_purchased=false` and filters the response to the *Costco run* list (or, if implemented, passes `list_name_or_id="Costco run"`).
- **Assistant:** "Olive oil, paper towels, and a rotisserie chicken."

### Scenario 2 — Adding an item

> **User:** "Add chocolate to my grocery list."

- LLM calls `add_grocery_item` with `name="chocolate"`, `ingredient="chocolate"` and no `list_name_or_id` (uses the default list).
- Server returns the created item's UID and the list it was added to.
- **Assistant:** "Added chocolate to your default list."

> **User:** "Actually make it 200 grams of dark chocolate, on the Costco list."

- LLM calls `remove_grocery_item` with `item_name_or_id="<uid from previous turn>"` to undo the first add.
- LLM calls `add_grocery_item` with `name="dark chocolate"`, `ingredient="dark chocolate"`, `quantity="200g"`, `list_name_or_id="Costco run"`.
- **Assistant:** "Done — 200 grams of dark chocolate is on the Costco list."

### Scenario 3 — Disambiguating a removal

> **User:** "Remove choco from my grocery list."

- LLM calls `remove_grocery_item` with `item_name_or_id="choco"`.
- Server finds no exact match for "choco" but sees "chocolate" and "chocolate chips" as substring candidates among the unpurchased items and returns an `AmbiguousMatchError` listing both with their UIDs and list names.
- **Assistant:** "There's no item called *choco*, but I see *chocolate* on your default list and *chocolate chips* on the Costco list. Which one should I check off?"

> **User:** "The plain chocolate one."

- LLM calls `remove_grocery_item` with the UID of the *chocolate* candidate from the previous response (preferring UID over name once disambiguated).
- Server marks the item `purchased=true`; it's no longer in the active list but remains in Paprika's checked-off history.
- **Assistant:** "Done — chocolate is checked off your default list."

### Scenario 4 — Single-match removal ("I bought it")

> **User:** "I picked up the rotisserie chicken."

- LLM calls `remove_grocery_item` with `item_name_or_id="rotisserie chicken"`.
- Server finds exactly one unambiguous match across the user's active lists and marks it purchased.
- **Assistant:** "Got it, rotisserie chicken is checked off."

> **User:** "Wait, I grabbed the wrong one, put it back."

- The MCP server has no "un-purchase" tool today; the LLM should tell the user to un-check it from the Paprika app, or (future enhancement) call a dedicated `restore_grocery_item` tool.
- **Assistant:** "I can't un-check items yet — open the Paprika app and tap *rotisserie chicken* in the purchased section to put it back on the active list."

### Scenario 5 — Browsing recipes

> **User:** "What chicken recipes do I have?"

- LLM calls `list_recipes` with `query="chicken"` and `names_only=true`.
- Server filters the cache for "chicken" in the recipe name or ingredients (case-insensitive substring) and returns only `[{uid, name}]` — no ingredients block, keeping the response compact.
- **Assistant:** "You have four: *Lemon roast chicken*, *Thai basil chicken*, *Chicken tikka masala*, and *Chicken noodle soup*."

> **User:** "Tell me more about the Thai basil chicken."

- LLM calls `list_recipes` with `query="Thai basil chicken"` and `names_only=false` (default).
- Server returns the full recipe including ingredients, description, notes, times, and servings.
- **Assistant:** "Thai basil chicken serves 4. Ingredients: …"

#### `list_recipes` tool contract

- **`query`** (optional, default `""`) — Case-insensitive substring filter applied to both `name` and `ingredients`. Returns all non-trashed recipes when omitted.
- **`names_only`** (optional, default `false`) — When `true`, each result contains only `uid` and `name`. Use this for browsing/searching where ingredients aren't needed. When `false`, the full recipe body is returned (ingredients, description, notes, times, servings).
- **`limit`** (optional, default `50`) — Maximum results after filtering.

### Scenario 6 — Cooking a recipe and stocking up

> **User:** "I want to make the Thai basil chicken tonight. Add what I'm missing to the grocery list."

- LLM calls `list_recipes` and locates *Thai basil chicken* (name match → UID + ingredients block).
- LLM compares the recipe's ingredients to the current pantry context the user has shared (or just asks the user which ones they're missing).
- For each missing ingredient the LLM calls `add_grocery_item` with `name`/`ingredient` set to the ingredient line and `list_name_or_id` left empty (default list).
- **Assistant:** "Added Thai basil, fish sauce, and bird's eye chillies to your default list."

### Scenario 7 — Saving a new recipe dictated by voice

> **User:** "Save a new recipe called *Quick weeknight pasta*. Ingredients: 200 grams spaghetti, two cloves of garlic, olive oil, chilli flakes, parsley. Directions: boil the pasta, sizzle the garlic and chilli in olive oil, toss together, finish with parsley."

- LLM calls `create_recipe` with `name="Quick weeknight pasta"`, `ingredients` as a newline-separated string, `directions` as the spoken instructions, and leaves the optional fields empty.
- Server returns the new UID; the recipe cache picks it up on the next `list_recipes` invocation via the index hash diff.
- **Assistant:** "Saved *Quick weeknight pasta* to your recipes."

### Scenario 8 — Editing an existing recipe

> **User:** "On the Thai basil chicken, change the prep time to 10 minutes and add a note that the kids prefer it without chillies."

- LLM calls `list_recipes` to resolve *Thai basil chicken* → UID.
- LLM calls `update_recipe_partial` with `uid=<that uid>`, `prep_time="10 mins"`, and `notes` set to the appended note text.
- **Assistant:** "Updated."

### Scenario 10 — Adding all recipe ingredients to the grocery list in one step

> **User:** "I'm making Thai basil chicken tomorrow. Add all the ingredients to my shopping list."

- LLM calls `add_recipes_to_grocery_list` with `recipes=["Thai basil chicken"]` (and optionally `list_name_or_id` if the user named a specific list).
- Server resolves the recipes by name from the cache, parses their `ingredients` fields line by line, and adds each non-empty line as a grocery item — all linked back to the recipe via Paprika's `recipe_uid` field so the Paprika app can group them under the recipe name in its shopping view.
- Server returns a summary: recipe names, number of items added, and the target list.
- **Assistant:** "Added 8 ingredients for Thai basil chicken to your default shopping list."

> **User:** "Actually, put them on the Costco list."

- LLM calls `add_recipes_to_grocery_list` with `recipes=["Thai basil chicken"]` and `list_name_or_id="Costco run"`.
- **Assistant:** "Done — 8 ingredients for Thai basil chicken are on your Costco list."

#### Tool contract

- **Name**: `add_recipes_to_grocery_list`
- **Parameters**:
  - `recipes` (required) — List of names or UIDs of the recipes to add. Resolved via the recipe cache.
  - `list_name_or_id` (optional) — Target grocery list. Defaults to the user's default list if omitted.
- **Returns**: Plain-text summary: recipe names, number of items added, list name.
- **Errors**: Uses the same error codes as other tools — `recipe_not_found` if the recipe cannot be resolved, `grocery_list_not_found` if the list name doesn't exist, plus the standard connectivity codes.
- **Recipe linking**: Every grocery item must have `recipe_uid` set to the recipe's UID, and `recipe` set to the recipe name. The Paprika app uses both to group the items under the recipe name in the shopping view.
- **Bulk POST**: All ingredients are sent in a **single POST** to `/sync/groceries/` as a JSON array (gzip-compressed multipart). This is more efficient than one call per item and matches how the official app adds a recipe to the shopping list.
- **`aisle` field**: Always sent as empty string — Paprika auto-assigns the aisle based on the `ingredient` value.
- **`separate` field**: Set to `false` for all items (matches the official app behaviour; `true` would visually separate the item from others in the same aisle).
- **Ingredient parsing**: Each non-empty, non-whitespace line of the recipe's `ingredients` field becomes one grocery item. The full line is used as both `name` and `ingredient`; `quantity` is left empty (the quantity is embedded in the ingredient line as Paprika stores it).

> **API reference**: [`aarons22/paprika-tools` API_REFERENCE.md §Grocery](https://github.com/aarons22/paprika-tools/blob/main/API_REFERENCE.md) — community-maintained reference for the undocumented Paprika sync API.

### Scenario 9 — Errors the user should hear in plain language

The voice agent's worst failure mode is *"Sorry, something went wrong."* Every tool the server exposes must therefore return errors that (a) name **what** failed, (b) name **why** in one short clause, and (c) where useful, suggest **what to do next**. The server returns errors via the MCP `isError=true` flag with a `TextContent` body that is already phrased for TTS — no stack traces, no UIDs, no HTTP status codes in the user-visible text. Detailed diagnostics go to the server log only.

The categories below are the contract the LLM relies on. Each has a stable error code (returned in `structuredContent.code`) so the LLM can branch on category instead of pattern-matching prose.

#### 9a. Paprika unreachable (`paprika_unreachable`)

> **User:** "What's on my grocery list?"

- LLM calls `get_groceries`.
- Server cannot reach `paprikaapp.com` (DNS, TCP, TLS, or timeout).
- Tool returns `isError=true`, code `paprika_unreachable`, text *"I can't reach the Paprika service right now. Please try again in a moment."*
- **Assistant:** "I can't reach Paprika right now. Want me to try again in a minute?"

#### 9b. Authentication failed (`paprika_auth_failed`)

> **User:** "Add milk to the list."

- LLM calls `add_grocery_item`.
- Paprika rejects the credentials (HTTP 401/403 even after a re-login attempt).
- Tool returns code `paprika_auth_failed`, text *"Paprika rejected my login. The saved username or password is probably wrong."*
- **Assistant:** "Paprika won't accept my login. You'll need to update the credentials on the server."

#### 9c. Rate-limited (`paprika_rate_limited`)

> **User:** "List my recipes."

- LLM calls `list_recipes` while the recipe-cache warm-up is being throttled by Paprika (HTTP 429 or temporary IP block).
- If a populated cache exists, the server serves stale data and notes it ran without a refresh — **not** an error.
- If no cache exists yet, the tool returns code `paprika_rate_limited`, text *"Paprika is rate-limiting us. I'll have your recipes ready in a couple of minutes."*
- **Assistant:** "Paprika is throttling me — give it a minute or two and ask again."

#### 9d. Item not found (`grocery_not_found`)

> **User:** "Take the kale off the list."

- LLM calls `remove_grocery_item` with `item_name_or_id="kale"`.
- No active item matches `kale` — not even as a substring.
- Tool returns code `grocery_not_found`, text *"There's nothing called 'kale' on your active grocery lists."*
- **Assistant:** "I don't see kale on your list. Did you maybe already check it off?"

#### 9e. Ambiguous match (`grocery_ambiguous`)

Already shown in Scenario 3, but spelled out as a category: the tool returns `isError=true` (so the LLM treats it as a branch, not a success), code `grocery_ambiguous`, plus `structuredContent.candidates` containing `[{uid, name, list_name}, …]`. The text body lists the candidates by **name and list** only — never UIDs aloud.

- **Assistant (reading the candidates):** "I see two: *chocolate* on your default list and *chocolate chips* on the Costco list. Which one?"

#### 9f. List not found (`grocery_list_not_found`)

> **User:** "Add olives to the *Trader Joe's* list."

- LLM calls `add_grocery_item` with `list_name_or_id="Trader Joe's"`.
- The user has no list resembling that name (no exact, substring, or fuzzy match).
- Tool returns code `grocery_list_not_found`, text *"You don't have a grocery list called 'Trader Joe's'. Your lists are: Default, Costco run."*
- **Assistant:** "There's no Trader Joe's list. You have Default and Costco run — want me to add olives to one of those?"

#### 9g. Recipe not found (`recipe_not_found`)

> **User:** "Update the prep time on *Quick weeknigt pasta* to 15 minutes." *(typo)*

- LLM calls `list_recipes`, finds no match for *weeknigt*, and either (a) does the substring search itself and asks the user, or (b) calls `update_recipe_partial` with a guessed UID.
- If the call is made with a UID that doesn't exist, the tool returns code `recipe_not_found`, text *"I can't find a recipe with that ID. It may have been deleted."*
- **Assistant:** "I can't find that recipe — did you maybe mean *Quick weeknight pasta*?"

#### 9h. Missing or invalid argument (`invalid_argument`)

> **User:** "Save a new recipe." *(no name, no ingredients)*

- LLM calls `create_recipe` with empty `name`.
- The MCP SDK's input-schema validator fails the call before it reaches our code; it produces *"Input validation error: 'name' is a required property."*
- The server upgrades that to a friendlier message via the same code: *"I need a name and at least the ingredients to save a recipe."*
- **Assistant:** "I need a name for the recipe — what should I call it?"

#### 9i. Unexpected Paprika failure (`paprika_error`)

Catch-all for any other non-2xx Paprika response (e.g. 5xx, malformed JSON). The tool returns code `paprika_error`, text *"Paprika returned an unexpected error. I've logged the details."* The server logs the status code and response body for the operator. **Never** echo Paprika's raw response into the assistant message — voice users don't want HTML or JSON read aloud.

- **Assistant:** "Something went wrong on Paprika's side. Try again in a moment, and if it keeps happening, check the server log."

### Conventions the LLM should follow

- **Resolve once, act with UIDs.** When a follow-up turn references an item the LLM just looked up, pass the UID rather than re-sending the name — this avoids re-triggering the disambiguation path.
- **Never silently pick on ambiguity.** If the server returns an `AmbiguousMatchError`, surface the candidates to the user instead of guessing.
- **Default list is implicit.** Omit `list_name_or_id` unless the user names a specific list.
- **Purchased items are hidden by default.** Only pass `include_purchased=true` to `get_groceries` if the user explicitly asks about already-bought items. `remove_grocery_item` only matches against unpurchased items for the same reason.
- **Read errors as their category, not their prose.** Branch on `structuredContent.code` (see Scenario 9) rather than parsing the user-facing text. The text is for the user; the code is for the LLM. Never read UIDs, HTTP status codes, or stack traces aloud.
- **Bulk ingredient add.** When the user wants to plan a meal and add all its ingredients at once, prefer `add_recipes_to_grocery_list` over calling `add_grocery_item` in a loop. The single-call version is faster, keeps items grouped by recipe in the Paprika app, and avoids timeouts on large ingredient lists.
### Scenario 11 — Planning a recipe as dinner on a specific day

> **User:** "Plan the Thai basil chicken as dinner on Saturday."

- LLM resolves "Saturday" to the correct date (e.g. `2026-07-05`).
- LLM calls `plan_meals` with `meals=[{"recipe_name_or_id": "Thai basil chicken", "date": "2026-07-05", "meal_type": "dinner"}]`.
- Server resolves the recipe from cache, builds a meal object, and POSTs it to Paprika's `/sync/meals/` endpoint.
- **Assistant:** "Scheduled Thai basil chicken as dinner on Saturday the 5th."

> **User:** "Put the blueberry muffins on Sunday morning."

- LLM calls `plan_meals` with `meals=[{"recipe_name_or_id": "blueberry muffins", "date": "2026-07-06", "meal_type": "breakfast"}]`.
- **Assistant:** "Scheduled blueberry muffins as breakfast on Sunday the 6th."

#### Tool contract

- **Name**: `plan_meals`
- **Parameters**:
  - `meals` (required) — List of meals to schedule. Each meal requires:
    - `recipe_name_or_id` (required) — Name or UID of the recipe.
    - `date` (required) — Target date in `YYYY-MM-DD` format.
    - `meal_type` (optional, default `"dinner"`) — One of `breakfast`, `lunch`, `dinner`, `snack`. Case-insensitive.
- **Returns**: Plain-text spoken confirmation: number of meals scheduled, and details per meal.
- **Errors**: `recipe_not_found` if any recipe cannot be resolved; `invalid_argument` if the date format or meal type is invalid; standard connectivity codes.
- **API**: POSTs a gzip-compressed JSON array to `POST /v2/sync/meals/`. There is no per-uid meals endpoint; all meal plan writes go to the array endpoint. `type_uid` is sent as an empty string (Paprika ignores it). `date` uses `"YYYY-MM-DD 00:00:00"` format.
- **Meal type integers**: `breakfast=0`, `lunch=1`, `dinner=2`, `snack=3` (as used in the Paprika sync API).



## MCP Client Requirements & Transport Protocols

Leverage nginx for authorisation or https and reverse proxy where required.

This server speaks two transports: `stdio` (for clients that spawn it as a child process) and **Streamable HTTP** at a single `/mcp` endpoint (for network clients). Streamable HTTP is the current MCP standard and supersedes the legacy SSE transport — every supported client below now uses `/mcp`.

### 1. Claude Desktop
- **Transport**: `stdio` (native), or Streamable HTTP via a remote bridge.
- **Requirements**: Claude Desktop spawns the server as a local child process using `stdio`. To connect to a remote instance, point Claude Desktop at the `/mcp` URL (directly if the build supports remote MCP, or via a small bridge configured in `claude_desktop_config.json`).

### 2. Home Assistant
- **Transport**: Streamable HTTP at `/mcp`.
- **Requirements**: Home Assistant's *Model Context Protocol* integration connects over HTTP/HTTPS to the `/mcp` endpoint (e.g. `http://<ip>:8000/mcp` internally, or `https://user:pass@example.com/<prefix>/mcp` through a reverse proxy with Basic Auth).

### 3. Google Antigravity
- **Transport**: `stdio` or Streamable HTTP.
- **Requirements**: Configure the local command for `stdio`, or the remote `/mcp` URL for network access.

### 4. Gemini CLI
- **Transport**: `stdio` or Streamable HTTP.
- **Requirements**: Use `command` for a local `stdio` subprocess, or `httpUrl` pointing at the remote `/mcp` endpoint.

### 5. VSCode GitHub Copilot
- **Transport**: `stdio` or Streamable HTTP.
- **Requirements**: The GitHub Copilot Chat extension supports MCP via `github.copilot.chat.mcpServers`. Local `stdio` subprocesses or a remote `/mcp` URL both work.

### 6. Claude.ai (Web)
- **Transport**: Streamable HTTP over HTTPS.
- **Requirements**: Web-based cloud models cannot spawn local subprocesses. Expose `/mcp` over HTTPS via a reverse proxy (Nginx, Cloudflare Tunnels, etc.) so Claude.ai can reach it.

### 7. Gemini (Web)
- **Transport**: Streamable HTTP over HTTPS.
- **Requirements**: Same as Claude Web — a publicly resolvable HTTPS `/mcp` URL.

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
