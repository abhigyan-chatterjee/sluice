# Sluice: design (locked)

Status: locked v3, 2026-11-05. Awaiting the build go-ahead.
Owner: Abhi
Target: Hermes Agent Plugin Hackathon. Submission 2026-11-11 17:00 EST (2026-11-12 03:30 IST).

## Intent

Sluice is one plugin that owns Hermes' web layer. It serves the native `web_search`
and `web_extract` tools through a single routing provider, and it manages every web
backend a user has: local, keyless, free-plan and paid.

The gap it fills: Hermes ships a provider registry and a hand-edited config key, but no
surface to install, test, monitor or switch backends. web-search-plus adds a second tool
surface instead of serving the native one, and does not treat free tiers as first-class.
Users end up with overlapping plugins, no shared health state, and a fallback chain they
cannot see.

## Goals

- Native `web_search` and `web_extract` served by one Sluice provider, so cron sessions,
  gateway sessions and subagents get the same routing without learning a tool name.
- Free-first tiered routing: self-hosted, then keyless public tiers, then free-plan keys,
  then paid keys. Paid is last by default, never exclusive.
- Per-backend health, latency, quota and circuit state persisted across restarts.
- Search cache and extract cache with TTLs and a size cap.
- A local-only extract ladder: plain HTTP plus readability, real Chromium, Camofox, with a
  block classifier deciding when to climb and when to stop.
- One control surface: `hermes sluice` CLI plus a Hermes Desktop pane.
- Unattended-safe: no prompts that hang cron or gateway runs; failures are clean and
  reported.

## Non-goals

- No browser engine of our own, and no custom CDP provider. Hermes already browses
  locally with packaged Chromium; Camoufox is not CDP-compatible, so it cannot fill the
  `browser.cloud_provider` slot.
- No automatic SearXNG deployment in v1. Sluice detects a running instance and ships a
  compose file, but does not silently start containers.
- No answer synthesis, no deep research mode.
- No CAPTCHA-solving services, no paywall or login circumvention, no forged identities. The
  block work is about being a correct, well-behaved client, not about defeating access
  controls.
- No billing, key purchase or account management UI.
- No replacement of web-search-plus's tool names. Sluice serves the native tools instead.

## Anti-goals

Anything below means the build went wrong even if it runs:

- A settings screen that flips config but does not itself route traffic.
- Fallbacks that hide which backend answered.
- Auto-installing hundreds of megabytes without an explicit user action.
- Writing `config.yaml` directly, or patching Hermes core.
- A keyless tier that pretends to be a keyed provider or silently outranks a paid key.

## Verified constraints

Each of these was checked against the installed source (v0.22.6), not assumed:

1. `ctx.set_config` writes only `plugins.entries.<id>.settings.*`. Hermes-level keys such
   as `web.search_backend`, `browser.cloud_provider` and `browser.use_real_profile` must go
   through the public `hermes config set` route. Sluice prints that command and can run it
   with an explicit `--apply`, never by editing the file.
2. Provider selection is: explicit `web.search_backend` / `web.extract_backend`, then the
   single eligible provider, then a hardcoded legacy walk, then keyless. Sluice's id is in
   none of those lists, so installing it changes nothing until the config key names it.
3. Extract returns per-URL items (`url`, `title`, `content`, `raw_content`, `metadata`,
   `error` per URL). Search returns `{"success": True, "data": {"web": [...]}}`. Failure is
   `{"success": False, "error": str}`. Nothing may raise across the boundary. The wrapper
   applies `web.extract_char_limit` and stores the full text, so the adapter must not
   pre-truncate.
4. `tools/web_tools_truncate._trim_results` strips every extract item down to `url`,
   `title`, `content`, `error` and `blocked_by_policy`. `metadata` never reaches the model
   on `web_extract`, so extract provenance rides as one short line at the top of
   `content`, never in `metadata`. Search rows are not trimmed that way, so `backend` and
   `tier` can stay as extra row keys there.
5. `tools/web_result_cache.py` already caches both search and extract outside the provider,
   20 minutes by default (`web.cache_ttl_minutes`; `web.cache_enabled: false` disables).
   Sluice's own cache never sees a repeat inside that window. Setup therefore turns the
   outer cache off when Sluice owns the backend, so one tier-aware cache and one TTL
   govern, and `hermes sluice cache clear` clears `cache/web` too.
6. Availability gating: `check_web_api_key()` lights the native tools when the configured
   or active provider reports `is_available()` or, through `_provider_is_ready`,
   `is_keyless_available()`. Sluice returns True from `is_available()` when any keyed
   backend has credentials, and True from `is_keyless_available()` when at least one
   keyless backend is enabled, even with zero keys. With no keys and keyless disabled it
   reports False from both, which is honest and lets `hermes tools` say so.
7. Camoufox speaks Juggler through Playwright, not CDP. The official `camofox` plugin wraps
   a Node REST server (`localhost:9377`) and registers its own browser toolset
   (`browser_navigate`, `browser_snapshot`, ...). Sluice integrates with that plugin; it
   does not emulate it. Hermes core treats `browser.cloud_provider: camofox` as "local,
   not a CDP provider".
8. SearXNG disables JSON output by default; `settings.yml` needs `search.formats: [html,
   json]`. The compose file Sluice ships must include that.
9. The Desktop SDK accepts a unified package at
   `$HERMES_HOME/plugins/sluice/desktop/plugin.js`, plain ESM, importing only
   `@hermes/plugin-sdk`, `react` and `react/jsx-runtime`. The backend must live at
   `dashboard/plugin_api.py` with `dashboard/manifest.json` naming it, because Hermes
   rejects any `api` path that resolves outside `dashboard/`. The pane reaches it at
   `/api/plugins/sluice/`.
10. There is no plugin or CLI route that writes a web API key. `hermes auth` is for
    inference credentials and `hermes secrets` is for external vaults. Keys reach Sluice
    through the `config_schema` secret fields in Settings ▸ Plugins, written by Hermes'
    credential route; the pane shows presence and links there.
11. Installed Hermes is v0.22.6, so `requires_hermes: ">=0.22.5"` is legal and tests the
    surface we built against.

## Architecture

### Components

| File | Job |
|---|---|
| `__init__.py` | `register(ctx)`: provider, CLI commands, bundled skill |
| `sluice/router.py` | tier policy, candidate selection, timeouts, fallback, winner metadata |
| `sluice/backends/*.py` | one module per backend: `probe`, `search`, `extract`, metadata |
| `sluice/ladder.py` | block classifier and escalation ladder |
| `sluice/store.py` | SQLite state and caches in `plugin_data_dir()` |
| `sluice/manage.py` | detect/install/start/stop for camofox, Playwright Chromium, Browserbase; config route |
| `dashboard/plugin_api.py` | REST for the pane: status, probe, test, jobs, cache clear |
| `dashboard/manifest.json` | declares the API file and the pane |
| `desktop/plugin.js` | pane, settings page, palette commands |
| `skills/sluice/SKILL.md` | when to use which backend, free-first policy, troubleshooting |

### Provider

One `WebSearchProvider` named `sluice`, registered once. It advertises both search and
extract. It reads its own settings through `ctx.get_config` and keys through
`agent.secret_scope.get_secret`, so profile multiplexing is respected.

Backends are self-contained HTTP adapters. They do not import Hermes' built-in provider
modules: those are undocumented internals, they change, and a catalog reviewer flags the
import. Endpoints and payload shapes live in `references/endpoints.md`.

### Tier policy

One entry per vendor, not one per access mode. A vendor declares the access modes it
offers, and the router uses the cheapest enabled mode it has: `keyless`, then `free` (a
free-plan key), then `paid`. That matches reality: Exa, Tavily, Firecrawl and Keenable use
the same key on a free allowance and a paid plan, and their APIs do not say which.

v1 vendors, twelve in total:

| Tier | Vendors |
|---|---|
| Local | SearXNG |
| Keyless | ddgs, exa, parallel, firecrawl, tavily, keenable |
| Free plan | brave, tavily, exa, firecrawl |
| Paid | exa, parallel, firecrawl, tavily, keenable, brave, perplexity, xai, deepseek, tinyfish |

The list is the set Hermes itself ships (brave-free, ddgs, searxng, exa, firecrawl,
keenable, parallel, perplexity, tavily, xai) plus DeepSeek and TinyFish by request.
`openai-native` is out of v1: it is a chat-completions feature, not a search endpoint.

No per-call pinning in v1. The native tool signatures (`web_search(query, limit)`,
`web_extract(urls, char_limit)`) have no slot for a backend argument, and a query-prefix
operator would collide with real search syntax. Pinning lives in settings and in
`hermes sluice test --backend <id> "<query>"`.

Free versus paid is a property of the backend entry, not of the credential. Exa, Tavily
and Firecrawl use one key whether the account sits on a free allowance or a paid plan, and
their APIs do not expose which. Each backend carries a `plan: free|paid` setting that
defaults to `free` for vendors with a published free tier and `paid` otherwise; the pane
shows a toggle so the ordering matches reality.

Within a tier, candidates sort by latency EMA among healthy backends. A backend is
skipped when its key is missing, its circuit is open, its quota window is exhausted, or
the query type it cannot serve (news vs docs) is requested and another can. Each search
gets a 2.5 s per-backend budget and an 8 s total budget. A light query classifier biases
order inside a tier; it never promotes a paid backend above a free one.

### Extract chain and fetch ladder

Extraction is local only. The chain never calls a paid extract API:

1. Plain `httpx` plus trafilatura. trafilatura is Apache-2.0 from v1.8.0, pin `>=1.8,<3`.
   The client identifies itself honestly as `Sluice/1.0` and sends no browser-only headers.
   A browser user agent appears only when a real browser is making the request.
2. Real Chromium through Playwright: the full build, never the headless shell, with
   `--disable-blink-features=AutomationControlled`, host locale and timezone, cookies
   carried from stage 1, and a 15 s cap.
3. The Camofox REST server, only when installed and healthy.

Each stage runs inside the Fetch Ladder: a deterministic block classifier reads status,
vendor headers, cookies and body, names the class, and decides whether to climb or stop.
The full design is in `RESEARCH-blocks.md`. Hard rules: 401, 402 and 403 stop the ladder,
429 honours `Retry-After`, a CAPTCHA is never solved, identity is never rotated after a
refusal, and a robots.txt or Content-Signal disallow returns `blocked_by_policy` with the
rule quoted.

`ddgs` can hang inside native code while holding the GIL, so it runs in a disposable child
process with a hard kill, never a thread timeout.

Per-URL isolation; a failed URL returns its own error item and never fails the batch.
Because `_trim_results` drops `metadata`, the block class and final stage are recorded as a
single header line at the top of `content`, with the full chain kept in SQLite and the pane.

### State and cache

SQLite in the plugin data directory: `backend_state`, `usage`, `cache`, `domain_memory`.
Circuit: three consecutive failures or one quota error opens it for 15 minutes, with
exponential backoff capped at an hour. Cache keys are normalized query plus filters plus
limit, TTL 15 minutes general and 5 minutes for news; extract cache is keyed by URL and
format with a 1 hour TTL. LRU eviction at a configurable cap, default 200 MB.
`domain_memory` stores `(domain, block_class, stage_that_worked, timestamp)` with a 24 hour
TTL, so a site that always challenges starts at the browser rung next time.

The outer Hermes cache is turned off during setup (`web.cache_enabled: false`) so there is
exactly one cache with one TTL; `hermes sluice cache clear` clears both anyway.

### Manager

Detect: camofox plugin installed and enabled, server health on its configured URL,
Playwright browser cache, Browserbase key, packaged Chromium.

Install (always an explicit user action, never automatic):
`playwright install chromium`; the camofox server through the user's container runtime
(podman first, then docker, then an npm checkout in a managed directory), with the
first-run banner that downloads Camoufox stated up front. Long installs run as jobs behind
`dashboard/plugin_api.py`; the pane polls job status instead of holding a request open.

Toggling Camofox on when it is not installed fails loudly, never silently: the pane and
`hermes sluice doctor` name what is missing (the `camofox` plugin, the Node server, the
Camoufox download) and the exact command for each, with a button that runs it.

Switch: `hermes config set web.search_backend sluice` and the extract equivalent, run
through the CLI with the active profile's environment and a short timeout. Browser
selection is explained, not forced: camofox is a plugin plus a server, Browserbase is a
key, local Chromium is the default.

Browser identity is the user's own. Sluice surfaces Hermes' existing
`browser.use_real_profile` consent switch and the `local` browser mode in the pane, so a
signed-in user gets pages tailored to their own session. Sluice never enables either
switch itself, and never imports cookies, credentials or browser profiles on its own.

### Desktop pane

A backend table (name, tier, plan, key present, health, latency p50, quota, calls, estimated
spend, last error), a test query box that shows which backend answered, buttons for probe
all, install Playwright, start or stop Camofox, clear cache, open Settings for keys, and a
per-domain block report naming the vendor, the signals and the stages tried. A settings page
for tier order, per-backend plan, timeouts, cache TTL and keyless on/off. Every action goes
through `dashboard/plugin_api.py`; the pane holds no secrets and never reads app markup.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Keyless tiers rate-limit hard and 403 under load | Tier 3 only, circuit breaker, honest error text, never the sole path |
| Camofox server is a Node dependency | Optional, detected not required, extraction falls back to Chromium |
| SearXNG not running | Detected, documented, compose file offered, never assumed |
| `hermes config set` subprocess contends for the config lock | 10 s timeout, surface the failure, no blind retry, `--apply` opt-in |
| Extract provenance invisible after `_trim_results` | One-line header in `content`, full detail in pane and SQLite |
| Six-day budget | Pane and ladder are must-haves; if a day slips, cut paid adapters, never the pane |
| Catalog review rejects a core import | Adapters are self-contained; no core module imports |
| Keys cannot be written by the pane | Key status plus a link to Settings ▸ Plugins, never a fake input |

## Build order and submission

- Day 1 (6 Nov): router, store, SearXNG and ddgs adapters, unit tests.
- Day 2 (7 Nov): remaining adapters, cache, circuit breaker, routing tests.
- Day 3 (8 Nov): extract ladder, classifier, Playwright stage, per-URL contract tests.
- Day 4 (9 Nov): manager, CLI, camofox integration.
- Day 5 (10 Nov): desktop pane, `dashboard/plugin_api.py`, settings page. Feature freeze at
  the end of this day, or earlier.
- Submission (10-11 Nov): validate, README with disclosures, catalog PR opened, Typeform
  submitted, demo video recorded. Anything after that is a SHA-bump PR, not a feature.

## Demo plan (under 90 seconds)

`hermes sluice doctor` shows the twelve-backend inventory and which ones are free. A live
query comes back with `backend` and `tier` in the row metadata. A forced quota failure on
the top backend falls through to the next one in the same request. A challenged page shows
the classifier naming the vendor, then the ladder climbing to Chromium or Camofox and
returning content. The pane shows the health table, a test query and the block report.
Close on the tier list.

## Decisions taken by Abhi

- Architecture C: router and manager in one plugin.
- Name: Sluice.
- "Local" means self-hosted; "free" means keyless public tiers or free API keys; paid
  tiers are supported but last.
- v1 covers installing Camoufox, Playwright and optionally Browserbase.
- Ship the vendors Hermes users actually use, with DeepSeek and TinyFish kept in; free
  access is the differentiator against web-search-plus.
- The Desktop pane is a must-have: a manager that cannot be configured is not a manager.
- Routing is sequential; parallel fan-out is a later improvement.
- Camofox installs and starts only on an explicit user command, and a toggle without an
  install must say exactly what is missing.
- Dogfood it: switch Abhi's own `web.search_backend` to sluice once routing works.
- No CAPTCHA, paywall or login circumvention; the real profile is used only when the user
  turns it on.
- Stage 1 identifies itself honestly as `Sluice/1.0`.
- Feature freeze at the end of day 5 or earlier; submit once the PR is open.
- Per-domain routing rules are v1.1, not v1.

## Calls made in this document

- No custom CDP browser provider; integrate the official camofox plugin instead, because
  Camoufox cannot speak CDP.
- Self-contained adapters rather than importing Hermes' built-in provider classes.
- Own SQLite state store rather than reusing web-search-plus's cache format.
- No per-call backend pinning; settings plus the CLI test command.
- Free versus paid is a per-backend `plan` setting, not a separate credential.
- Outer Hermes cache off by default when Sluice owns the backend.
- No TLS impersonation in the HTTP stage; when the network fingerprint matters, only a real
  browser tells the truth about itself.
- `requires_hermes >=0.22.5`.

## Out of scope for v1 (candidates for v1.1)

- Parallel or racing fan-out and result fusion.
- Per-domain routing rules set by the user.
- TLS impersonation profiles.
- Answer synthesis and deep research mode.
- Web-dashboard (non-desktop) UI.
- Additional vendors beyond the twelve.

## Closed gaps

The four items this document left open are settled in `PLAN.md` and `references/endpoints.md`:

- Vendor payloads: `references/endpoints.md`, exact request and response shapes for all
  twelve vendors, with failure mapping and the two-key Brave trap.
- Cache, circuit and eviction numbers: `PLAN.md`, Closed gap 2. Search TTL 900 s general
  and 300 s news, extract 3600 s, negative cache 60 s, domain memory 24 h, circuit 15 min
  or 60 min on quota, LRU to a 200 MB cap.
- Pane layout, sizes, contribution areas and polling: `PLAN.md`, Closed gap 3.
- Demo fixture and the deterministic challenge beat: `PLAN.md`, Closed gap 4.

`PLAN.md` carries the fourteen build tasks with acceptance criteria and the day-by-day
order. Nothing is left to interpretation before build.
