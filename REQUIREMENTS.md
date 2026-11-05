# Sluice requirements

Extracted from `PLAN.md` (173 lines, the build plan) and `DESIGN.md` (334 lines, locked v3).
Every requirement below carries the source line numbers it came from. Where the plan is
ambiguous the plan is quoted verbatim and the ambiguity is named, not smoothed over. When
this file and `PLAN.md` disagree, `PLAN.md` wins and this file is the bug.

Line references are to the files as committed in this repository at the T1 scaffold commit.

| Source | Range | Covers |
|---|---|---|
| `PLAN.md` | 3-8 | purpose of the plan, freeze and submission dates |
| `PLAN.md` | 10-39 | the four closed gaps |
| `PLAN.md` | 41-173 | the fourteen build tasks and their acceptance criteria |
| `PLAN.md` | 164-173 | day-by-day order, parallelism, what may slip first |
| `DESIGN.md` | 9-31 | intent and goals |
| `DESIGN.md` | 33-55 | non-goals and anti-goals |
| `DESIGN.md` | 59-107 | eleven verified constraints against Hermes v0.22.6 |
| `DESIGN.md` | 109-244 | component map, provider, tier policy, extract chain, state, manager, pane |
| `DESIGN.md` | 246-320 | risks, build order, demo plan, decisions, out of scope |

## 1. What Sluice is

One Hermes plugin — `name: sluice`, `version: 1.0.0`, `kind: backend`,
`requires_hermes: ">=0.22.5"` — that owns Hermes' web layer: it serves the native
`web_search` and `web_extract` tools through a single `WebSearchProvider`, and manages every
web backend a user has: local, keyless, free-plan and paid (`PLAN.md:3-5, 43-54`;
`DESIGN.md:9-11`).

The gap it fills (`DESIGN.md:13-17`): Hermes ships a provider registry and a hand-edited
config key but no surface to install, test, monitor or switch backends; `web-search-plus`
adds a second tool surface instead of serving the native one and does not treat free tiers
as first-class. Users end up with overlapping plugins, no shared health state, and a
fallback chain they cannot see.

Goals that shape every task (`DESIGN.md:19-31`): native tools served by one provider so cron
sessions, gateway sessions and subagents route identically; free-first tier order; per-backend
health, latency, quota and circuit state persisted across restarts; search and extract caches
with TTLs and a size cap; a local-only extract ladder; one control surface (`hermes sluice`
plus a Desktop pane); unattended-safe — no prompt may hang a cron or gateway run.

Twelve vendors, one entry each, tiers cheapest first (`DESIGN.md:136-150`):

| Tier | Vendors |
|---|---|
| Local | SearXNG |
| Keyless | ddgs, exa, parallel, firecrawl, tavily, keenable |
| Free plan | brave, tavily, exa, firecrawl |
| Paid | exa, parallel, firecrawl, tavily, keenable, brave, perplexity, xai, deepseek, tinyfish |

`openai-native` is out of v1: it is a chat-completions feature, not a search endpoint
(`DESIGN.md:154`).

## 2. Closed gaps (`PLAN.md:10-39`)

"The four items left open in the design are settled here" (`PLAN.md:12`).

### 2.1 Vendor payloads (`PLAN.md:14-16`)

`references/endpoints.md` holds the exact request and response shape and the failure mapping
for all twelve vendors, "no builder guesses left in that file" (`PLAN.md:14-16`). Perplexity
and Parallel extract paths are named; xAI is marked LLM-mediated and fragile.

| Vendor | Endpoint section | Modes | Search | Extract |
|---|---|---|---|---|
| searxng | `references/endpoints.md:39-45` | local | yes | no |
| ddgs | `references/endpoints.md:47-53` | keyless | yes | no |
| exa | `references/endpoints.md:55-73` | keyless MCP, keyed REST | yes | yes |
| parallel | `references/endpoints.md:75-92` | keyless MCP, keyed | yes | keyed via MCP fetch |
| firecrawl | `references/endpoints.md:94-103` | keyless, keyed | yes | yes |
| tavily | `references/endpoints.md:105-116` | keyless, keyed | yes | yes |
| keenable | `references/endpoints.md:118-126` | public, keyed | yes | yes |
| brave | `references/endpoints.md:128-138` | free, paid | yes | no |
| perplexity | `references/endpoints.md:140-148` | paid | yes | snippets |
| xai | `references/endpoints.md:150-159` | paid | yes | no |
| deepseek | `references/endpoints.md:161-175` | paid | yes | yes |
| tinyfish | `references/endpoints.md:177-184` | paid | yes | no |

Conventions every adapter follows (`references/endpoints.md:8-20`): JSON over HTTPS through
`httpx`, `timeout=10` for search and `30` for fetch/scrape; search rows normalize to
`{url, title, description, position}` plus `date`/`source`/`score` when supplied, and the
router adds `backend` and `tier`; extract items are `{url, title, content}` with per-URL
`{url, error}` failures; a missing key is a configuration error naming the env var and the
signup URL; HTTP 402 and any 400/403/429 whose body mentions credits or quota is a quota
failure with `out_of_credit=True` (no retry, circuit open for an hour); 429 without credit
wording honours `Retry-After` and retries once. No adapter raises across the provider
boundary (`references/endpoints.md:195`).

### 2.2 Cache and circuit numbers (`PLAN.md:18-23`)

Fixed values, all configurable with these defaults (`PLAN.md:18-23`):

| Item | Value | Source |
|---|---|---|
| Search cache TTL, general | 900 s | `PLAN.md:18` |
| Search cache TTL, news | 300 s | `PLAN.md:18` |
| Extract cache TTL | 3600 s | `PLAN.md:19` |
| Negative cache (a failed URL) | 60 s | `PLAN.md:19-20` |
| Domain memory TTL | 24 h (86400 s) | `PLAN.md:20` |
| Circuit: consecutive failures to open | 3 | `PLAN.md:20` |
| Circuit: open duration after those failures | 15 min | `PLAN.md:20` |
| Circuit: open duration after one quota error | 60 min | `PLAN.md:20-21` |
| Circuit reset | a success resets both | `PLAN.md:21` |

Eviction, quoted verbatim from `PLAN.md:21-23`:

> Eviction: on write, if the DB exceeds 150 MB, delete expired rows first, then
> least-recently-hit rows down to 200 MB. One `VACUUM` per week, at most. All of it
> configurable with those defaults.

**Ambiguity, stated rather than resolved silently.** The trigger in `PLAN.md:21-22` is
150 MB and the post-eviction target is "down to 200 MB". Deleting rows cannot shrink a
database *up* to a larger number, so the two figures cannot both be literal. The cap is
independently fixed at 200 MB by `DESIGN.md:205` ("LRU eviction at a configurable cap,
default 200 MB") and `DESIGN.md:329` ("LRU to a 200 MB cap").

Reading T2 must implement, and the one this scaffold encodes (`cache_max_mb: 200` in
`plugin.yaml`): trigger when the store exceeds the 200 MB cap, delete expired rows first,
then least-recently-hit rows, down to a 150 MB low-water mark. That keeps the documented
200 MB cap and gives the 150 MB figure a job. This is a reconciliation of two contradictory
numbers, not a claim about what the author meant; Abhi can override it and then both this
section and the T2 tests change.

### 2.3 Pane layout (`PLAN.md:25-33`)

Right-side pane, 380 px (`PLAN.md:25`), containing:

- header: status dot, active backend count, Refresh (`PLAN.md:25-26`);
- a 12-row backend table — Name, Tier, Key, Health, p50, Calls, Spend — with a row that
  expands to the last error and a plan toggle (`PLAN.md:26-27`);
- a test-query box at the bottom whose result line names backend, tier and latency
  (`PLAN.md:27-28`).

The settings page holds tier order, per-backend plan, keyless toggle, cache TTL, timeouts,
the real-profile link and the two config-route buttons (`PLAN.md:28-30`).

Contribution areas and mechanics (`PLAN.md:30-33`): `PANES_AREA` with `placement: right`
and `width: 380px`, `registerSettingsPage`, `PALETTE_AREA` with entries "probe backends",
"test query", "clear cache"; poll `/api/plugins/sluice/status` every 5 s while visible; no
websockets; UI kit components only, theme variables only, no markup queries.

### 2.4 Demo target (`PLAN.md:35-39`)

`demo/fixture_server.py` serves deterministic cases: `/ok` (plain HTML), `/js` (content
injected by script), `/cf` (403 plus `cf-mitigated: challenge`), `/imperva` (200 with an
83-byte body and `x-iinfo`), `/ratelimit` (429 with `Retry-After: 2`). The challenge beat is
recorded against the fixture so the demo never depends on a live vendor; one real
Cloudflare-fronted page may be shown afterwards if it behaves (`PLAN.md:35-39`).

## 3. Task breakdown (T1-T14, `PLAN.md:41-173`)

Acceptance criteria are the bar for `kanban_complete`, not suggestions (`PLAN.md:3-5`).
The `Done:` line of each task is quoted from the plan.

### T1 — repository scaffold (`PLAN.md:43-54`)

- **Lines:** 43-54. Day 1. No dependencies.
- **Files:** `plugin.yaml`, `__init__.py`, `pyproject.toml`, `.gitignore`, `LICENSE` (MIT),
  `README.md` stub (`PLAN.md:45-46`).
- **Behaviour:** manifest carries `name: sluice`, version 1.0.0, `kind: backend`,
  `requires_hermes: ">=0.22.5"`, the four `python_dependencies` pins, `capabilities`
  matching exactly what `register(ctx)` does, and a `config_schema` with the settings and
  the ten secret fields (`PLAN.md:47-52`). `__init__.py` registers the provider class and
  CLI commands only, with lazy imports (`PLAN.md:53`).
- **Done:** "`hermes plugins doctor .` loads the plugin and reports zero errors."
  (`PLAN.md:54`)

### T2 — store (`PLAN.md:56-62`)

- **Lines:** 56-62. Day 1. Depends on T1.
- **Files:** `sluice/store.py`, `tests/test_store.py`.
- **Behaviour:** SQLite in `plugin_data_dir()` with `backend_state`, `usage`, `cache`,
  `domain_memory`, `jobs` (`PLAN.md:59`); the numbers in Closed gap 2 implemented exactly
  (`PLAN.md:60`, this file §2.2); thread-safe connections, WAL, no ORM (`PLAN.md:61`).
- **Done:** "tests cover circuit open/close, quota block, TTL expiry, LRU eviction, domain
  memory TTL." (`PLAN.md:62`)

### T3 — router (`PLAN.md:64-72`)

- **Lines:** 64-72. Day 1. Depends on T2.
- **Files:** `sluice/router.py`, `tests/test_router.py`.
- **Behaviour:** tier order local, keyless, free, paid; skip missing key, open circuit,
  exhausted quota, wrong capability; latency EMA ordering inside a tier (`PLAN.md:67`);
  2.5 s per-backend budget, 8 s total, sequential (`PLAN.md:68`); attach `backend`, `tier`,
  `cached`, `fallback_used`, `elapsed_ms` to every result (`PLAN.md:69`).
- **Done:** "tests prove a free backend beats a paid one, a failed backend falls through,
  and a total-budget expiry returns a clean error listing each backend's reason."
  (`PLAN.md:70-72`)

### T4 — adapters A (`PLAN.md:74-80`)

- **Lines:** 74-80. Day 1-2. Depends on T1.
- **Files:** `sluice/backends/searxng.py`, `ddgs.py`, `exa.py`, plus tests.
- **Behaviour:** follow `references/endpoints.md` exactly (`PLAN.md:77`); `ddgs` runs in a
  spawned child with a hard kill and a 12 s wall clock (`PLAN.md:78`); Exa supports keyless
  MCP and keyed REST behind one backend entry (`PLAN.md:79`).
- **Done:** "unit tests with recorded responses; one live call per mode behind an env flag."
  (`PLAN.md:80`)

### T5 — adapters B (`PLAN.md:82-86`)

- **Lines:** 82-86. Day 2. Depends on T4 (shares helpers).
- **Files:** `parallel.py`, `firecrawl.py`, `tavily.py`, `keenable.py`, tests.
- **Behaviour:** keyless and keyed modes per vendor; MCP helper reused for Parallel
  (`PLAN.md:85`).
- **Done:** "same bar as T4." (`PLAN.md:86`)

### T6 — adapters C (`PLAN.md:88-93`)

- **Lines:** 88-93. Day 2. Depends on T5.
- **Files:** `brave.py`, `perplexity.py`, `deepseek.py`, `tinyfish.py`, `xai.py`, tests.
- **Behaviour:** Brave's two-key trap and 422/402 mapping; DeepSeek 128-char clipping; xAI
  JSON-block parsing with annotation fallback (`PLAN.md:91`).
- **Done:** "same bar as T4, and each failure message names the actionable cause."
  (`PLAN.md:92-93`)

### T7 — fetch ladder (`PLAN.md:95-106`)

- **Lines:** 95-106. Day 3. Depends on T2.
- **Files:** `sluice/ladder.py`, `tests/test_ladder.py`, `tests/fixtures/blocks/*`.
- **Behaviour:** the nine classes ok, js_required, rate_limited, challenge, forbidden,
  auth_required, payment_required, policy_blocked, error (`PLAN.md:98-99`); robots.txt per
  origin cached 24 h, `X-Robots-Tag`, Content-Signal, a disallow returns
  `blocked_by_policy` with the rule quoted (`PLAN.md:99-100`); stages are honest
  `Sluice/1.0` httpx, real Chromium through Playwright with
  `--disable-blink-features=AutomationControlled`, host locale and timezone, cookies
  carried, 15 s cap, then Camofox REST when healthy, then stop (`PLAN.md:100-104`);
  401/402/403 stop, 429 honours `Retry-After`, no CAPTCHA, no identity change
  (`PLAN.md:105`).
- **Done:** "fixture tests cover every class, each stop rule, and the robots path."
  (`PLAN.md:106`)

### T8 — manager (`PLAN.md:108-117`)

- **Lines:** 108-117. Day 4. Depends on T1.
- **Files:** `sluice/manage.py`, tests.
- **Behaviour:** detect the camofox plugin and server health, Playwright browsers, the
  Browserbase key and packaged Chromium (`PLAN.md:111`); install Playwright and the camofox
  server (podman, then docker, then npm) as jobs, never automatic (`PLAN.md:112-113`);
  config route via `hermes config set ...` with the active profile env, 10 s timeout,
  `--apply` opt-in (`PLAN.md:114`).
- **Done:** "detection tests with stubbed binaries; a job record survives a restart."
  (`PLAN.md:115`)

### T9 — CLI (`PLAN.md:119-125`)

- **Lines:** 119-125. Day 4. Depends on T3, T7, T8.
- **Files:** `sluice/cli.py`, registered through `ctx.register_cli_command`
  (`PLAN.md:121`).
- **Behaviour:** `hermes sluice doctor`, `status`, `setup [--apply]`,
  `use search|extract`, `test --backend <id> "<query>"`, `cache clear`,
  `install playwright|camofox|browserbase` (`PLAN.md:122-123`); `doctor` prints the
  twelve-backend inventory with free/paid and what is missing (`PLAN.md:124`).
- **Done:** "every command runs unattended, exits non-zero on failure, prints the exact next
  command." (`PLAN.md:125`)

### T10 — plugin API (`PLAN.md:127-134`)

- **Lines:** 127-134. Day 5. Depends on T2, T3, T8.
- **Files:** `dashboard/plugin_api.py`, `dashboard/manifest.json`.
- **Behaviour:** `GET /status`, `POST /probe`, `POST /test`, `POST /install`,
  `GET /jobs/{id}`, `POST /cache/clear` (`PLAN.md:130-131`); a thin wrapper importing
  `sluice.*`, JSON only, no SSE (`PLAN.md:132`).
- **Done:** "routes mount at `/api/plugins/sluice/` after a gateway restart and answer
  under 300 ms with a warm store." (`PLAN.md:133-134`)

### T11 — desktop pane (`PLAN.md:136-142`)

- **Lines:** 136-142. Day 5. Depends on T10.
- **Files:** `desktop/plugin.js`.
- **Behaviour:** layout exactly as Closed gap 3 (`PLAN.md:139`, this file §2.3); no secrets,
  no app markup, no `eval`, imports only the SDK and React (`PLAN.md:140`).
- **Done:** "pane renders, refresh works, test query round-trips, settings page writes
  plugin settings, and `hermes plugins validate` passes the desktop surface check."
  (`PLAN.md:141-142`)

### T12 — skill, README, disclosures (`PLAN.md:144-150`)

- **Lines:** 144-150. Day 5-6. Depends on T9.
- **Files:** `skills/sluice/SKILL.md`, `README.md`.
- **Behaviour:** the skill explains when the ladder climbs, what the classes mean and how to
  read `doctor` (`PLAN.md:147`); the README discloses network calls to each vendor, local
  browser and server processes, shell commands for installs, the plugin data directory and
  stored credentials through Hermes (`PLAN.md:148`).
- **Done:** "README lists every vendor and every side effect; skill loads through
  `skill_view`." (`PLAN.md:149-150`)

### T13 — test sweep and validation (`PLAN.md:152-155`)

- **Lines:** 152-155. Day 5-6. Depends on all.
- **Behaviour:** `pytest` green; `hermes plugins validate . --install-deps` green;
  `hermes plugins doctor` clean (`PLAN.md:154`).
- **Done:** "both commands pass on the pinned commit, not the working tree."
  (`PLAN.md:155`)

### T14 — submission (`PLAN.md:157-162`)

- **Lines:** 157-162. Day 6. Depends on T13.
- **Files:** `demo/fixture_server.py`, `demo/script.md`.
- **Behaviour:** fixture per Closed gap 4 (§2.4); record under 90 s following `DESIGN.md`
  (`PLAN.md:160`); open the catalog PR with one entry pinning a full 40-character SHA and
  submit the Typeform (`PLAN.md:161`).
- **Done:** "PR URL and form confirmation recorded in the repo's `SUBMISSION.md`."
  (`PLAN.md:162`)

### Order, parallelism, slip rule (`PLAN.md:164-173`)

Day 1: T1 → T2 → T3 serial (`PLAN.md:166`). Day 2: T4, T5, T6 in parallel once helpers
exist (`PLAN.md:167`). Day 3: T7 alone (`PLAN.md:168`). Day 4: T8 and T9 in parallel
(`PLAN.md:169`). Day 5: T10 then T11, T12 alongside (`PLAN.md:170`). Day 6: T13, T14
(`PLAN.md:171`). "Anything that slips comes out of T6 paid adapters first, never out of T7
or T11." (`PLAN.md:173`)

## 4. Interfaces and APIs exposed

### 4.1 Hermes `WebSearchProvider`

Sluice registers exactly one provider, `name = "sluice"`, and advertises both capabilities
(`DESIGN.md:126-130`). The base class is `agent.web_search_provider.WebSearchProvider`;
these are the members it defines and Sluice overrides:

| Member | Contract |
|---|---|
| `name` | stable id matched by `web.search_backend`, `web.extract_backend`, `web.backend`; value `"sluice"` |
| `is_available()` | cheap, no network; true when a keyed backend has credentials |
| `is_keyless_available()` | cheap, no network; true when a keyless backend is enabled, even with zero keys; must never make `is_available()` true |
| `supports_search()` | true |
| `supports_extract()` | true |
| `search(query, limit=5)` | `{"success": True, "data": {"web": [{"title", "url", "description", "position", ...}]}}` |
| `extract(urls, **kwargs)` | `[{"url", "title", "content", "raw_content", "metadata"?, "error"?}, ...]`, per-URL failure never fails the batch |
| failure | `{"success": False, "error": str}`; nothing raises across the boundary |

Availability policy: `is_available()` is true when any keyed backend has credentials and
`is_keyless_available()` is true when at least one keyless backend is enabled, even with
zero keys; with no keys and keyless disabled both are false, "which is honest and lets
`hermes tools` say so" (`DESIGN.md:83-88`).

**T1 exception, recorded deliberately.** This scaffold returns `False` from both methods
because no backend is routable until T3 exists. T3 replaces the two method bodies with the
policy above. Reporting `True` here would light the native tools for a provider whose every
call returns a failure envelope. `tests/test_scaffold.py` pins the current behaviour so T3
must change it on purpose.

Extract provenance: `tools/web_tools_truncate._trim_results` strips every extract item to
`url`, `title`, `content`, `error`, `blocked_by_policy`, so `metadata` never reaches the
model; the block class and final stage ride as one short line at the top of `content`
(`DESIGN.md:73-77`). Search rows are not trimmed, so `backend` and `tier` stay as extra row
keys there.

### 4.2 `plugin.yaml` manifest and `config_schema`

Declared in `plugin.yaml` and checked by `hermes plugins validate`:

- `name: sluice`, `version: "1.0.0"`, `kind: backend`, `manifest_version: 2`,
  `requires_hermes: ">=0.22.5"`, `license: MIT`, `capabilities: []`.
- `provides_web_providers: ["sluice"]`.
- `python_dependencies`: `httpx>=0.27,<1`, `trafilatura>=1.8,<3`, `playwright>=1.45,<2`,
  `ddgs>=9,<10` (`PLAN.md:48`).
- `config_schema` settings: `tier_order`, `keyless_enabled`, `per_backend_budget_ms`,
  `total_budget_ms`, `search_ttl_general`, `search_ttl_news`, `extract_ttl`,
  `negative_ttl`, `domain_memory_ttl`, `circuit_failures`, `circuit_open_seconds`,
  `circuit_quota_seconds`, `cache_max_mb`, `searxng_url`.
- Ten secret fields, each `{type: secret, env: <VAR>, description: ...}` (`PLAN.md:49-52`):
  `exa_api_key` / `EXA_API_KEY`, `parallel_api_key` / `PARALLEL_API_KEY`,
  `firecrawl_api_key` / `FIRECRAWL_API_KEY`, `tavily_api_key` / `TAVILY_API_KEY`,
  `keenable_api_key` / `KEENABLE_API_KEY`, `brave_api_key` / `BRAVE_API_KEY`,
  `perplexity_api_key` / `PERPLEXITY_API_KEY`, `xai_api_key` / `XAI_API_KEY`,
  `deepseek_api_key` / `DEEPSEEK_API_KEY`, `tinyfish_api_key` / `TINYFISH_API_KEY`.

There is no plugin or CLI route that writes a web API key: keys reach Sluice through the
`config_schema` secret fields in Settings ▸ Plugins, written by Hermes' credential route;
the pane shows presence and links there (`DESIGN.md:102-105`).

### 4.3 CLI (`PLAN.md:119-125`)

| Command | Behaviour |
|---|---|
| `hermes sluice doctor` | twelve-backend inventory, free/paid, what is missing |
| `hermes sluice status` | circuit, quota, cache state per backend |
| `hermes sluice setup [--apply]` | print the config route; run it only with `--apply` |
| `hermes sluice use search\|extract` | switch that Hermes backend to `sluice` |
| `hermes sluice test --backend <id> "<query>"` | one backend, one query, latency reported |
| `hermes sluice cache clear` | clear the Sluice cache and `cache/web` |
| `hermes sluice install playwright\|camofox\|browserbase` | install as a job, never automatic |

Registered through `ctx.register_cli_command("sluice", ...)`; T1 ships the parser and a
dispatch that exits non-zero naming the implementing task.

### 4.4 Dashboard REST API (`PLAN.md:127-134`)

Mounted at `/api/plugins/sluice/` from `dashboard/plugin_api.py`, declared by
`dashboard/manifest.json` (Hermes rejects an `api` path outside `dashboard/`,
`DESIGN.md:96-101`):

| Route | Purpose |
|---|---|
| `GET /status` | backend table: tier, key present, health, p50, quota, calls, spend, last error |
| `POST /probe` | probe one or all backends |
| `POST /test` | run a test query, name backend, tier and latency |
| `POST /install` | start an install job |
| `GET /jobs/{id}` | job status, polled instead of holding a request open |
| `POST /cache/clear` | clear the cache |

JSON only, no SSE (`PLAN.md:132`); routes must mount after a gateway restart and answer
under 300 ms with a warm store (`PLAN.md:133-134`).

### 4.5 Desktop surface (`PLAN.md:136-142`)

`desktop/plugin.js`, plain ESM, importing only `@hermes/plugin-sdk`, `react` and
`react/jsx-runtime` (`DESIGN.md:96-101`), contributing a right-side 380 px pane, a settings
page and three palette entries (§2.3). Polls `/api/plugins/sluice/status` every 5 s while
visible; no websockets; UI kit components and theme variables only; never reads app markup.

## 5. Non-goals and anti-goals

Non-goals for v1 (`DESIGN.md:33-45`): no browser engine of our own and no custom CDP
provider (Hermes browses locally with packaged Chromium, and Camoufox is not
CDP-compatible); no automatic SearXNG deployment — detection plus a compose file, never a
silently started container; no answer synthesis and no deep research mode; no
CAPTCHA-solving services, no paywall or login circumvention, no forged identities; no
billing, key purchase or account management UI; no replacement of `web-search-plus`'s tool
names.

Anti-goals — "the build went wrong even if it runs" (`DESIGN.md:47-55`): a settings screen
that flips config but does not itself route traffic; fallbacks that hide which backend
answered; auto-installing hundreds of megabytes without an explicit user action; writing
`config.yaml` directly or patching Hermes core; a keyless tier that pretends to be a keyed
provider or silently outranks a paid key.

Out of scope for v1, candidates for v1.1 (`DESIGN.md:312-319`): parallel or racing fan-out
and result fusion; per-domain routing rules set by the user; TLS impersonation profiles;
answer synthesis and deep research mode; a web-dashboard (non-desktop) UI; additional
vendors beyond the twelve.

Hard rules that are requirements, not preferences: 401, 402 and 403 stop the ladder, 429
honours `Retry-After`, a CAPTCHA is never solved and identity is never changed after a
refusal (`PLAN.md:105`); installs never start automatically and the config route is
`--apply` opt-in (`PLAN.md:113-114`); the pane holds no secrets, reads no app markup and
uses no `eval` (`PLAN.md:140`).

## 6. Decisions taken at T1

These are scaffold decisions, each reversible by the task that owns the surface.

1. **Availability returns `False`** until T3 implements routing (§4.1). T3 owns the real
   policy from `DESIGN.md:83-88`.
2. **Eviction reads as a hysteresis band** — trigger at the 200 MB cap, evict down to a
   150 MB low-water mark (§2.2). The plan's two numbers cannot both be literal.
3. **Provider module has a fallback base class.** `sluice/provider.py` imports
   `agent.web_search_provider.WebSearchProvider` when Hermes is present and falls back to a
   minimal local base only when the `agent` package is genuinely missing, so standalone
   `pytest` and `hermes plugins validate` (which imports `register()` in a bare interpreter)
   both work. A missing transitive dependency inside `agent` is re-raised, never masked.
4. **The root `__init__.py` is a directory-plugin wrapper.** Hermes imports it as
   `hermes_plugins.<name>` with the repository root as its package path, so it imports
   `sluice.*` relatively. The pip package (`sluice/`) and the plugin wrapper share code;
   nothing outside the wrapper imports Hermes.
5. **CLI handlers exit non-zero until implemented**, naming the task that implements them
   (`PLAN.md:125` requires non-zero on failure). T9 replaces the dispatch bodies.
