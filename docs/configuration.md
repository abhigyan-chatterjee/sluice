# Configuration

Everything Sluice reads at runtime comes from one place: the plugin settings Hermes stores
under `plugins.entries.sluice.settings`, plus the credential environment variables those
settings name. This page lists every key, its type, its default and what it changes, in the
order the code resolves it. Line references point at the implementation, not at the design
documents.

- Schema: `plugin.yaml` (`config_schema`, lines 17-58).
- Runtime defaults: `sluice/config.py` (`DEFAULTS`, lines 45-64).
- Resolution: `sluice/config.py:load_settings` (lines 120-157).
- Cache and circuit policy: `sluice/store.py` (`StorePolicy`, lines 59-78).

`tests/test_config.py` asserts that every non-secret key in `plugin.yaml` has a matching
entry in `DEFAULTS` with the same default, so the manifest and the runtime cannot drift.

## Where settings live and how they resolve

`load_settings()` builds the effective settings in this order:

1. `DEFAULTS` from `sluice/config.py:45`.
2. Overrides. With a plugin context, one `ctx.get_config(key, default)` call per key
   (`sluice/config.py:131-140`). Without a context (the `sluice` console script, the
   dashboard API, tests), the saved values under
   `plugins.entries.sluice.settings` (`sluice/config.py:87-105`).
3. Credentials: the ten `*_API_KEY` names through `get_provider_env()`, or `os.environ`
   when Hermes is not importable (`sluice/config.py:108-117`).
4. `camofox_url` falls back to `CAMOFOX_URL`, then to `http://localhost:9377`.
5. `tier_order` is normalized and `keyless_enabled` is coerced to a boolean.

Two coercions are worth knowing because they make a malformed value harmless instead of
fatal:

- `tier_order` keeps only the four known tiers, in the order you wrote them, then appends
  whatever is missing (`sluice/config.py:160-166`). `paid,free` becomes
  `paid,free,local,keyless`. An unknown word is dropped, never an error.
- `keyless_enabled` accepts a boolean or the strings `1/true/yes/on` and `0/false/no/off`,
  and anything else falls back to the default `true` (`sluice/config.py:169-178`).

```sh
.venv/bin/python -c "from sluice.config import tier_order; print(tier_order({'tier_order': 'paid,free'}))"
# observed: ['paid', 'free', 'local', 'keyless']
```

## Routing settings

| Key | Type | Default | Effect |
|---|---|---|---|
| `tier_order` | str | `local,keyless,free,paid` | Tier order, cheapest first. Subset of `local`,`keyless`,`free`,`paid`; missing tiers are appended automatically. Read by `config.tier_order()` (`sluice/config.py:181`) and used as the first sort key in `Router.candidates()` (`sluice/router.py:226-238`). |
| `keyless_enabled` | bool | `true` | Allow anonymous public tiers: `ddgs`, and the keyless paths of Exa, Parallel, Firecrawl, Tavily and Keenable. With it off, a backend with no key is `unavailable` (`sluice/backends/__init__.py:125-132`) and the provider reports `is_keyless_available() == False`. |
| `per_backend_budget_ms` | int | `2500` | Wall-clock budget for one backend in one search. Passed to the adapter as its request timeout (`sluice/router.py:332`, `sluice/router.py:396-414`), so it bounds the call instead of being measured afterwards. It also shortens the ddgs child-process wall clock (`sluice/backends/ddgs.py:143-147`). |
| `total_budget_ms` | int | `8000` | Wall-clock budget for one whole search. Checked before each candidate; the last attempt is capped by whatever remains (`sluice/router.py:328-332`). When it runs out the error names the backend that was skipped. |

Routing is sequential. There is no parallel fan-out and no result fusion in 1.0.0.

## Cache and circuit settings

| Key | Type | Default | Effect |
|---|---|---|---|
| `search_ttl_general` | int (s) | `900` | TTL for cached searches whose query class is `general` or `docs` (namespace `search:general`). |
| `search_ttl_news` | int (s) | `300` | TTL for queries classified `news` (namespace `search:news`). Classification is a word-list check in `sluice/router.py:119-126`; it never leaves the machine. |
| `extract_ttl` | int (s) | `3600` | TTL per cached URL and format (namespace `extract`). |
| `negative_ttl` | int (s) | `60` | TTL for a failed URL (namespace `extract:negative`), so a dead link is not re-fetched on every call (`sluice/router.py:490-496`). |
| `domain_memory_ttl` | int (s) | `86400` | How long `domain_memory` remembers the block class and the stage that worked for a domain (`sluice/store.py:721-741`). |
| `circuit_failures` | int | `3` | Consecutive failures that open a backend circuit (`sluice/store.py:622`). |
| `circuit_open_seconds` | int (s) | `900` | Open duration after `circuit_failures` consecutive failures. |
| `circuit_quota_seconds` | int (s) | `3600` | Open duration after one quota or credit error. A quota failure sets both `quota_exhausted_until` and `circuit_open_until` (`sluice/store.py:619-623`). |
| `cache_max_mb` | int (MB) | `200` | Size cap for `sluice.db`. `cache_put()` calls `maybe_evict()` after every write (`sluice/store.py:304-325`). |

A success resets the failure count and both breakers (`sluice/store.py:590-595`). A circuit
is per backend id, persisted, and survives a restart.

### The two reconciled thresholds

`PLAN.md` and `DESIGN.md` disagree on two numbers. These are the two pairs in `PLAN.md`'s
"Closed gaps" item 2 (`PLAN.md:18-23`). Both are resolved in the code, and both resolutions
are recorded in `REQUIREMENTS.md` §2.2.

**1. Cache eviction: 200 MB trigger, 150 MB low-water mark.** `PLAN.md:21-23` says "if the
DB exceeds 150 MB, delete expired rows first, then least-recently-hit rows down to 200 MB".
Deleting rows cannot shrink a database up to a larger number, so the two figures cannot both
be literal. `DESIGN.md:205` and `DESIGN.md:329` both fix the cap at 200 MB. The
implementation therefore triggers at `cache_max_mb` (default 200 MB), deletes expired cache
and domain-memory rows first, then deletes least-recently-hit cache rows until the live
payload falls to `cache_low_water_mb` (default 150 MB), then checkpoints the WAL
(`sluice/store.py:403-466`). `VACUUM`, the only operation that returns pages to the
filesystem, runs at most once per `vacuum_interval_seconds` (default 604800) and only when
the file is still over the cap (`sluice/store.py:454-462`).

**2. Circuit: 15 minutes after 3 failures, 60 minutes after 1 quota error.**
`DESIGN.md:204-205` says "three consecutive failures or one quota error opens it for 15
minutes, with exponential backoff capped at an hour". `PLAN.md:20-21` gives a quota error 60
minutes. The implementation follows `PLAN.md`, the document that closed the gap:
`circuit_open_seconds` (900) after `circuit_failures` (3) consecutive failures, and
`circuit_quota_seconds` (3600) after one quota or credit error, with no exponential backoff
(`sluice/store.py:599-631`). `REQUIREMENTS.md` §2.2 notes that switching to the design's
uniform 15 minutes is a two-constant change.

## Local backend settings

| Key | Type | Default | Effect |
|---|---|---|---|
| `searxng_url` | str | `""` | Base URL of a running SearXNG whose `settings.yml` has `search.formats: [html, json]`. Empty means the SearXNG backend is skipped entirely (`sluice/backends/__init__.py:115-120`). |
| `camofox_url` | str | `http://localhost:9377` | Not in `plugin.yaml`. Address of the Camofox REST server used by the third ladder rung. Falls back to `CAMOFOX_URL`, then the default (`sluice/config.py:61-63`, `sluice/config.py:152-153`). |

Pointing Sluice at a SearXNG instance is a plugin-setting write, not an environment
variable. `SEARXNG_URL` is printed by several messages but is not read anywhere in the
code in 1.0.0 (see "Known gaps" below).

```sh
hermes config set plugins.entries.sluice.settings.searxng_url http://127.0.0.1:8080
hermes config get plugins.entries.sluice.settings.searxng_url
# observed: http://127.0.0.1:8080
hermes sluice doctor
# observed (excerpt from the twelve-row table):
# searxng      local    paid  n/a      ready
# ddgs         keyless  paid  keyless  ready
```

## Per-backend plan settings

One key per keyed vendor: `plan_<vendor>`. Values are `free` or `paid`; anything else falls
back to the vendor's default (`sluice/config.py:185-187`). The plan only changes routing
order inside the keyed tiers. It never labels a keyless call as paid, and it never promotes
a paid backend above a free one (`sluice/backends/__init__.py:134-152`).

| Key | Default | Key | Default |
|---|---|---|---|
| `plan_exa` | `free` | `plan_brave` | `free` |
| `plan_parallel` | `paid` | `plan_perplexity` | `paid` |
| `plan_firecrawl` | `free` | `plan_deepseek` | `paid` |
| `plan_tavily` | `free` | `plan_tinyfish` | `paid` |
| `plan_keenable` | `paid` | `plan_xai` | `paid` |

Vendors with a published free allowance default to `free` (`sluice/config.py:23`,
`FREE_PLAN_VENDORS`). Everything else defaults to `paid`. `searxng` and `ddgs` have no plan
key because neither takes a credential.

The plan is only consulted when a key is present. A vendor with no key runs its keyless
path and is labelled `keyless` whatever its plan says (`sluice/backends/__init__.py:134-152`).

## Credential settings

Secrets are declared in the manifest as `type: secret` with an `env:` name. Hermes writes
them through its credential route (Settings ▸ Plugins) into the profile `.env`; Sluice reads
presence and value through `get_provider_env()` and never writes a key. Outside Hermes the
same names are read from `os.environ`.

| Setting / env var | Vendor | Keyless path without it |
|---|---|---|
| `EXA_API_KEY` | Exa | yes, the Exa MCP endpoint |
| `PARALLEL_API_KEY` | Parallel | yes, the Parallel MCP endpoint |
| `FIRECRAWL_API_KEY` | Firecrawl | yes, public endpoint |
| `TAVILY_API_KEY` | Tavily | yes, public endpoint |
| `KEENABLE_API_KEY` | Keenable | yes, public endpoint |
| `BRAVE_API_KEY` | Brave Search | no; Brave needs a key. `BRAVE_SEARCH_API_KEY` is a different key and is not accepted (`sluice/backends/brave.py:44-72`) |
| `PERPLEXITY_API_KEY` | Perplexity | no |
| `XAI_API_KEY` | xAI | no |
| `DEEPSEEK_API_KEY` | DeepSeek | no |
| `TINYFISH_API_KEY` | TinyFish | no |

`BROWSERBASE_API_KEY` is read for detection only (`sluice/manage.py:205`); it is not a
Sluice backend and does not appear in the manifest.

## Settings the code reads but the manifest does not declare

These are honoured when present under `plugins.entries.sluice.settings`, but they have no
field in Settings ▸ Plugins and `POST /settings` refuses them (only `EDITABLE_KEYS` and
`plan_*` are writable there), so a config-file edit is the route.

| Key | Default | Read at |
|---|---|---|
| `cache_low_water_mb` | `150` | `sluice/store.py:75`, via `StorePolicy.from_settings` |
| `vacuum_interval_seconds` | `604800` | `sluice/store.py:77` |
| `FIRECRAWL_API_URL` | `https://api.firecrawl.dev` | `sluice/backends/firecrawl.py:40-51` |
| `TAVILY_BASE_URL` | `https://api.tavily.com` | `sluice/backends/tavily.py:39-51` |
| `KEENABLE_API_URL` | `https://api.keenable.ai` | `sluice/backends/keenable.py:36-48` |
| `PERPLEXITY_BASE_URL` | `https://api.perplexity.ai` | `sluice/backends/perplexity.py:42-70` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | `sluice/backends/deepseek.py:46-59` |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | `sluice/backends/xai.py:42-67` |

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `EXA_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `KEENABLE_API_KEY`, `BRAVE_API_KEY`, `PERPLEXITY_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `TINYFISH_API_KEY` | `sluice/config.py:108-117`, `sluice/provider.py:81-96` | Credentials when Hermes is not importable, and the fallback path in a Hermes session. |
| `CAMOFOX_URL` | `sluice/config.py:152-153` | Camofox server address when `camofox_url` is empty. |
| `BROWSERBASE_API_KEY` | `sluice/manage.py:205` | Presence only, for the doctor and pane "Browserbase" row. |
| `SLUICE_DATA_DIR` | `sluice/store.py:842-845` | Data directory override when Hermes' `plugin_data_dir()` is not importable (standalone installs and tests). Ignored when Hermes is present. |
| `HERMES_HOME` | `sluice/manage.py:92-95` | Hermes home used to find `cache/web`, `plugin-data/sluice/camofox` and installed plugins. |
| `PLAYWRIGHT_BROWSERS_PATH` | `sluice/manage.py:121-123` | Where detection looks for the Playwright Chromium build. |
| `SLUICE_CAMOFOX_DIR` | `sluice/manage.py:381-385` | Install directory for the npm fallback of the Camofox server. |
| `SLUICE_LIVE_TESTS` | `tests/test_backends.py:864-878` | Test switch only, never read by the plugin at runtime. `1` opts into the live vendor calls. |
| `SEARXNG_URL` | nothing | Printed in messages, not read. Use the `searxng_url` setting. |

## Hermes-level keys Sluice depends on

These are Hermes keys, not plugin settings. Sluice never writes them; `hermes sluice setup`
prints the commands and runs them only with `--apply` (`sluice/manage.py:37-41`).

| Key | Value Sluice sets | Why |
|---|---|---|
| `web.search_backend` | `sluice` | Makes the native `web_search` tool use the Sluice provider. Until this is set, installing the plugin changes nothing. |
| `web.extract_backend` | `sluice` | Same for `web_extract`. |
| `web.cache_enabled` | `false` | Turns off Hermes' outer `cache/web` layer so exactly one tier-aware cache and one TTL govern. `hermes sluice cache clear` clears both layers anyway (`sluice/cli.py:297-311`). |

Two wrapper keys shape what the model sees but belong to Hermes, not Sluice:
`web.extract_char_limit` (applied by the wrapper to the returned extract text) and
`web.cache_ttl_minutes` (the outer cache, 20 minutes by default, disabled by the route
above).

## Changing settings

Three routes, all writing the same `plugins.entries.sluice.settings` map:

1. Settings ▸ Plugins in Hermes, which renders `plugin.yaml`'s `config_schema`.
2. The Desktop pane's settings page, which posts to `/api/plugins/sluice/settings` and
   writes through `hermes_cli.plugins_settings.save_plugin_settings`
   (`sluice/api.py:403-435`). The route refuses unknown keys and refuses every secret:
   `{"ok": false, "error": "not editable through Sluice: EXA_API_KEY"}`.
3. `hermes config set plugins.entries.sluice.settings.<key> <value>`.

`POST /settings` accepts only `EDITABLE_KEYS` plus `plan_<vendor>`
(`sluice/api.py:39-54`). The observed validation results:

```python
from sluice.api import save_settings_payload
save_settings_payload({"nope": 1}, writer=lambda v: v)
# observed: {'ok': False, 'error': 'not editable through Sluice: nope'}
save_settings_payload({"EXA_API_KEY": "sk-x"}, writer=lambda v: v)
# observed: {'ok': False, 'error': 'not editable through Sluice: EXA_API_KEY'}
save_settings_payload({"cache_max_mb": 300, "plan_exa": "paid"}, writer=lambda v: sorted(v))
# observed: {'ok': True, 'written': ['cache_max_mb', 'plan_exa']}
```

A `hermes sluice` subcommand reads the settings fresh on every invocation, so a change is
visible there at once. Inside a long-lived Hermes process the provider builds its settings
and router once (`sluice/provider.py:127-133`, `sluice/provider.py:170-197`), so a change
applies after that provider is rebuilt, which in practice means a Hermes or gateway restart.

## Known gaps in 1.0.0

- `SEARXNG_URL` is not read. The SearXNG backend reads the `searxng_url` setting, and the
  manifest declares only the setting. Messages in `sluice/cli.py:71-75`,
  `sluice/backends/__init__.py:115-120` and `docker/searxng/docker-compose.yml:7` still
  name the environment variable.
- `ddgs` cannot spawn its child process when Sluice is loaded by Hermes as a directory
  plugin: the child fails with `ModuleNotFoundError: No module named 'hermes_plugins'`
  because the loader's package name is synthetic. The router records it as a failed backend
  and moves to the next candidate. The standalone CLI and a checkout-local Python are
  unaffected.
