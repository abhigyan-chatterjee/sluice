# Configuration

Sluice reads its configuration from one place: the plugin settings Hermes stores under
`plugins.entries.sluice.settings`, plus the credential environment variables those settings
name. This page lists every key, its type, its default and what it changes.
`tests/test_config.py` asserts that every non-secret key in `plugin.yaml` has a matching
entry in `sluice/config.py:DEFAULTS` with the same default, so the manifest and the runtime
cannot drift.

## Where settings live and how they resolve

`load_settings()` builds the effective settings in this order:

1. `DEFAULTS` from `sluice/config.py`.
2. Overrides. With a plugin context, one `ctx.get_config(key, default)` call per key.
   Without a context (the `sluice` console script, the dashboard API, tests), the saved
   values under `plugins.entries.sluice.settings`.
3. Credentials: the ten `*_API_KEY` names through `get_provider_env()`, or `os.environ`
   when Hermes is not importable.
4. `camofox_url` falls back to `CAMOFOX_URL`, then to `http://localhost:9377`.
5. `searxng_url` falls back to `SEARXNG_URL`. The result is validated once; a rejected
   address is dropped with its reason kept beside it as `searxng_url_error`.
6. `tier_order` is normalized and `keyless_enabled` is coerced to a boolean.

Two coercions are worth knowing because they make a malformed value harmless instead of
fatal:

- `tier_order` keeps only the four known tiers, in the order you wrote them, then appends
  whatever is missing. `paid,free` becomes `paid,free,local,keyless`. An unknown word is
  dropped, never an error.
- `keyless_enabled` accepts a boolean or the strings `1/true/yes/on` and `0/false/no/off`,
  and anything else falls back to the default `true`.

```sh
.venv/bin/python -c "from sluice.config import tier_order; print(tier_order({'tier_order': 'paid,free'}))"
# observed: ['paid', 'free', 'local', 'keyless']
```

## Routing settings

| Key | Type | Default | Effect |
|---|---|---|---|
| `tier_order` | str | `local,keyless,free,paid` | Tier order, cheapest first. Subset of `local`,`keyless`,`free`,`paid`; missing tiers are appended automatically. Read by `config.tier_order()` and used as the first sort key in `Router.candidates()`. |
| `keyless_enabled` | bool | `true` | Allow anonymous public tiers: `ddgs`, and the keyless paths of Exa, Parallel, Firecrawl, Tavily and Keenable. With it off, a backend with no key is `unavailable` and the provider reports `is_keyless_available() == False`. |
| `per_backend_budget_ms` | int | `2500` | Default per-attempt window for one backend in one search. A backend that declares `budget_ms` gets the wider of the two. The window is passed to the adapter as its request timeout, so it bounds the call instead of being measured afterwards. It also caps the `ddgs` child-process wall clock, and it is the floor reserved for the candidates behind a widened attempt. |
| `total_budget_ms` | int | `8000` | Wall-clock budget for one whole search, shared by every attempt. Checked before each candidate. While a candidate is still behind the current one, one `per_backend_budget_ms` window is reserved out of what remains, so one slow backend cannot consume the whole ladder; the last attempt is capped by whatever remains. When it runs out the error names the backend that was skipped. |

Routing is sequential. There is no parallel fan-out and no result fusion in 1.0.0.

The global default is a 2500 ms window per attempt and an 8000 ms budget per search. A
backend may declare a wider window of its own, and `ddgs` does: it runs a local multi-engine
scrape rather than an API call, and measurements on one development machine put a real call
at 1.9 s to 7.4 s, median 6.3 s. A declared window only widens that one attempt, and the
router keeps a default window in reserve for whatever is behind it: with the defaults `ddgs`
gets 5500 ms and the next candidate still gets 2500 ms. Raise `total_budget_ms` to give
`ddgs` its whole declared 8000 ms.

## Cache and circuit settings

| Key | Type | Default | Effect |
|---|---|---|---|
| `search_ttl_general` | int (s) | `900` | TTL for cached searches whose query class is `general` or `docs` (namespace `search:general`). |
| `search_ttl_news` | int (s) | `300` | TTL for queries classified `news` (namespace `search:news`). Classification is a word-list check in `sluice/router.py`; it never leaves the machine. |
| `extract_ttl` | int (s) | `3600` | TTL per cached URL and format (namespace `extract`). |
| `negative_ttl` | int (s) | `60` | TTL for a failed URL (namespace `extract:negative`), so a dead link is not re-fetched on every call. |
| `domain_memory_ttl` | int (s) | `86400` | How long `domain_memory` remembers the block class and the stage that worked for a domain. |
| `circuit_failures` | int | `3` | Consecutive failures that open a backend circuit. |
| `circuit_open_seconds` | int (s) | `900` | Open duration after `circuit_failures` consecutive failures. |
| `circuit_quota_seconds` | int (s) | `3600` | Open duration after one quota or credit error. A quota failure sets both `quota_exhausted_until` and `circuit_open_until`. |
| `cache_max_mb` | int (MB) | `200` | Size cap for `sluice.db`. `cache_put()` calls `maybe_evict()` after every write. |

A success resets the failure count and both breakers. A circuit is per backend id,
persisted, and survives a restart.

### Cache eviction and circuit policy

The store applies two policies that bound disk use and how long a dead backend stays out.

**Cache eviction.** Trigger at `cache_max_mb` (default 200 MB), delete expired cache and
domain-memory rows first, then delete least-recently-hit cache rows until the live payload
falls to `cache_low_water_mb` (default 150 MB), then checkpoint the WAL. `VACUUM`, the only
operation that returns pages to the filesystem, runs at most once per
`vacuum_interval_seconds` (default 604800) and only when the file is still over the cap.

**Circuit.** `circuit_open_seconds` (900) after `circuit_failures` (3) consecutive
failures, and `circuit_quota_seconds` (3600) after one quota or credit error, with no
exponential backoff. A success resets both.

## Local backend settings

| Key | Type | Default | Effect |
|---|---|---|---|
| `searxng_url` | str | `""` | Base URL of a running SearXNG whose `settings.yml` has `search.formats: [html, json]`. When empty, `SEARXNG_URL` is read; when both are empty the SearXNG backend is skipped entirely. |
| `camofox_url` | str | `http://localhost:9377` | Not in `plugin.yaml`. Address of the Camofox REST server used by the third ladder rung. Falls back to `CAMOFOX_URL`, then the default. |

SearXNG can be configured either way. The `searxng_url` plugin setting wins when both are
set; `SEARXNG_URL` is the fallback, the same shape as `CAMOFOX_URL`. The CLI hint and
`docker/searxng/docker-compose.yml` name the environment variable, and both paths reach the
same backend.

The value is validated once, in `sluice/config.py`, whichever source it came from:

* it must be an absolute `http` or `https` URL with a host, so `localhost:8080` is refused
  with the reason rather than handed to httpx later;
* trailing slashes are removed, so the adapter's `base + "/search"` never doubles one and
  `http://127.0.0.1:8080/` and `http://127.0.0.1:8080` are the same instance;
* a path prefix is kept (`https://example.com/searxng/` becomes `https://example.com/searxng`);
* a query or a fragment is refused, because neither belongs on a base address.

A refused address is dropped and its reason kept in the settings view as
`searxng_url_error`. The backend is then not routed to, `hermes sluice doctor` prints the
reason on its own line, and the SearXNG row's `missing` text carries it to the desktop pane.
Nothing reports "not set" for an address the user did set.

```sh
hermes config set plugins.entries.sluice.settings.searxng_url http://127.0.0.1:8080
hermes config get plugins.entries.sluice.settings.searxng_url
# observed: http://127.0.0.1:8080
hermes sluice doctor
# observed (excerpt from the twelve-row table):
# searxng      local    paid  n/a      ready
# ddgs         keyless  paid  keyless  ready
SEARXNG_URL=localhost:8080 .venv/bin/sluice doctor
# observed:
# SEARXNG_URL: SEARXNG_URL must be a full URL including the scheme, for example
# http://127.0.0.1:8888 (got 'localhost:8080'): a bare host and port cannot be routed.
```

## Per-backend plan settings

One key per keyed vendor: `plan_<vendor>`. Values are `free` or `paid`; anything else falls
back to the vendor's default. The plan only changes routing order inside the keyed tiers. It
never labels a keyless call as paid, and it never promotes a paid backend above a free one.

| Key | Default | Key | Default |
|---|---|---|---|
| `plan_exa` | `free` | `plan_brave` | `free` |
| `plan_parallel` | `paid` | `plan_perplexity` | `paid` |
| `plan_firecrawl` | `free` | `plan_deepseek` | `paid` |
| `plan_tavily` | `free` | `plan_tinyfish` | `paid` |
| `plan_keenable` | `paid` | `plan_xai` | `paid` |

Vendors with a published free allowance default to `free` (`FREE_PLAN_VENDORS` in
`sluice/config.py`). Everything else defaults to `paid`. `searxng` and `ddgs` have no plan
key because neither takes a credential.

The plan is only consulted when a key is present. A vendor with no key runs its keyless
path and is labelled `keyless` whatever its plan says.

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
| `BRAVE_API_KEY` | Brave Search | no; Brave needs a key. `BRAVE_SEARCH_API_KEY` is a different key and is not accepted |
| `PERPLEXITY_API_KEY` | Perplexity | no |
| `XAI_API_KEY` | xAI | no |
| `DEEPSEEK_API_KEY` | DeepSeek | no |
| `TINYFISH_API_KEY` | TinyFish | no |

`BROWSERBASE_API_KEY` is read for detection only (`sluice/manage.py`); it is not a Sluice
backend and does not appear in the manifest.

## Settings the code reads but the manifest does not declare

These are honoured when present under `plugins.entries.sluice.settings`, but they have no
field in Settings ▸ Plugins and `POST /settings` refuses them (only `EDITABLE_KEYS` and
`plan_*` are writable there), so a config-file edit is the route.

| Key | Default | Where it is read |
|---|---|---|
| `cache_low_water_mb` | `150` | `sluice/store.py`, through `StorePolicy.from_settings` |
| `vacuum_interval_seconds` | `604800` | `sluice/store.py` |
| `FIRECRAWL_API_URL` | `https://api.firecrawl.dev` | `sluice/backends/firecrawl.py` |
| `TAVILY_BASE_URL` | `https://api.tavily.com` | `sluice/backends/tavily.py` |
| `KEENABLE_API_URL` | `https://api.keenable.ai` | `sluice/backends/keenable.py` |
| `PERPLEXITY_BASE_URL` | `https://api.perplexity.ai` | `sluice/backends/perplexity.py` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | `sluice/backends/deepseek.py` |
| `XAI_BASE_URL` | `https://api.x.ai/v1` | `sluice/backends/xai.py` |

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `EXA_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `KEENABLE_API_KEY`, `BRAVE_API_KEY`, `PERPLEXITY_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `TINYFISH_API_KEY` | `sluice/config.py`, `sluice/provider.py` | Credentials when Hermes is not importable, and the fallback path in a Hermes session. |
| `CAMOFOX_URL` | `sluice/config.py` | Camofox server address when `camofox_url` is empty. |
| `BROWSERBASE_API_KEY` | `sluice/manage.py` | Presence only, for the doctor and pane "Browserbase" row. |
| `SLUICE_DATA_DIR` | `sluice/store.py` | Data directory override when Hermes' `plugin_data_dir()` is not importable (standalone installs and tests). Ignored when Hermes is present. |
| `HERMES_HOME` | `sluice/manage.py` | Hermes home used to find `cache/web`, `plugin-data/sluice/camofox` and installed plugins. |
| `PLAYWRIGHT_BROWSERS_PATH` | `sluice/manage.py` | Where detection looks for the Playwright Chromium build. |
| `SLUICE_CAMOFOX_DIR` | `sluice/manage.py` | Install directory for the npm fallback of the Camofox server. |
| `SLUICE_LIVE_TESTS` | `tests/test_backends.py` | Test switch only, never read by the plugin at runtime. `1` opts into the live vendor calls. |
| `SEARXNG_URL` | `sluice/config.py` | SearXNG base URL when the `searxng_url` setting is empty. An explicit setting always wins. Validated like the setting: a full http(s) URL, trailing slashes stripped, and a malformed value dropped with its reason. |

## Hermes-level keys Sluice depends on

These are Hermes keys, not plugin settings. Sluice never writes them; `hermes sluice setup`
prints the commands and runs them only with `--apply` (`sluice/manage.py`).

| Key | Value Sluice sets | Why |
|---|---|---|
| `web.search_backend` | `sluice` | Makes the native `web_search` tool use the Sluice provider. Until this is set, installing the plugin changes nothing. |
| `web.extract_backend` | `sluice` | Same for `web_extract`. |
| `web.cache_enabled` | `false` | Turns off Hermes' outer `cache/web` layer so exactly one tier-aware cache and one TTL govern. `hermes sluice cache clear` clears both layers anyway. |

Two wrapper keys shape what the model sees but belong to Hermes, not Sluice:
`web.extract_char_limit` (applied by the wrapper to the returned extract text) and
`web.cache_ttl_minutes` (the outer cache, 20 minutes by default, disabled by the route
above).

## Changing settings

Three routes, all writing the same `plugins.entries.sluice.settings` map:

1. Settings ▸ Plugins in Hermes, which renders `plugin.yaml`'s `config_schema`.
2. The Desktop pane's settings page, which posts to `/api/plugins/sluice/settings`. The route
   writes each key through `PluginContext.set_config()`, the documented plugin API, which is
   the same writer Settings ▸ Plugins uses, so the CLI, the pane and the plugin cannot
   disagree on where a setting lives. It refuses unknown keys, every secret, and a value
   whose type does not match the manifest:
   `{"ok": false, "error": "'cache_max_mb' should be int (got str)"}`.
3. `hermes config set plugins.entries.sluice.settings.<key> <value>`.

`POST /settings` accepts only `EDITABLE_KEYS` plus `plan_<vendor>` (`sluice/api.py`). The
observed validation results:

```python
from sluice.api import save_settings_payload
save_settings_payload({"nope": 1}, writer=lambda v: v)
# observed: {'ok': False, 'error': 'not editable through Sluice: nope'}
save_settings_payload({"EXA_API_KEY": "sk-x"}, writer=lambda v: v)
# observed: {'ok': False, 'error': 'not editable through Sluice: EXA_API_KEY'}
save_settings_payload({"cache_max_mb": 300, "plan_exa": "paid"}, writer=lambda v: sorted(v))
# observed: {'ok': True, 'written': ['cache_max_mb', 'plan_exa']}
save_settings_payload({"cache_max_mb": "300"}, writer=lambda v: sorted(v))
# observed: {'ok': False, 'error': "'cache_max_mb' should be int (got str)"}
```

The pane route only writes inside a Hermes process that has loaded the plugin, because that
is where the context comes from. Without one it says so instead of failing later:
`{"ok": false, "error": "no Hermes plugin context in this process: enable Sluice with
'hermes plugins' and restart Hermes, then save from Settings ▸ Plugins"}`.

A `hermes sluice` subcommand reads the settings fresh on every invocation, so a change is
visible there at once. Inside a long-lived Hermes process the provider builds its settings
and router once, so a change applies after that provider is rebuilt, which in practice means
a Hermes or gateway restart.

## Directory-plugin loading

Hermes imports this repository as the directory plugin `hermes_plugins.sluice`. That
namespace exists only in the parent process, so the `ddgs` query runs in a `subprocess`
`-c` script that takes one JSON request on stdin and prints one JSON object. A
`multiprocessing` spawn would try to import `hermes_plugins` in the child and fail. The
script puts the repository root first on `PYTHONPATH`, so it imports `search_row` from the
same copy of Sluice that started it.

`SEARXNG_URL` and the `searxng_url` setting are both read; the plugin setting wins when both
are set (`sluice/config.py`).

Regression coverage:
`tests/test_backends.py::test_ddgs_search_works_under_the_hermes_plugins_loader`,
`tests/test_backends.py::test_ddgs_router_search_works_under_the_hermes_plugins_loader`,
`tests/test_backends.py::test_ddgs_failure_is_not_silent_and_surfaces_in_store_and_desktop_status`,
`tests/test_config.py::test_searxng_url_env_configures_searxng_when_the_setting_is_unset`,
and `tests/test_public_surface.py` (no private Hermes import; settings write through
`ctx.set_config`).
