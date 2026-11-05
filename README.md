# Sluice

Sluice is one Hermes plugin that owns the web layer. It serves the native `web_search`
and `web_extract` tools through a single routing provider over twelve backends, and it
manages every engine it can use: a local SearXNG, keyless public endpoints, free-plan keys
and paid keys.

Routing is free-first: local, then keyless, then free-plan keys, then paid. Paid is last by
default, never exclusive. Extraction is local only: honest `httpx` plus trafilatura, then
real Chromium through Playwright, then the Camofox REST server, with a deterministic block
classifier deciding when to climb and when to stop.

- `PLAN.md` — the build plan and acceptance criteria, task by task.
- `DESIGN.md` — the locked design (architecture, tier policy, extract chain, non-goals).
- `REQUIREMENTS.md` — the traceable requirements extracted from the plan.
- `references/endpoints.md` — exact request and response shapes for all twelve vendors.

## Install

```sh
hermes plugins install abhigyan-chatterjee/sluice
hermes plugins validate abhigyan-chatterjee/sluice
hermes sluice doctor          # inventory: tier, plan, key, what is missing
hermes sluice setup --apply   # point web_search and web_extract at Sluice
```

`setup` prints three `hermes config set` commands and only runs them with `--apply`:

```sh
hermes config set web.search_backend sluice
hermes config set web.extract_backend sluice
hermes config set web.cache_enabled false
```

The outer Hermes cache is turned off so there is exactly one cache with one TTL. Sluice
never writes `config.yaml` itself.

API keys are set in **Settings ▸ Plugins**, not through the CLI and not through the pane:
Hermes stores them in the profile's `.env` and Sluice only ever reads presence, never a
value. There is no key input anywhere in the plugin.

## Commands

| Command | What it does |
|---|---|
| `hermes sluice doctor` | twelve-backend inventory, free/paid, key state, browser engines, exact next command per gap |
| `hermes sluice status` | circuit, quota, p50/EMA latency, calls, spend, cache size, per-domain block memory |
| `hermes sluice setup [--apply]` | print (or run) the three config routes |
| `hermes sluice use search\|extract [--apply]` | switch one Hermes backend to Sluice |
| `hermes sluice test --backend <id> "query"` | one backend, one query, latency and rows |
| `hermes sluice cache clear` | clear the Sluice SQLite cache **and** Hermes' `cache/web` |
| `hermes sluice install playwright\|camofox\|browserbase` | run an install job; never automatic |

## Backends

One entry per vendor. A vendor with a key runs keyed; without one it runs its keyless path
when keyless tiers are enabled; otherwise it is skipped, and the reason is printed.

| Backend | Tier | Search | Extract | Key |
|---|---|---|---|---|
| SearXNG | local | yes | no | none (self-hosted URL) |
| ddgs | keyless | yes | no | none |
| Exa | keyless / free | yes | yes (keyed REST) | `EXA_API_KEY` |
| Parallel | keyless / paid | yes | yes (MCP) | `PARALLEL_API_KEY` |
| Firecrawl | keyless / free | yes | yes | `FIRECRAWL_API_KEY` |
| Tavily | keyless / free | yes | yes | `TAVILY_API_KEY` |
| Keenable | keyless / paid | yes | yes | `KEENABLE_API_KEY` |
| Brave | free / paid | yes | no | `BRAVE_API_KEY` |
| Perplexity | paid | yes | yes | `PERPLEXITY_API_KEY` |
| DeepSeek | paid | yes | yes | `DEEPSEEK_API_KEY` |
| TinyFish | paid | yes | no | `TINYFISH_API_KEY` |
| xAI | paid | yes | no | `XAI_API_KEY` |

Search-only backends raise `NotImplementedError` from `extract`; the router never calls
them for extraction. `extract` on the native tool is served by the local ladder.

Per-vendor plans (`plan_exa`, `plan_brave`, …) only change routing order inside the keyed
tiers. A free key is never labelled paid, and a paid key never jumps a free one.

## Network and process disclosures

Sluice makes network calls only when you or a Hermes tool ask it to. The destinations:

- **Search/extract APIs**: the twelve vendors above, at their documented hosts —
  `api.exa.ai` and `mcp.exa.ai`, `api.parallel.ai` and `search.parallel.ai`,
  `api.firecrawl.dev`, `api.tavily.com`, `api.keenable.ai`, `api.search.brave.com`,
  `api.perplexity.ai`, `api.deepseek.com`, `api.search.tinyfish.ai`, `api.x.ai`, plus your
  own `SEARXNG_URL`. The keyless paths (Exa MCP, Parallel MCP, Firecrawl, Tavily,
  Keenable public) send no credential.
- **Target pages**: extraction fetches the URL you asked for, its `robots.txt`, and — only
  when the ladder climbs — the same URL through Chromium or Camofox. Responses are not sent
  anywhere else.
- **Local servers**: Camofox at `CAMOFOX_URL` (default `http://localhost:9377`) and SearXNG
  at `SEARXNG_URL`, if you run them. Health is checked with `GET /health` and
  `GET /search`.

Local processes and binary use:

- **Playwright Chromium** is launched headless by the ladder (one browser per fetch, 15 s
  cap). `hermes sluice install playwright` runs `python -m playwright install chromium`,
  which downloads the browser build. Nothing is installed otherwise.
- **Camofox** is a Node server you run yourself. `hermes sluice install camofox` either
  starts the container `sluice-camofox` from `ghcr.io/jo-inc/camofox-browser:latest`
  (podman first, then docker) or, with no container runtime, installs
  `@askjo/camofox-browser` into the plugin data directory and prints the `npx` command
  that starts it. The first start downloads Camoufox (the vendor calls it "hundreds of
  MB"). The pane's Stop button runs `podman|docker stop sluice-camofox`.
- **Packaged Chromium** (`google-chrome`, `chromium`, …) is only detected, never launched
  by Sluice.

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

The SQLite store lives in the plugin data directory — `ctx.plugin_data_dir()`, normally
`~/.hermes/plugin-data/sluice/sluice.db`, or `~/.hermes/plugins/sluice/data` outside a
Hermes context. It holds:

- the search/extract cache (search 900 s general / 300 s news, extract 3600 s, failed URL
  60 s), evicted LRU down to a low-water mark when the file passes 200 MB;
- backend state: circuit breakers (3 consecutive failures → 15 min, one quota error →
  60 min), quota windows, latency EMA and p50 samples;
- usage rows (backend, kind, ok, latency, cost, time) and per-domain block memory for 24 h;
- install job records.

No credential is ever written to that store. Keys live in the profile `.env` through
Hermes and are read through `get_provider_env` (or `os.environ` outside Hermes); the pane
and the API expose presence and the env var name only.

The dashboard API is mounted at `/api/plugins/sluice/` and speaks JSON only; the desktop
pane polls `/status` every 5 s while visible.

## Development

```sh
uv venv
uv pip install -e ".[dev]"
pytest                       # live vendor calls are skipped unless SLUICE_LIVE_TESTS=1
```

```sh
hermes plugins doctor .
hermes plugins validate .
```

Reference local SearXNG with JSON enabled:

```sh
docker compose -f docker/searxng/docker-compose.yml up -d
export SEARXNG_URL=http://localhost:8080
```

Deterministic demo targets: `python demo/fixture_server.py --port 8765`; the script is in
`demo/script.md`.

## Non-goals

No browser engine of its own, no automatic SearXNG deployment, no answer synthesis or deep
research, no CAPTCHA solving, no paywall or login circumvention, no billing or key
purchase, and no silent fallback that hides which backend answered.

## License

MIT. See `LICENSE`.
