# Sluice

Sluice is one Hermes plugin that owns the web layer. It serves the native `web_search` and
`web_extract` tools through a single routing provider over twelve backends, and it manages
every engine it can use: a local SearXNG, keyless public endpoints, free-plan keys and paid
keys.

Routing is free-first: local, then keyless, then free-plan keys, then paid. Paid is last by
default, never exclusive. Extraction is local only: honest `httpx` plus trafilatura, then
real Chromium through Playwright, then the Camofox REST server, with a deterministic block
classifier deciding when to climb and when to stop.

```
web_search ──┐
             ├─► SluiceWebSearchProvider ─► Router ─► 12 backends ─► SQLite state
web_extract ─┘                              │
                                            └─► Fetch Ladder ─► http │ chromium │ camofox
```

Requirements: Hermes 0.22.5 or newer (`plugin.yaml`), Python 3.11 or newer.

## Why it exists

Hermes ships a provider registry and a config key you edit by hand, but no surface to
install, test, monitor or switch backends. `web-search-plus` adds a second tool surface
instead of serving the native one, and does not treat free tiers as first-class. The result
is overlapping plugins, no shared health state, and a fallback chain nobody can see.

Sluice serves the native tools, so cron sessions, gateway sessions and subagents route
identically without learning a new tool name. It keeps per-backend health, latency, quota and
circuit state across restarts, caches searches and extracts, and names the backend that
answered on every row.

## Install

```sh
hermes plugins install abhigyan-chatterjee/sluice
hermes plugins enable sluice
hermes plugins doctor sluice --ci
hermes sluice doctor
```

`hermes plugins install` asks before installing the plugin's four Python dependencies
(`httpx`, `trafilatura`, `playwright`, `ddgs`). `doctor --ci` exits non-zero on a manifest or
registration error. `hermes sluice doctor` prints the twelve-backend inventory: tier, plan,
key state and what is missing.

To route Hermes through Sluice:

```sh
hermes sluice setup --apply
```

That runs three `hermes config set` commands and nothing else:

```sh
hermes config set web.search_backend sluice
hermes config set web.extract_backend sluice
hermes config set web.cache_enabled false
```

Observed:

```
Sluice config route (Sluice never writes config.yaml itself):
hermes config set web.search_backend sluice   [ok]
hermes config set web.extract_backend sluice   [ok]
hermes config set web.cache_enabled false   [ok]

done: Hermes now routes web_search and web_extract through Sluice, outer cache off
```

The outer Hermes cache is turned off so there is exactly one cache with one TTL. Without
`--apply`, `setup` only prints the commands. Sluice never writes `config.yaml` itself and
never patches Hermes core.

Each route is also available on its own as `hermes sluice use search [--apply]` and
`hermes sluice use extract [--apply]`. The same operations work from a checkout through the
standalone console script:

```sh
.venv/bin/sluice doctor
.venv/bin/sluice test --backend exa --limit 2 "sqlite wal checkpoint"
```

## Minimal configuration

None. The keyless tier is on by default, so Exa's MCP endpoint, Parallel's MCP endpoint,
Firecrawl, Tavily and Keenable answer with no key at all, and `ddgs` runs from a local child
process with no key either. A backend with a key runs keyed; a backend without one runs its
keyless path when keyless tiers are enabled; otherwise it is skipped and the reason is
printed.

API keys are set in **Settings ▸ Plugins**, not through the CLI and not through the pane:
Hermes stores them in the profile's `.env` and Sluice reads presence through
`get_provider_env()`, never writes a value. There is no key input anywhere in the plugin.

The full settings reference, including every default and effect, is in
[`docs/configuration.md`](docs/configuration.md).

## Quick start

Save this as `example.py` at the root of a checkout and run it with the project's
interpreter. It does one keyless search and one extraction against the deterministic fixture
server in `demo/`, so it needs no keys and no configuration. The search needs network; the
extract does not.

```python
"""One keyless search and one extract. No API keys, no config."""

from demo.fixture_server import running_server
from sluice.provider import SluiceWebSearchProvider

if __name__ == "__main__":
    provider = SluiceWebSearchProvider()

    result = provider.search("sqlite wal checkpoint", limit=2)
    print("search success:", result["success"])
    for row in result["data"]["web"]:
        print(f'  {row["position"]}. {row["title"]}  [{row["backend"]}/{row["tier"]}]')
        print(f'     {row["url"]}')

    with running_server() as base:
        for url in (f"{base}/ok", f"{base}/js", f"{base}/private"):
            item = provider.extract([url])[0]
            print(f'{url.rsplit("/", 1)[-1]:>8}: {item["block_class"]} via {item["stage"]}')
            print("          ", (item.get("content") or "").splitlines()[0])
```

```sh
uv venv
uv pip install -e .
.venv/bin/sluice install playwright
.venv/bin/python example.py
```

`sluice install playwright` downloads Playwright's Chromium build. If it is skipped, the
Chromium rung uses a `google-chrome`, `google-chrome-stable`, `chromium`,
`chromium-browser` or `chrome` binary already on `PATH` instead. With neither present, the
`/js` line prints `js: js_required via http` and the other two lines are unchanged. The
output below was reproduced with the normal locale, `LANG=C.UTF-8` and `env -i`.

Observed output on 5 November 2026:

```
search success: True
  1. Write-Ahead Logging  [exa/keyless]
     https://sqlite.org/wal.html
  2. Checkpoint a database  [exa/keyless]
     https://sqlite.org/c3ref/wal_checkpoint_v2.html
      ok: ok via http
           [sluice: stage=http status=ok]
      js: ok via chromium
           [sluice: stage=chromium status=ok]
 private: policy_blocked via policy
           [sluice: stage=policy status=policy_blocked]
```

The three extract lines are the ladder doing its job on the fixture pages: `/ok` is read at
the HTTP rung, the JavaScript shell `/js` is read at the Chromium rung, and `/private` never
gets a request because `robots.txt` disallows it. Which keyless backend answers a search
varies with load and latency; every row names it. `ddgs` runs its query in a separate
interpreter that reads one JSON request on stdin and prints one JSON object, so the
`if __name__ == "__main__":` guard in the sample is conventional rather than required.

The same thing through Hermes, once the plugin is installed:

```sh
hermes sluice test --backend exa --limit 2 "sqlite wal checkpoint"
```

```
exa (keyless, keyless) answered in 886 ms with 2 result(s)

 1. Write-Ahead Logging
    https://sqlite.org/wal.html
 2. Checkpoint a database
    https://sqlite.org/c3ref/wal_checkpoint_v2.html
```

## Commands

| Command | What it does |
|---|---|
| `hermes sluice doctor` | twelve-backend inventory, free/paid, key state, browser engines, the exact next command per gap |
| `hermes sluice status` | circuit, quota, p50/EMA latency, calls, spend, cache size, per-domain block memory |
| `hermes sluice setup [--apply]` | print (or run) the three config routes |
| `hermes sluice use search\|extract [--apply]` | switch one Hermes backend to Sluice |
| `hermes sluice test --backend <id> "query"` | one backend, one query, latency and rows |
| `hermes sluice cache clear` | clear the Sluice SQLite cache **and** Hermes' `cache/web` |
| `hermes sluice install playwright\|camofox\|browserbase` | run an install job; never automatic |

Every command is unattended-safe: it never prompts, returns non-zero on failure, and prints
the command that comes next.

## Backends

One entry per vendor. A vendor with a key runs keyed; without one it runs its keyless path
when keyless tiers are enabled; otherwise it is skipped.

| Backend | Tier | Search | Extract | Key |
|---|---|---|---|---|
| SearXNG | local | yes | no | none (self-hosted URL) |
| ddgs | keyless | yes | no | none |
| Exa | keyless / free / paid | yes | yes | `EXA_API_KEY` |
| Parallel | keyless / paid | yes | yes | `PARALLEL_API_KEY` |
| Firecrawl | keyless / free / paid | yes | yes | `FIRECRAWL_API_KEY` |
| Tavily | keyless / free / paid | yes | yes | `TAVILY_API_KEY` |
| Keenable | keyless / paid | yes | yes | `KEENABLE_API_KEY` |
| Brave | free / paid | yes | no | `BRAVE_API_KEY` |
| Perplexity | paid | yes | yes | `PERPLEXITY_API_KEY` |
| DeepSeek | paid | yes | yes | `DEEPSEEK_API_KEY` |
| TinyFish | paid | yes | no | `TINYFISH_API_KEY` |
| xAI | paid | yes | no | `XAI_API_KEY` |

Search-only backends raise `NotImplementedError` from `extract`, and the router never calls
them for extraction: `web_extract` is served by the local Fetch Ladder. Per-vendor plans
(`plan_exa`, `plan_brave`, ...) only change routing order inside the keyed tiers. A free key
is never labelled paid, and a paid backend never jumps a free one.

## Network and process disclosures

Sluice makes network calls only when you or a Hermes tool ask it to. The destinations:

- **Search and extract APIs**: the twelve vendors at their documented hosts:
  `api.exa.ai` and `mcp.exa.ai`, `api.parallel.ai` and `search.parallel.ai`,
  `api.firecrawl.dev`, `api.tavily.com`, `api.keenable.ai`, `api.search.brave.com`,
  `api.perplexity.ai`, `api.deepseek.com`, `api.search.tinyfish.ai`, `api.x.ai`, plus your
  own SearXNG at the `searxng_url` setting. The keyless paths (Exa MCP, Parallel MCP,
  Firecrawl, Tavily, Keenable public) send no credential.
- **Target pages**: extraction fetches the URL you asked for, its `robots.txt`, and, only
  when the ladder climbs, the same URL through Chromium or Camofox. Responses are not sent
  anywhere else.
- **Local servers**: Camofox at `CAMOFOX_URL` (default `http://localhost:9377`) and SearXNG
  at `searxng_url` or `SEARXNG_URL`, if you run them. Health is checked with `GET /health`,
  and SearXNG with `GET /search`.

Local processes and binary use:

- **Playwright Chromium** is launched headless by the ladder, one browser per fetch, with a
  15 s cap. `hermes sluice install playwright` runs `python -m playwright install chromium`,
  which downloads the browser build. Nothing is installed otherwise.
- **Camofox** is a Node server you run yourself. `hermes sluice install camofox` either
  starts the container `sluice-camofox` from `ghcr.io/jo-inc/camofox-browser:latest` (podman
  first, then docker) or, with no container runtime, installs `@askjo/camofox-browser` into
  the plugin data directory and prints the `npx` command that starts it. The first start
  downloads Camoufox (the vendor calls it "hundreds of MB"). The pane's Stop button runs
  `podman|docker stop sluice-camofox`.
- **Packaged Chromium** (`google-chrome`, `chromium`, ...) is detected by the doctor and
  never launched by Sluice.

Shell commands Sluice may run, always from an explicit user action:

```sh
python -m playwright install chromium
podman|docker run -d --name sluice-camofox -p 9377:9377 -e CAMOFOX_PORT=9377 ghcr.io/jo-inc/camofox-browser:latest
podman|docker stop sluice-camofox
npm install --prefix <plugin data dir>/camofox @askjo/camofox-browser
hermes config set web.search_backend sluice      # only with --apply
hermes config set web.extract_backend sluice     # only with --apply
hermes config set web.cache_enabled false        # only with --apply
```

## Data and credentials

The SQLite store lives in the plugin data directory, `ctx.plugin_data_dir()`, normally
`~/.hermes/plugin-data/sluice/sluice.db`, or `~/.hermes/plugins/sluice/data` outside a
Hermes context. It holds:

- the search and extract cache, with TTLs (search 900 s general and 300 s news, extract
  3600 s, failed URL 60 s) and LRU eviction down to a low-water mark when the file passes
  200 MB;
- backend state: circuit breakers (3 consecutive failures, 15 min; one quota error, 60 min),
  quota windows, latency EMA and p50 samples;
- usage rows (backend, kind, ok, latency, cost, time) and per-domain block memory for 24 h;
- install job records.

No credential is ever written to that store. Keys live in the profile `.env` through Hermes
and are read through `get_provider_env()`, or `os.environ` outside Hermes; the pane and the
API expose presence and the env var name only.

The dashboard API is mounted at `/api/plugins/sluice/` and speaks JSON only. The Desktop pane
polls `/status` every 5 s while visible and holds no secret.

## Documentation

- [`docs/configuration.md`](docs/configuration.md): every setting, environment variable,
  default and effect, plus the two reconciled `PLAN.md` vs `DESIGN.md` thresholds.
- [`docs/architecture.md`](docs/architecture.md): module map, the search and extract data
  paths, the Hermes seams, and the extension points.
- [`docs/development.md`](docs/development.md): dev environment, tests, the fixture server,
  local SearXNG, validation, release.
- `PLAN.md`, `DESIGN.md`, `REQUIREMENTS.md`: the build plan, the locked design and the
  traceable requirements.
- `references/endpoints.md`: request and response shapes for all twelve vendors.

## Not yet implemented

Out of scope for v1, from `DESIGN.md:312-319`:

- Parallel or racing fan-out and result fusion. Routing is sequential.
- Per-domain routing rules set by the user. Domain memory is automatic.
- TLS impersonation profiles.
- Answer synthesis and deep research mode.
- A web dashboard that is not the Desktop pane.
- The `openai-native` backend.
- A custom CDP browser provider.
- Automatic SearXNG deployment. The compose file is a reference; nothing starts it for you.
- CAPTCHA solving, paywall or login circumvention.
- Per-call backend pinning in `web_search(query, limit)` or `web_extract(urls, char_limit)`.

Also not shipped:

- `SUBMISSION.md` and the 90-second demo recording (`PLAN.md` T14). They are external
  submission artifacts, not runtime code.

## Development

```sh
uv venv
uv pip install -e ".[dev]"
.venv/bin/pytest -q           # 379 passed, 10 skipped in a git checkout
```

Live vendor calls are opt-in with `SLUICE_LIVE_TESTS=1`. The fixture server for the demo is
`python demo/fixture_server.py --port 8765`. The full loop is in
[`docs/development.md`](docs/development.md).

## License

MIT. See `LICENSE`.
