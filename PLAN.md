# Sluice: build plan

Companion to `DESIGN.md` (locked v3) and `references/endpoints.md`. Every task below is
sized for one worker with no context beyond this repo. Acceptance criteria are the bar for
`kanban_complete`, not suggestions.

Freeze: end of day 5, 10 Nov 2026. Submission: catalog PR opened and Typeform submitted on
10 or 11 Nov, video the same day.

## Closed gaps

The four items left open in the design are settled here.

**1. Vendor payloads.** `references/endpoints.md`, exact shapes and failure mapping for
all twelve vendors. No builder guesses left in that file. Perplexity and Parallel extract
paths are named; xAI is marked LLM-mediated and fragile.

**2. Cache and circuit numbers (T2).** The authoritative source of truth for all store thresholds is `sluice/store.py:StorePolicy`.
Search TTL 900 s general, 300 s for news; extract TTL 3600 s; negative cache 60 s for a
failed URL so a dead link is not re-fetched on every call; domain memory 24 h (86400 s).
Circuit: three consecutive failures opens for 15 min (900 s); one quota error opens for
60 min (3600 s); a success resets both. Eviction: on write, if the DB exceeds 200 MB
(`cache_max_mb`), delete expired rows first, then least-recently-hit rows down to a 150 MB
low-water mark (`cache_low_water_mb`) to provide effective hysteresis. One `VACUUM` per week
(604800 s), at most. All of it configurable with those defaults.

**3. Pane layout (T11).** Right-side pane, 380 px: header with status dot, active backend
count and Refresh; a 12-row backend table (Name, Tier, Key, Health, p50, Calls, Spend) with
a row expanding to last error and plan toggle; a test-query box at the bottom whose result
line names backend, tier and latency. Settings page holds tier order, per-backend plan,
keyless toggle, cache TTL, timeouts, the real-profile link and the two config-route
buttons. Palette entries: probe backends, test query, clear cache. Use `PANES_AREA`
(`placement: right`, `width: 380px`), `registerSettingsPage`, `PALETTE_AREA`. Poll
`/api/plugins/sluice-search-search/status` every 5 s while visible; no websockets. UI kit components only,
theme variables only, no markup queries.

**4. Demo target (T14).** `demo/fixture_server.py` serves deterministic cases: `/ok`
(plain HTML), `/js` (content injected by script), `/cf` (403 plus `cf-mitigated: challenge`),
`/imperva` (200 with an 83-byte body and `x-iinfo`), `/ratelimit` (429 with
`Retry-After: 2`). Record the challenge beat against the fixture so the demo never depends
on a live vendor; optionally show one real Cloudflare-fronted page afterwards if it behaves.

## Tasks

### T1: repository scaffold
Day 1. No dependencies.
Files: `plugin.yaml`, `__init__.py`, `pyproject.toml`, `.gitignore`, `LICENSE` (MIT),
`README.md` stub.
- `plugin.yaml`: `name: sluice`, version 1.0.0, `kind: backend`, `requires_hermes: ">=0.22.5"`,
  `python_dependencies: ["httpx>=0.27,<1", "trafilatura>=1.8,<3", "playwright>=1.45,<2", "ddgs>=9,<10"]`,
  `capabilities` matching exactly what `register(ctx)` does, `config_schema` with the settings
  and secret fields (`EXA_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`,
  `KEENABLE_API_KEY`, `BRAVE_API_KEY`, `PERPLEXITY_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`,
  `TINYFISH_API_KEY`).
- `__init__.py` registers the provider class and CLI commands only; imports lazy.
- Done: `hermes plugins doctor .` loads the plugin and reports zero errors.

### T2: store
Day 1. Depends on T1.
Files: `sluice/store.py`, `tests/test_store.py`.
- SQLite in `plugin_data_dir()`: `backend_state`, `usage`, `cache`, `domain_memory`, `jobs`.
- Implement the numbers in Closed gap 2 exactly.
- Thread-safe connections; WAL; no ORM.
- Done: tests cover circuit open/close, quota block, TTL expiry, LRU eviction, domain memory TTL.

### T3: router
Day 1. Depends on T2.
Files: `sluice/router.py`, `tests/test_router.py`.
- Tier order local, keyless, free, paid; skip missing key, open circuit, exhausted quota,
  wrong capability; latency EMA ordering inside a tier.
- 2.5 s per-backend budget, 8 s total, sequential. A backend may declare a wider window of
  its own (ddgs does, because it is a local multi-engine scrape, not an API call); the total
  budget still bounds every attempt. While another candidate is still behind the current
  one, one default window is reserved out of what remains, so a widened attempt is capped at
  the total minus that window: ddgs gets 5.5 s of the 8 s and the next candidate keeps its
  full 2.5 s.
- Attach `backend`, `tier`, `cached`, `fallback_used`, `elapsed_ms` to every result.
- Done: tests prove a free backend beats a paid one, a failed backend falls through, and a
  total-budget expiry returns a clean error listing each backend's reason.

### T4: adapters A
Day 1-2. Depends on T1.
Files: `sluice/backends/searxng.py`, `ddgs.py`, `exa.py`, plus tests.
- Follow `references/endpoints.md` exactly.
- `ddgs` runs in a spawned child with a hard kill and a 12 s wall clock.
- Exa supports keyless MCP and keyed REST behind one backend entry.
- Done: unit tests with recorded responses; one live call per mode behind an env flag.

### T5: adapters B
Day 2. Depends on T4 (shares helpers).
Files: `parallel.py`, `firecrawl.py`, `tavily.py`, `keenable.py`, tests.
- Keyless and keyed modes per vendor; MCP helper reused for Parallel.
- Done: same bar as T4.

### T6: adapters C
Day 2. Depends on T5.
Files: `brave.py`, `perplexity.py`, `deepseek.py`, `tinyfish.py`, `xai.py`, tests.
- Brave's two-key trap and 422/402 mapping; DeepSeek 128-char clipping; xAI JSON-block
  parsing with annotation fallback.
- Done: same bar as T4, and each failure message names the actionable cause.

### T7: fetch ladder
Day 3. Depends on T2.
Files: `sluice/ladder.py`, `tests/test_ladder.py`, `tests/fixtures/blocks/*`.
- Nine classes from `RESEARCH-blocks.md`: ok, js_required, rate_limited, challenge,
  forbidden, auth_required, payment_required, policy_blocked, error.
- robots.txt per origin cached 24 h, `X-Robots-Tag`, Content-Signal; disallow returns
  `blocked_by_policy` with the rule quoted.
- Stages: honest `Sluice/1.0` httpx; real Chromium via Playwright with
  `--disable-blink-features=AutomationControlled`, host locale and timezone, cookies carried,
  15 s cap; Camofox REST when healthy; stop.
- 401/402/403 stop; 429 honours `Retry-After`; no CAPTCHA; no identity change.
- Done: fixture tests cover every class, each stop rule, and the robots path.

### T8: manager
Day 4. Depends on T1.
Files: `sluice/manage.py`, tests.
- Detect camofox plugin and server health, Playwright browsers, Browserbase key, packaged
  Chromium.
- Install Playwright and camofox server (podman, then docker, then npm) as jobs; never
  automatic.
- Config route: run `hermes config set ...` with the active profile env, 10 s timeout,
  `--apply` opt-in.
- Done: detection tests with stubbed binaries; a job record survives a restart.

### T9: CLI
Day 4. Depends on T3, T7, T8.
Files: `sluice/cli.py` registered through `ctx.register_cli_command`.
- `hermes sluice-search doctor`, `status`, `setup [--apply]`, `use search|extract`, `test
  --backend <id> "<query>"`, `cache clear`, `install playwright|camofox|browserbase`.
- `doctor` prints the twelve-backend inventory with free/paid and what is missing.
- Done: every command runs unattended, exits non-zero on failure, prints the exact next command.

### T10: plugin API
Day 5. Depends on T2, T3, T8.
Files: `dashboard/plugin_api.py`, `dashboard/manifest.json`.
- `GET /status`, `POST /probe`, `POST /test`, `POST /install`, `GET /jobs/{id}`,
  `POST /cache/clear`.
- Thin wrapper importing `sluice.*`; JSON only, no SSE.
- Done: routes mount at `/api/plugins/sluice-search-search/` after a gateway restart and answer under 300 ms
  with a warm store.

### T11: desktop pane
Day 5. Depends on T10.
Files: `desktop/plugin.js`.
- Layout exactly as Closed gap 3.
- No secrets, no app markup, no `eval`, imports only the SDK and React.
- Done: pane renders, refresh works, test query round-trips, settings page writes plugin
  settings, and `hermes plugins validate` passes the desktop surface check.

### T12: skill, README, disclosures
Day 5-6. Depends on T9.
Files: `skills/sluice/SKILL.md`, `README.md`.
- Skill: when the ladder climbs, what the classes mean, how to read `doctor`.
- README disclosures: network calls to each vendor, local browser and server processes,
  shell commands for installs, plugin data directory, stored credentials through Hermes.
- Done: README lists every vendor and every side effect; skill loads through `skill_view`.

### T13: test sweep and validation
Day 5-6. Depends on all.
- `pytest` green; `hermes plugins validate . --install-deps` green; `hermes plugins doctor` clean.
- Done: both commands pass on the pinned commit, not the working tree.

### T14: submission
Day 6. Depends on T13.
Files: `demo/fixture_server.py`, `demo/script.md`.
- Fixture per Closed gap 4. Record under 90 s following `DESIGN.md`.
- Open the catalog PR with one entry pinning a full 40-character SHA; submit the Typeform.
- Done: PR URL and form confirmation recorded in the repo's `SUBMISSION.md`.

## Order and parallelism

Day 1: T1 → T2 → T3 (serial, same worker or three workers with the scaffold first).
Day 2: T4, T5, T6 in parallel once helpers exist.
Day 3: T7 alone.
Day 4: T8 and T9 in parallel.
Day 5: T10 then T11; T12 alongside.
Day 6: T13, T14.

Anything that slips comes out of T6 paid adapters first, never out of T7 or T11.
