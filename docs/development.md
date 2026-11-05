# Development

This page covers the local loop: a clean environment, the test suite, the two local HTTP
targets, the plugin validator, and a release. Every command here was run on 5 November 2026
while writing these docs; the outputs shown are the ones observed, not idealized ones. The
consolidated log is at the end.

## Requirements

- Python 3.11 or newer (`pyproject.toml:10`; CI runs 3.11).
- `uv` for the environment below (`pip` works too, but the commands here are `uv`).
- Git, because two tests read tracked files (`git ls-files`, `git check-ignore`).
- Optional: Playwright Chromium and a container runtime or Node.js for the browser rungs of
  the Fetch Ladder.
- Optional: SearXNG through any Compose v2 provider, for the local backend.

## Clean environment

From a clean checkout:

```sh
uv venv
uv pip install -e ".[dev]"
.venv/bin/pytest -q
```

Observed in a fresh copy of the committed tree (`git archive`, so no `.git` directory):

```
Using CPython 3.14.7
Creating virtual environment at: .venv
Resolved 43 packages in 625ms
Installed 43 packages in 224ms
...
377 passed, 12 skipped in 13.67s
```

Run the same three commands inside a git checkout and the count is `379 passed, 10 skipped`:
`tests/test_surfaces.py` has two tests that skip when there is no `.git`, because they assert
that the dashboard entry and API files are tracked and not gitignored. The ten skips in a
checkout are the opt-in live vendor tests (below).

The `dev` extra adds `pytest` and `fastapi`. `fastapi` is a Hermes dependency at runtime, not
a package dependency, so installing it is what lets the route tests in
`tests/test_plugin_api.py` run instead of skipping (`pyproject.toml:20-24`).

## The test suite

`tests/conftest.py` installs an autouse fixture that points `HERMES_HOME` and
`SLUICE_DATA_DIR` at a per-test temporary directory and patches
`sluice.store.default_data_dir`, so no test can read or write the developer's real Hermes
state. The default suite touches no network.

| File | What it pins |
|---|---|
| `tests/test_scaffold.py` | package imports without Hermes, `register(ctx)` wires one provider plus the CLI command and the skill, every planned subcommand parses, the manifest declares ten secrets and the four python dependencies |
| `tests/test_config.py` | every manifest setting has the same default in `sluice/config.py`; secret names and plan keys match |
| `tests/test_store.py` | circuit open/close, quota window, TTL expiry, LRU eviction to the low-water mark, domain memory TTL, job lifecycle |
| `tests/test_router.py` | tier order, free before paid, fallback on failure, total-budget error, cache hit and stale-backend invalidation, per-URL extract isolation |
| `tests/test_ladder.py` | all nine block classes against `tests/fixtures/blocks/`, stop rules, robots parsing, `Retry-After`, provenance line |
| `tests/test_backends.py` | every adapter against recorded payloads, quota mapping, MCP parsing, the live tests behind the flag |
| `tests/test_cli.py` | every command runs unattended and exits non-zero on failure |
| `tests/test_manage.py` | detection with stubbed binaries, install plans, config route, job persistence |
| `tests/test_plugin_api.py` | the six planned routes plus settings and setup, mounted on a FastAPI test client |
| `tests/test_surfaces.py` | manifest entry files exist and are tracked, desktop pane imports only the SDK and React, README discloses every vendor host and side effect, SearXNG JSON settings, fixture server routes |

### Live vendor tests

The default suite skips them. To run one real search per vendor that has usable
credentials:

```sh
SLUICE_LIVE_TESTS=1 .venv/bin/pytest -q -k live
```

Observed on 5 November 2026 with no API keys set (keyless tiers enabled, so five vendors were
eligible). The run predates the F1-F4 regression tests, which is why it deselects 361 tests
where the current suite deselects 379:

```
1 failed, 4 passed, 5 skipped, 361 deselected in 6.09s
FAILED tests/test_backends.py::test_live_search_against_the_real_vendor[firecrawl]
E   AssertionError: Firecrawl quota is exhausted; skipped for up to an hour
```

Four keyless vendors answered; Firecrawl's public endpoint reported its keyless quota
exhausted; the five paid vendors skipped for want of credentials. The live run is not part
of CI for exactly this reason: it tests the vendors' quotas as much as the code.

## The fixture server

`demo/fixture_server.py` serves deterministic responses for the demo and for the ladder
tests. Run it on a loopback port:

```sh
.venv/bin/python demo/fixture_server.py --port 8765
```

```
sluice fixtures on http://127.0.0.1:8765
  http://127.0.0.1:8765/ok
  http://127.0.0.1:8765/js
  http://127.0.0.1:8765/cf
  http://127.0.0.1:8765/imperva
  http://127.0.0.1:8765/ratelimit
  http://127.0.0.1:8765/ratelimit-once
  http://127.0.0.1:8765/private
  http://127.0.0.1:8765/private/ok
  http://127.0.0.1:8765/robots.txt
```

| Path | Response | Class it exercises |
|---|---|---|
| `/ok` | 200, plain HTML with prose | `ok` at stage `http` |
| `/js` | 200, shell filled in by a script | `js_required`, climbs to `chromium` |
| `/cf` | 403 with `cf-mitigated: challenge` | `challenge` |
| `/imperva` | 200 with an 83-byte body and `x-iinfo` | `challenge` |
| `/ratelimit` | 429 with `Retry-After: 2` | `rate_limited` |
| `/ratelimit-once` | 429 once, then 200 | the single `Retry-After` retry |
| `/private` | robots.txt disallows it | `policy_blocked`, no request made |
| `/private/ok` | allowed by a longer `Allow` rule | `ok` |

`curl` shows the headers the classifier keys on:

```sh
curl -i http://127.0.0.1:8765/cf
```

```
HTTP/1.1 403 Forbidden
Server: sluice-fixture/1.0 Python/3.14.7
Date: Thu, 05 Nov 2026 14:00:21 GMT
Content-Type: text/html; charset=utf-8
Content-Length: 218
cf-mitigated: challenge
```

## Local SearXNG

`docker/searxng/docker-compose.yml` runs a SearXNG bound to `127.0.0.1:8080` with
`search.formats: [html, json]` mounted from `docker/searxng/settings.yml`. JSON output is
off in the stock configuration, which is the only reason this file exists.

```sh
podman-compose -f docker/searxng/docker-compose.yml up -d
```

Observed: the image was pulled and `sluice-searxng` started. Any Compose v2 provider works;
this machine has no Docker Compose plugin, so the run above used `podman-compose` (installed
in a throwaway venv). Then check the JSON API and point Sluice at it:

```sh
curl -s "http://127.0.0.1:8080/search?q=sluice&format=json" | head -c 300
```

```
{"query": "sluice", "results": [], "answers": [], "corrections": [], "infoboxes": [], "suggestions": [], "unresponsive_engines": [["brave", "timeout"], ["duckduckgo", "timeout"], ["google cse", "timeout"], ["wikidata", "timeout"], ["wikipedia", "timeout"]]}
```

The JSON envelope is the point: the API is reachable and well-formed. The empty result list
comes from the container's upstream engines timing out (`unresponsive_engines` names all
five), not from the settings file.

Both controls work: the `searxng_url` setting wins, and `SEARXNG_URL` is the fallback when
the setting is empty (`sluice/config.py:154-159`):

```sh
SEARXNG_URL=http://127.0.0.1:8080 .venv/bin/sluice doctor
# searxng      local    paid  n/a      ready
hermes config set plugins.entries.sluice.settings.searxng_url http://127.0.0.1:8080
hermes sluice test --backend searxng --limit 2 "sqlite wal"
```

```
searxng (local, local) answered in 3097 ms with 0 result(s)
```

Stop and remove it when done:

```sh
podman-compose -f docker/searxng/docker-compose.yml down
```

## Validating the plugin

Both commands run against a directory, and both are read-only:

```sh
hermes plugins doctor /home/abhi/sluice --ci
hermes plugins validate /home/abhi/sluice
```

`doctor --ci` output (exit 0):

```
Plugin Doctor: /home/abhi/sluice
  manifest: sluice 1.0.0 (backend)
  WARN: declared python_dependencies not installed: trafilatura>=1.8,<3,
playwright>=1.45,<2 — install and enable the plugin with hermes plugins. For an
enabled plugin, run hermes pm repair, then restart Hermes.
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
```

The warning is about the interpreter running `doctor`, not about the repository: this
checkout's dependencies live in `.venv`, while `hermes` runs on its own interpreter. Install
the plugin's dependencies there (or run `doctor` with the venv's interpreter) to silence it.

`validate` output (exit 0):

```
✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — spec '>=0.22.5' parses
✓ config schema — shape valid
✓ requires_env — all entries UPPER_SNAKE
✓ oauth — not declared
✓ loadable — entry: __init__.py, desktop/plugin.js
✓ python dependencies — 4 requirements from pyproject
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no tools to check
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
✓ desktop surface — stays inside the plugin SDK surface
⚠ private core imports (catalog rule 9: public surfaces only; these can break on
any release): sluice/api.py:420
hermes_cli.plugins_settings.save_plugin_settings. Use ctx.* / documented plugin
APIs, or open an issue for the seam you need.
◆ Isolation — runs in the plugin host (plugins.isolation: host)
Validation passed.
```

The single warning is the settings writer: Sluice uses Hermes' own
`save_plugin_settings` rather than adding a second writer for `plugins.entries.sluice`. It is
a warning, not an error, and `validate` exits 0.

## Hermetic CLI runs

The CLI and the provider read Hermes settings when Hermes is importable, and the standalone
console script reads `os.environ` when it is not. To exercise either without touching your
real Hermes profile:

```sh
DEV=$(mktemp -d)
export HERMES_HOME="$DEV/hermes-home"
export SLUICE_DATA_DIR="$DEV/data"
unset EXA_API_KEY PARALLEL_API_KEY FIRECRAWL_API_KEY TAVILY_API_KEY KEENABLE_API_KEY \
      BRAVE_API_KEY PERPLEXITY_API_KEY XAI_API_KEY DEEPSEEK_API_KEY TINYFISH_API_KEY \
      BROWSERBASE_API_KEY CAMOFOX_URL SEARXNG_URL
.venv/bin/sluice doctor
```

Observed (first lines; the full table lists all twelve backends):

```
Sluice 1.0.0 — 12 backends, free first
tier order: local,keyless,free,paid   keyless: on

backend      tier     plan  key      status
-------------------------------------------
searxng      local    paid  n/a      missing: SEARXNG_URL is not set
```

`sluice status` in the same environment creates the store on first use and prints an empty
cache. The only file created under `$DEV` was `$DEV/data/sluice.db`.

To test the installed-plugin path instead, install from a scratch Hermes home:

```sh
hermes plugins install abhigyan-chatterjee/sluice --no-deps --enable
hermes plugins enable sluice
hermes sluice doctor
```

`--no-deps` keeps the install unattended and leaves the plugin disabled; the explicit
`enable` turns it on without a dependency prompt. Observed: the clone succeeded, `enable`
reported `✓ Plugin sluice enabled.`, and `hermes sluice doctor` printed the same twelve-row
table as the standalone run.

## Cutting a release

1. Bump the version in four places, which the tests pin to the same value:
   `plugin.yaml:2`, `pyproject.toml:7`, `__init__.py:17` and the front matter of
   `skills/sluice/SKILL.md:5`.
2. Update the three assertions that hardcode it: `tests/test_scaffold.py:300`,
   `tests/test_scaffold.py:325` and `tests/test_surfaces.py:188`.
3. Run `.venv/bin/pytest -q` and the two validator commands above. All three must pass on
   the commit you are about to tag, not on the working tree.
4. Commit, tag (`v<version>`), push the branch and the tag.
5. Watch CI for the pushed commit. `.github/workflows/ci.yml` runs on every push and pull
   request: Python 3.11, `pip install -e ".[dev]"`, `pytest`.
6. If the release changes what the README discloses (vendors, hosts, side effects), the
   surface tests will fail first. That is the intended tripwire, not a nuisance.

## Documentation verification log

Every command used in `README.md` and this page, and what it returned on 5 November 2026.
"Standalone" means the `sluice` console script from `.venv` with `HERMES_HOME` and
`SLUICE_DATA_DIR` pointed at a temporary directory, no vendor keys.

| Command | Observed |
|---|---|
| `uv venv` then `uv pip install -e ".[dev]"` | 43 packages installed into `.venv` |
| `sluice --version` | `sluice 1.0.0` |
| `sluice doctor` | twelve-row table, keyless row `ready`, seven keyed rows `missing`, browser engines block |
| `sluice status` | twelve rows `idle`, `cache: 0 live / 0 rows`, `spend: $0.0000` |
| `sluice setup` | three `hermes config set` lines, `dry run: re-run with --apply` |
| `sluice use search` | `hermes config set web.search_backend sluice`, dry-run note |
| `sluice cache clear` | `deleted 0 row(s)`; outer `cache/web` cleared |
| `sluice install browserbase` | "there is nothing to install", next command `hermes config set browser.cloud_provider browserbase` |
| `sluice install playwright` | job ran `python -m playwright install chromium`, `done. next: hermes sluice install camofox` (43 s) |
| `sluice test --backend ddgs --limit 3 "duckdb vs sqlite benchmark"` | `ddgs (keyless, keyless) answered in 3333 ms with 3 result(s)` |
| `hermes plugins install abhigyan-chatterjee/sluice --no-deps --enable` | clone succeeded, `Plugin installed but not enabled` (the README shows the same command without the flags, which adds the dependency consent prompt) |
| `hermes plugins enable sluice` | `✓ Plugin sluice enabled.` |
| `hermes plugins doctor sluice --ci` | `OK: runtime discovery, manifest parsing, import, and registration passed`, exit 0 |
| `hermes sluice doctor` | same twelve-row table, from the installed plugin |
| `hermes sluice setup` | three config route lines, dry run |
| `hermes sluice setup --apply` | three `[ok]` lines, `✓ Set web.search_backend = sluice`, `web.extract_backend = sluice`, `web.cache_enabled = False`; `done: Hermes now routes ... outer cache off` |
| `hermes sluice status` | twelve rows, `cache: 0 live / 0 rows`, spend line |
| `hermes sluice cache clear` | `deleted 0 row(s)`, `hermes cache/web: nothing at <scratch>/cache/web` |
| `hermes sluice test --backend exa --limit 2 "sqlite wal checkpoint"` | `exa (keyless, keyless) answered in 886 ms with 2 result(s)` |
| `.venv/bin/pytest tests/test_backends.py -q -k ddgs` | `13 passed`; the loader test imports `ddgs.py` as `hermes_plugins.sluice.sluice.backends.ddgs` and completes one query in a real child process |
| `hermes sluice install camofox` | not exercised: it starts a container and the first start downloads the Camoufox bundle. The plan behind it is covered by `tests/test_manage.py` |
| `hermes plugins doctor /home/abhi/sluice --ci` | `OK: runtime discovery, manifest parsing, import, and registration passed`, exit 0 |
| `hermes plugins validate /home/abhi/sluice` | `Validation passed.`, exit 0, one warning on the private settings import |
| `hermes config set plugins.entries.sluice.settings.searxng_url ...` | `✓ Set ...`, `doctor` then shows `searxng local paid n/a ready` |
| `podman-compose -f docker/searxng/docker-compose.yml up -d` | image pulled, `sluice-searxng` started |
| `curl -s "http://127.0.0.1:8080/search?q=sluice&format=json"` | JSON envelope, five engines reported `timeout` |
| `podman-compose -f docker/searxng/docker-compose.yml down` | container and network removed |
| `.venv/bin/pytest -q` | `379 passed, 10 skipped` in a git checkout |
| `SLUICE_LIVE_TESTS=1 .venv/bin/pytest -q -k live` | `1 failed, 4 passed, 5 skipped` (Firecrawl keyless quota) |
| `python demo/fixture_server.py --port 8765` | nine fixture URLs printed |
| `curl -i http://127.0.0.1:8765/cf` | `HTTP/1.1 403 Forbidden` plus `cf-mitigated: challenge` |
| the README quick-start script (`provider.search` plus `provider.extract` on fixtures) | search answered by `exa/keyless`; `/ok` ok at `http`, `/js` ok at `chromium`, `/private` `policy_blocked` at `policy` |
| GitHub Actions `ci` workflow | `test` job green on the pushed commit |
