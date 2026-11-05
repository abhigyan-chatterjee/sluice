# Architecture

Sluice is one Hermes plugin with two jobs: it serves the native `web_search` and
`web_extract` tools through a single provider, and it owns the state that makes free-first
routing honest (health, latency, quota, circuit, cache). This page maps the modules, the two
request paths, the Hermes seams the plugin hooks into, and the places a contributor can
extend it.

```
Hermes tool layer
  web_search(query, limit) ─┐
  web_extract(urls, ...)  ──┤
                            ▼
              SluiceWebSearchProvider          sluice/provider.py:104
                            │
                    ┌───────┴────────┐
                    ▼                ▼
                 Router           Router.extract     sluice/router.py:272 / :442
              (search path)      (per-URL path)
                    │                │
        ┌───────────┴───┐            ▼
        ▼               ▼        Fetch Ladder          sluice/ladder.py:734
  tier candidates   cache     http → chromium → camofox
        │
        ▼
  sluice/backends/<vendor>.py   (12 adapters, one module each)
        │
        ▼
  sluice/store.py  (SQLite: cache, backend_state, usage, domain_memory, jobs)
```

## Module map

| File | Job | Key lines |
|---|---|---|
| `__init__.py` | `register(ctx)`: provider, `hermes sluice` command tree, bundled skill. All imports are function-local so the validation probe can import the file without Hermes. | `22-65` |
| `sluice/provider.py` | The single `WebSearchProvider` named `sluice`. Capability flags, availability, lazy router/store construction, failure envelopes. | `104-216` |
| `sluice/config.py` | Runtime view of `plugin.yaml config_schema`: defaults, saved settings, credentials, tier normalization, plugin data directory. | `45-229` |
| `sluice/router.py` | Tier policy, candidate ordering, budgets, fallback, cache keys, winner metadata. | `198-497` |
| `sluice/backends/__init__.py` | `BackendSpec` registry, `Backend` base class, mode and tier resolution, `make_backend`, `specs`. | `28-249` |
| `sluice/backends/*.py` | One self-contained HTTP adapter per vendor. No Hermes imports. | 12 modules |
| `sluice/ladder.py` | Nine-class block classifier, robots and policy checks, the three-stage fetch ladder. | `33-61`, `108-179`, `683-824` |
| `sluice/store.py` | SQLite store: `backend_state`, `usage`, `cache`, `domain_memory`, `jobs`, plus internal `meta`. WAL, one connection, re-entrant lock, no ORM. | `59-78`, `132-201` |
| `sluice/manage.py` | Detection and install jobs for the browser engines, the `hermes config set` route, outer cache clearing. | `37-44`, `170-214`, `388-463` |
| `sluice/cli.py` | The seven `hermes sluice` subcommands and the standalone `sluice` console script. | `344-434` |
| `sluice/api.py` | Dashboard behaviour with no web framework in it: status, probe, test, install, jobs, cache, settings, setup. | `141-445` |
| `dashboard/plugin_api.py` | FastAPI wrapper mounted at `/api/plugins/sluice/`. Thin handlers around `sluice.api`, each wrapped in `_safe`. | `28-107` |
| `dashboard/manifest.json` | Declares `entry: dist/index.js` and `api: plugin_api.py`; `tab.hidden: true` because the pane is the UI. | whole file |
| `dashboard/dist/index.js` | Dashboard loader hook (`registry.register('sluice', ...)`); no tab. | whole file |
| `desktop/plugin.js` | Desktop pane, settings page, three palette commands. Plain ESM, imports only the SDK and React. | `669-745` |
| `skills/sluice/SKILL.md` | When the ladder climbs, what the nine classes mean, how to read `doctor`. Registered through `ctx.register_skill`. | whole file |
| `demo/fixture_server.py` | Deterministic HTTP targets for the demo and the ladder tests. | `87-123` |
| `docker/searxng/` | Reference SearXNG with `search.formats: [html, json]`, bound to localhost. | whole file |

## Build plan traceability

Each module carries the `PLAN.md` task that built it: T1 scaffold (`plugin.yaml`,
`__init__.py`, `pyproject.toml`), T2 store, T3 router, T4 to T6 adapters (each
`BackendSpec.task` names T4, T5 or T6), T7 Fetch Ladder, T8 manager, T9 CLI, T10 dashboard
API, T11 pane, T12 skill and disclosures, T13 test sweep, T14 demo fixture and submission.

The four items `PLAN.md` settled before the build are its "Closed gaps": vendor payloads in
`references/endpoints.md`, the cache and circuit numbers implemented in `sluice/store.py`,
the pane layout (`PANES_AREA`, `registerSettingsPage`, `PALETTE_AREA`), and the deterministic
demo fixture in `demo/fixture_server.py`.

## The two tool contracts

Search returns an envelope, never an exception. The envelope shape:

```json
{"success": true, "data": {"web": [
  {"url": "...", "title": "...", "description": "...", "position": 1,
   "backend": "exa", "tier": "keyless", "cached": false,
   "fallback_used": false, "elapsed_ms": 886.2}
]}}
```

Failure is `{"success": false, "error": "..."}` where the error lists every backend's
reason (`sluice/router.py:375-380`). An empty query is rejected before routing
(`sluice/provider.py:203-204`).

Extract returns one item per URL, and one bad URL never fails the batch
(`sluice/provider.py:210-216`). This is a real item for the `/js` fixture page, which the
ladder had to read through Chromium:

```json
{
  "url": "http://127.0.0.1:44431/js",
  "title": "Sluice fixture: JS shell",
  "content": "[sluice: stage=chromium status=ok]\n\nLoaded by script\nThis sentence only exists after JavaScript runs.",
  "raw_content": "Loaded by script\nThis sentence only exists after JavaScript runs.",
  "block_class": "ok",
  "stage": "chromium",
  "stages": [
    {"stage": "http", "block_class": "js_required", "status": 200, "ms": 43.2,
     "skipped": false, "reason": "", "signals": []},
    {"stage": "chromium", "block_class": "ok", "status": 200, "ms": 2800.9,
     "skipped": false, "reason": "", "signals": []}
  ],
  "blocked_by_policy": false,
  "status": 200,
  "backend": "ladder", "tier": "local", "cached": false,
  "fallback_used": false, "elapsed_ms": 3434.5
}
```

The provider boundary is total: no exception crosses `provider.search` or
`provider.extract`, which is what lets cron, gateway and subagent sessions use the native
tools safely.

## Search path, ingestion to result

1. Hermes calls `web_search`, which calls `SluiceWebSearchProvider.search`
   (`sluice/provider.py:201`). The provider lazily builds one `Router`, and with it one
   `Store`, per provider instance (`sluice/provider.py:170-187`).
2. `Router.search` coerces the limit (`sluice/router.py:145-158`), classifies the query as
   `news`, `docs` or `general` (`119-126`), picks the cache namespace and TTL from that
   class, and builds the cache key from the normalized query, limit, freshness and class
   (`133-138`).
3. Cache lookup. A hit is re-stamped `cached=true`, `elapsed_ms=0`, and is only returned if
   the backend that produced it is still routable; a cached answer from a backend whose key
   was removed or whose circuit is open is treated as a miss (`sluice/router.py:291-309`,
   `382-394`).
4. `Router.candidates` builds the candidate list (`198-238`): drop backends without the
   requested capability, without a usable access mode, outside `tier_order`, or with an open
   circuit or exhausted quota. Sort by tier index, then query-class preference, then
   latency EMA, then id.
5. Sequential attempts. Before each one: re-check the circuit, compute the remaining total
   budget, and set `timeout = min(per_backend, remaining)` (`322-332`). The timeout is
   handed to the adapter, so the budget is enforced, not measured after the fact.
6. On the first success: stamp every row with `backend`, `tier`, `cached`,
   `fallback_used` (true if any earlier candidate failed) and `elapsed_ms` (`416-438`),
   record health and latency, cache the rows without the per-request metadata, and return
   (`337-362`).
7. On failure: record it, including quota classification, and continue. When the list is
   exhausted, append `skip_reasons()` and return one error that names each backend and its
   reason (`364-380`).

Cache writes go through `Store.cache_put`, which also runs eviction (`sluice/store.py:304`).
Cache reads update `last_hit_at` and `hits`, which is what the LRU phase sorts on.

## Extract path, ingestion to result

Extraction is local only. The router never calls a vendor extract endpoint; those adapters
exist for `hermes sluice test --backend <id>` and for probes (`sluice/router.py:442-447`).

1. `Router.extract` loops the URLs and calls `_extract_one` per URL (`450-453`).
2. Cache lookup in `extract` and `extract:negative`, keyed by URL and format
   (`455-463`). A negative hit returns the cached failure.
3. `sluice.ladder.fetch` (`734-876`): reject non-HTTP schemes, then check policy. Robots is
   fetched once per origin and cached 24 h (`289-328`); a disallow returns
   `blocked_by_policy` with the rule quoted, and no page request is made.
4. The start rung comes from `domain_memory`: the stage that worked last time, or Chromium
   when the domain previously challenged (`889-902`).
5. Stage 1, `http` (`400-464`): `httpx` with `User-Agent: Sluice/1.0`, no browser-only
   headers, trafilatura for text, a clean-text fallback when trafilatura refuses. A 429
   honours `Retry-After` and retries once if the wait is at most 5 s; a 5xx retries once.
6. Stage 2, `chromium` (`515-621`): the full Chromium build through Playwright, launched
   `headless=False` with `--headless=new` so it is the real browser and not the headless
   shell, `--disable-blink-features=AutomationControlled`, host locale and timezone, cookies
   carried from stage 1, 15 s cap. Playwright's managed build is used when present; when it
   is absent, a Chromium/Chrome already on `PATH` is passed as `executable_path`.
7. Stage 3, `camofox` (`623-732`): only when `GET /health` reports `ok`, opens a tab, reads
   the snapshot text, closes the tab.
8. After every response with headers, `X-Robots-Tag` and `Content-Signal` are checked
   (`349-364`, `809-825`). A refusal stops the ladder as `policy_blocked` even if the body
   was readable.
9. Stop rules (`45-47`, `847-850`): `forbidden`, `auth_required`, `payment_required`,
   `policy_blocked` and `error` stop; `rate_limited` stops after its single retry;
   `challenge` and `js_required` climb. A CAPTCHA is never solved and the identity is never
   changed.
10. The item is assembled by `_item` (`918-950`) with one provenance line at the top of
    `content`, `[sluice: stage=<stage> status=<class>]`, because Hermes trims extract items
    down to `url`, `title`, `content`, `error` and `blocked_by_policy`
    (`tools/web_tools_truncate._trim_results`). Domain memory and the positive or negative
    cache row are written, then the router stamps `backend=ladder`, `tier=local` and
    `elapsed_ms` (`482-497`).

## The nine block classes

`BLOCK_CLASSES` (`sluice/ladder.py:33-43`), classified by `classify()`
(`sluice/ladder.py:108-179`) from status, vendor headers, cookies, body markers and body
size. Vendor markers are checked before status codes because status alone lies: Cloudflare
challenges arrive as 2xx or 403, Imperva answers 200 with an 83-byte body, AWS WAF uses
202/405, Kasada 429 and Fastly 406.

| Class | Meaning | Ladder |
|---|---|---|
| `ok` | readable page | stops, returns content |
| `js_required` | shell page, content injected by script | climbs |
| `rate_limited` | 429, or 503 with `Retry-After` | one `Retry-After` retry in stage 1, then stops |
| `challenge` | Cloudflare, Akamai, DataDome, Imperva, Kasada, Fastly, AWS WAF or HUMAN marker | climbs |
| `forbidden` | plain 403 with no challenge marker | stops |
| `auth_required` | 401 | stops |
| `payment_required` | 402, or a `crawler-price` header | stops |
| `policy_blocked` | robots.txt, `X-Robots-Tag` or `Content-Signal` refuses | stops, rule quoted, `blocked_by_policy: true` |
| `error` | transport, parse or 5xx failure | stops |

A domain that challenged is remembered for 24 h, so the next fetch on that origin starts at
Chromium instead of repeating the HTTP rung.

## State

`sluice/store.py` owns one SQLite file, `sluice.db`, in the plugin data directory
(`ctx.plugin_data_dir()`, normally `~/.hermes/plugin-data/sluice/sluice.db`).

| Table | Holds | Written by |
|---|---|---|
| `cache` | namespaced JSON values with `expires_at`, `last_hit_at`, `hits` | `cache_put` (`304`), read and touched by `cache_get` (`327`) |
| `backend_state` | per-backend plan, circuit and quota windows, consecutive failures, latency EMA and the last 20 samples, last error and last success | `record_success` (`555`), `record_failure` (`599`), `set_plan` (`521`) |
| `usage` | one row per call: backend, kind, ok, latency, cost, timestamp | `_record_usage` (`646`) |
| `domain_memory` | domain, block class, stage that worked, signals, expiry | `domain_memory_put` (`721`) |
| `jobs` | install jobs: kind, target, status, command, output, error, timestamps | `create_job` (`754`), `update_job` (`775`) |
| `meta` | maintenance bookkeeping, currently the last VACUUM time | `_set_meta` (`295`) |

Connections are opened with `check_same_thread=False`, WAL journalling and a 15 s busy
timeout; every public method takes a re-entrant lock (`sluice/store.py:210-241`). A job left
`queued` or `running` by a dead process is marked failed on the next open
(`recover_stale_jobs`, `807-817`).

### Threshold reconciliations

The two `PLAN.md` vs `DESIGN.md` conflicts are resolved in this module and in the tests.
`docs/configuration.md` carries the user-facing version; the short version:

1. **Cache eviction.** Trigger at `cache_max_mb` (200 MB), evict expired rows, then LRU rows
   until the live payload reaches `cache_low_water_mb` (150 MB), checkpoint the WAL, and run
   `VACUUM` at most once per `vacuum_interval_seconds` (`sluice/store.py:403-466`). This
   gives both plan numbers a job, since deleting rows cannot shrink a database up to
   200 MB.
2. **Circuit.** `circuit_open_seconds` (900) after `circuit_failures` (3) consecutive
   failures, `circuit_quota_seconds` (3600) after one quota error, no exponential backoff,
   success resets both (`sluice/store.py:590-631`). `REQUIREMENTS.md` §2.2 records that this
   follows `PLAN.md` over `DESIGN.md`.

## Where Hermes hooks in

| Seam | Used at | What it does |
|---|---|---|
| `__init__.py:register` | `__init__.py:22-65` | The plugin entry point. Registers the provider, the CLI command tree and the bundled skill. Raises a named `RuntimeError` on a Hermes without `ctx.register_web_search_provider`. |
| `SluiceWebSearchProvider` | `sluice/provider.py:104` | The `WebSearchProvider` subclass. `supports_search`/`supports_extract` both true; `is_available` true with any key or a SearXNG URL; `is_keyless_available` true while the keyless tier is enabled; `get_setup_schema` advertises no env prompt because keys live in Settings. |
| `ctx.get_config` | `sluice/config.py:131-140` | Reads each setting with its manifest default as the fallback, so an unset key keeps the default instead of becoming `None`. |
| `agent.web_search_provider.get_provider_env` | `sluice/config.py:111`, `sluice/provider.py:90` | Config-aware, profile-scope-aware credential lookup. Wrapped so an unscoped secret or a broken config read is treated as absent instead of raising. |
| `tools/web_tools_truncate._trim_results` | worked around at `sluice/ladder.py:16-18`, `918-950` | The wrapper strips every extract key except `url`, `title`, `content`, `error`, `blocked_by_policy`. Sluice puts the block class and final stage in one header line at the top of `content`; the full chain stays in `stages` for the pane and the API. Search rows are not trimmed, so `backend` and `tier` survive as extra row keys. |
| `tools/web_result_cache` | `sluice/manage.py:331-356`, `sluice/cli.py:304` | Hermes' outer cache (20 minutes by default). `setup --apply` turns it off so one tier-aware cache governs; `cache clear` clears `cache/web` as well as the SQLite cache. |
| `dashboard/manifest.json` | whole file | Declares `api: plugin_api.py` (Hermes rejects an API path outside `dashboard/`) and `entry: dist/index.js` for the loader. `tab.hidden: true`: the pane is the UI. |
| `dashboard/plugin_api.py` | `28-107` | Mounts at `/api/plugins/sluice/`. Routes: `GET /status`, `POST /probe`, `POST /test`, `POST /install`, `GET /jobs/{id}`, `POST /cache/clear`, plus `GET/POST /settings` and `POST /setup` for the pane. Every handler is wrapped in `_safe`, so a failure is a JSON body, not a 500. |
| `desktop/plugin.js` | `669-745` | Default export `{id, name, register(ctx)}`. `ctx.register({area: PANES_AREA, data: {placement: 'right', width: '380px'}})` for the pane; `ctx.registerSettingsPage` for the settings page; three commands under `PALETTE_AREA`: `sluice.probe`, `sluice.test`, `sluice.cache.clear`. Polls `/status` every 5 s while visible (`POLL_MS`, lines 27 and 96-114) and holds no secret, no `eval`, no DOM markup queries. |
| `ctx.register_cli_command` | `__init__.py:43-52` | Registers `sluice` with `setup_fn`/`handler_fn`, which is how `hermes sluice <command>` reaches `sluice/cli.py`. |
| `ctx.register_skill` | `__init__.py:57-65` | Registers `skills/sluice/SKILL.md` when the host supports it. Optional: a missing skill file must not stop the provider from loading. |

## Extension points

### Add a backend adapter

1. Create `sluice/backends/<id>.py` with a module-level `SPEC = BackendSpec(...)` and a
   `<Stem>Backend(Backend)` class (`sluice/backends/__init__.py:54-188`). Set
   `capabilities` to `("search",)`, `("extract",)` or both, and return the shared
   `success()`/`failure()` envelopes from `sluice/backends/_http.py:107-147`. Do not raise
   across the adapter boundary.
2. Add the module name to `BACKEND_MODULES` (`sluice/backends/__init__.py:28-41`) and add the
   id to `_class_stem` (`218-232`), which maps id to class name.
3. If the vendor takes a key, declare it as a `type: secret` field with `env:` in
   `plugin.yaml`, add the env name to `sluice/config.py:SECRET_ENVS` (`67-78`) and to
   `sluice/provider.py:SECRET_ENVS` (`67-78`).
4. If it has a published free allowance, add it to `FREE_PLAN_VENDORS`
   (`sluice/config.py:23`) so `plan_<id>` defaults to `free`.
5. Tests that move with the change: `tests/test_scaffold.py` asserts twelve vendors and the
   tier counts (local 1, keyless 6, free 4, paid 10), `tests/test_scaffold.py` also asserts
   exactly ten `type: secret` fields, `tests/test_config.py` asserts manifest defaults match
   `DEFAULTS`, and adapter behaviour belongs in `tests/test_backends.py`.

`hermes sluice doctor`, the pane and `/status` all read `specs()`, so a registered adapter
appears in every surface without further wiring.

### Extend the ladder

- Add or reorder rungs in `STAGES` (`sluice/ladder.py:54`) and implement the stage function
  with the `StageOutcome` shape (`370-397`).
- Adjust which classes stop or climb in `STOP_CLASSES` and `CLIMB_CLASSES` (`46-51`).
- Add classifier signals in `_has_challenge_marker` (`152-179`) and fixtures under
  `tests/fixtures/blocks/`; `tests/test_ladder.py` covers every class and stop rule.

### Extend the API or the CLI

- API: add a payload function to `sluice/api.py` that never raises and never returns a
  secret, then a route in `dashboard/plugin_api.py` wrapped in `_safe`. The pane reaches it
  with `ctx.rest('/path')`.
- CLI: add a subparser in `sluice/cli.py:_add_subcommands` (`363-391`) and its handler in
  `_HANDLERS` (`344-352`), then extend the parametrized command list in
  `tests/test_scaffold.py:199-214`.
- Settings: add the key to `plugin.yaml config_schema` and to `config.DEFAULTS`
  (`tests/test_config.py` fails if they drift), and add it to `sluice/api.py:EDITABLE_KEYS`
  only if the pane may write it.

## Not yet implemented

Out of scope for v1, recorded in `DESIGN.md:312-319` and `PLAN.md`:

- Parallel or racing fan-out and result fusion. Routing is sequential.
- Per-domain routing rules set by the user. `domain_memory` is automatic, not configurable.
- TLS impersonation profiles. The HTTP rung identifies itself honestly as `Sluice/1.0`.
- Answer synthesis and deep research mode.
- A web dashboard that is not the Desktop pane. `dashboard/dist/index.js` registers no tab.
- The `openai-native` backend. It is a chat-completions feature, not a search endpoint.
- A custom CDP browser provider. Camoufox speaks Juggler, not CDP, so Sluice integrates the
  Camofox REST server instead.
- Automatic SearXNG deployment. `docker/searxng/` is a reference compose file; nothing
  starts it for you.
- CAPTCHA solving, paywall or login circumvention.
- Per-call backend pinning in `web_search(query, limit)` or `web_extract(urls, char_limit)`.
  The native signatures have no slot for it; pinning is `hermes sluice test --backend <id>`.
- `SUBMISSION.md` and the 90-second demo recording (`PLAN.md` T14). These are external
  submission artifacts, not runtime code; `demo/script.md` is the shooting script.

## Directory-plugin namespace

Hermes imports this repository as `hermes_plugins.sluice`, and that package name exists only
in the parent process. The `ddgs` rung therefore runs its query in a `subprocess` `-c` script
(JSON request on stdin, one JSON object on stdout) with the repository root first on
`PYTHONPATH`; a `multiprocessing` spawn would fail in the child trying to import
`hermes_plugins` (`sluice/backends/ddgs.py:53-166`).

`SEARXNG_URL` is read as a fallback for the `searxng_url` setting; an explicit plugin setting
wins (`sluice/config.py:154-159`).
