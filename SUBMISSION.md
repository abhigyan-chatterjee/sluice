# Sluice — Submission & Release Logistics (T14)

This document provides the complete project submission summary for **Sluice**, drafts the upstream Hermes Plugin Catalog pull request, and enumerates the checklist items requiring Abhi in person.

---

## 1. Project Summary

- **Repository**: [https://github.com/abhigyan-chatterjee/sluice](https://github.com/abhigyan-chatterjee/sluice)
- **Primary Branch**: `main`
- **License**: MIT
- **Author**: Abhigyan Chatterjee (`officialpxrpose@gmail.com`, `abhi@nullbit.in`)
- **Compatibility**: Hermes Agent `>=0.22.5`

### What Sluice Is
Sluice is a production-grade, tiered, free-first web search and extraction engine for Hermes Agent. It unifies 12 search engines and a local fetch ladder behind Hermes' native search and extraction provider interfaces. Sluice prioritizes zero-cost local and keyless engines before consuming paid commercial API credits, and for extraction it tries plain HTTP first, escalates to headless Chromium when the page needs JavaScript or answers with a bot challenge, escalates to a local Camofox browser session next, and stops at 401, 402 and 403 and at robots.txt, `X-Robots-Tag` or `Content-Signal` refusals, quoting the rule it obeyed. It persists health and cache state in a single SQLite WAL database, and exposes real-time controls through Hermes Desktop and CLI.

### Closed Capability Gaps
1. **Vendor Payloads & Normalization (`references/endpoints.md`, `sluice/backends/_http.py`)**:
   Standardized search and extraction schemas across all 12 engines (DuckDuckGo, Exa, Parallel, Firecrawl, Tavily, Keenable, SearXNG, Brave, Perplexity, DeepSeek, TinyFish, xAI). Enforces strict error mapping (HTTP 402 and credit errors immediately trip `out_of_credit=True`, 429 performs a single `Retry-After` bounded backoff).
2. **Unified SQLite Store (`sluice/store.py`)**:
   Replaces fragmented outer caches with a single WAL-mode SQLite database (`sluice.db`) governed by `StorePolicy`. Features query-normalized caching (900s general / 300s news TTL), content extract caching (3600s TTL), negative URL failure caching (60s TTL), per-domain bot mitigation memory (24h TTL), per-backend circuit breakers (3 failures trip a 15-minute open circuit; quota exhaustion trips a 60-minute open circuit), hysteresis LRU eviction (triggers at 200 MB down to 150 MB low-water mark), and weekly VACUUM scheduling.
3. **Desktop Management Panel (`desktop/plugin.js`)**:
   Provides a 380px right-side pane (`PANES_AREA`), settings page (`registerSettingsPage`), and command palette entries (`PALETTE_AREA`) built strictly with Hermes Plugin SDK and theme tokens. Displays live health, latency EMA, call counts, and spend for all 12 backends; supports manual probe/test execution, cache wiping, and tier plan toggling without manual configuration editing.
4. **Deterministic Demo Fixture Server (`demo/fixture_server.py`)**:
   An in-repo loopback server simulating standard responses, dynamic JavaScript rendering, bot protection signatures (`/cf` Cloudflare Turnstile, `/imperva` challenge, `/ratelimit` 429), and policy restrictions (`/private` robots.txt disallow). Guarantees reproducible, hermetic validation and video recording without reliance on third-party uptime.

### Tiered Routing & Fetch Ladder
- **Tier Architecture (`sluice/router.py`)**:
  Routes requests through `local` (SearXNG) → `keyless` (DuckDuckGo, Exa MCP, Parallel MCP, Firecrawl, Tavily, Keenable) → `free` (free-plan keys for Exa, Firecrawl, Tavily and Brave) → `paid` (Parallel, Keenable, Perplexity, DeepSeek, TinyFish and xAI, plus any free-plan vendor switched to `paid`). Backends within the same tier are sorted dynamically by latency EMA. Any engine with an open circuit, exhausted quota, or missing credentials is skipped. A 2500 ms default window per backend attempt and an 8000 ms total budget bound each search; a backend that declares `budget_ms` gets the wider per-attempt window, still capped by the total, and while a candidate is still behind it one 2500 ms default window stays reserved (ddgs gets 5500 ms of the 8000 ms and the next candidate keeps its full 2500 ms).
- **Local Anti-Bot Extraction Ladder (`sluice/ladder.py`)**:
  Implements a nine-class block classifier: `ok`, `js_required`, `rate_limited`, `challenge`, `forbidden`, `auth_required`, `payment_required`, `policy_blocked` and `error`. Vendor markers (Cloudflare, Imperva, PerimeterX/HUMAN, Kasada, Akamai, DataDome, AWS WAF, Fastly) feed the `challenge` class. Escalates extraction across three stages: HTTP (httpx + trafilatura) → real Chromium through Playwright → the Camofox REST server, stopping at authentication and policy boundaries. Checks `robots.txt` before the first request and `X-Robots-Tag`/`Content-Signal` on every response.

### Hermetic Test Story
- **Test Results**: `552 passed, 10 skipped`, 0 failed across 13 test modules at the pinned commit `c5aac71`. The ten skips are the opt-in live vendor tests gated by `SLUICE_LIVE_TESTS=1`. Re-run `pytest -q` at the tip of `main` for the current number.
- **Hermetic Isolation**: Tests run against a synthetic temporary `HERMES_HOME` with zero writes to host configuration or live agent state.
- **Validation**:
  - `hermes plugins doctor --ci`: Passed (exit code 0).
  - `hermes plugins validate`: Passed (16/16 checks pass, exit code 0).
  - Clean-clone reproduction and automated GitHub Actions CI confirmed.

---

## 2. Plugin Catalog PR Draft

```markdown
### Summary
Add Sluice (v1.0.0) — a tiered, free-first web search and extraction router and backend manager for Hermes Agent.

Sluice provides a drop-in replacement for Hermes search and extract providers, unifying 12 search engines with automatic tier fallback (Local -> Keyless -> Free -> Paid), a local three-stage anti-bot extraction ladder with headless browser escalation, a unified SQLite WAL cache with hysteresis LRU, and a Hermes Desktop management panel.

### Manifest Summary
- **Catalog Key**: `sluice-search` (the `sluice` key is already taken upstream by an unrelated plugin)
- **Name**: `sluice`
- **Version**: `1.0.0`
- **Kind**: `backend`
- **Manifest Version**: `2`
- **Provides Web Providers**: `[sluice]`
- **Hermes Requirement**: `>=0.22.5`
- **License**: MIT
- **Repository**: https://github.com/abhigyan-chatterjee/sluice

### Declared Python Dependencies
- `httpx>=0.27,<1`
- `trafilatura>=1.8,<3`
- `playwright>=1.45,<2`
- `ddgs>=9,<10`

### Declared Settings (`config_schema`)
| Setting | Type | Default | Description |
|---|---|---|---|
| `tier_order` | string | `local,keyless,free,paid` | Routing priority order, lowest cost first |
| `keyless_enabled` | boolean | `true` | Enable anonymous public backends (DDGS, Exa/Parallel MCP, etc.) |
| `per_backend_budget_ms` | integer | `2500` | Wall-clock timeout per individual backend attempt |
| `total_budget_ms` | integer | `8000` | Cumulative wall-clock budget for search dispatch |
| `search_ttl_general` | integer | `900` | Search result cache TTL for non-news queries (seconds) |
| `search_ttl_news` | integer | `300` | Search result cache TTL for news queries (seconds) |
| `extract_ttl` | integer | `3600` | Content extraction cache TTL (seconds) |
| `negative_ttl` | integer | `60` | Negative cache duration for failed URLs (seconds) |
| `domain_memory_ttl` | integer | `86400` | Bot mitigation stage memory per domain (seconds) |
| `circuit_failures` | integer | `3` | Consecutive failures before opening a backend circuit breaker |
| `circuit_open_seconds` | integer | `900` | Duration to hold circuit open on consecutive failures (seconds) |
| `circuit_quota_seconds` | integer | `3600` | Duration to hold circuit open on quota/credit exhaustion (seconds) |
| `cache_max_mb` | integer | `200` | SQLite cache size high-water trigger for LRU eviction (MB) |
| `searxng_url` | string | `""` | Base URL for self-hosted SearXNG instance |
| `plan_<vendor>` | string | (varies) | Plan assignment (`free` or `paid`) for commercial vendors |

### Installation & Activation
```bash
# Install plugin from the catalog (key is sluice-search: an unrelated plugin already
# owns the `sluice` name upstream; the plugin itself installs and enables as `sluice`)
hermes plugins install sluice-search

# Or install straight from the repository
hermes plugins install https://github.com/abhigyan-chatterjee/sluice

# Switch Hermes search and extract providers to Sluice
hermes config set web.search_backend sluice
hermes config set web.extract_backend sluice

# (Optional) Install Playwright Chromium for local Stage 2 extraction
.venv/bin/sluice install playwright
```

### Verification
- 552 unit and integration tests passing, 10 skipped in a hermetic environment at the pinned commit `c5aac71`.
- `hermes plugins doctor --ci` and `hermes plugins validate` pass with 0 errors.
```

---

## 3. Submission Checklist & Typeform Package (Needs Abhi)

The following checklist explicitly enumerates items that require personal execution by Abhi. None are checked off.

### Action Checklist
- [ ] **Record Demo Video (<90s)**:
  - **Target Runtime**: 75–85 seconds (strict maximum 90 seconds).
  - **Reference Guide**: Follow `demo/script.md`.
  - **Pre-flight**:
    1. Start local fixture: `python demo/fixture_server.py --port 8765 &`
    2. Warm doctor cache: `hermes sluice doctor > /dev/null`
    3. Arrange screen: Terminal window (left), Hermes Desktop Sluice pane (right).
  - **Shot List & Narration Beats**:
    1. `0:00–0:12`: Run `hermes sluice doctor`. Highlight 12 backends, tier layout, and diagnostic next-action commands.
    2. `0:12–0:28`: Run `hermes sluice test --backend ddgs "zstd vs gzip compression ratio"`. Demonstrate returned backend and tier metadata.
    3. `0:28–0:44`: Execute simulated quota trip via store helper. Show immediate status update in desktop pane and seamless fallback to next backend tier.
    4. `0:44–1:05`: Run ladder extract against `http://127.0.0.1:8765/cf`. Show classification as `challenge`, escalation to Playwright Chromium, and the 24h block memory in desktop pane.
    5. `1:05–1:20`: Run ladder extract against `http://127.0.0.1:8765/private`. Demonstrate local `robots.txt` rejection before any outbound HTTP packet is sent.
    6. `1:20–1:25`: Close on `hermes sluice doctor` tier overview.
  - **Upload**: Post the video on **X and tag @NousResearch**. The form's field 5 is "X Demo Post Link" and it is required — a Loom/YouTube/Drive link does not satisfy it. Capture the post URL. See `submission/typeform.md` for the full field list.
- [ ] **Open Upstream Catalog Pull Request**:
  - Fork/clone the official Hermes plugins directory repository.
  - Paste the **Plugin Catalog PR Draft** content above.
  - Pin the exact 40-character commit SHA from `main`.
  - Catalog entry file: `submission/plugin-catalog-sluice.yaml` (in this repo).
  - Re-check the commit pin in that file against the tip of `main` at submit time.
- [ ] **Submit Typeform**:
  - Fill out the hackathon submission form using the pre-filled factual parameters below.

---

### Typeform Pre-filled Factual Parameters

| Field | Pre-filled Value | Notes / Source |
|---|---|---|
| **Plugin Name** | `sluice` (catalog key: `sluice-search`) | `plugin.yaml:1`. The catalog key cannot be `sluice`: an unrelated plugin already owns `plugin-catalog/sluice.yaml` upstream (orjinameh/airlock). The plugin still installs and enables as `sluice`. |
| **Short Description / Tagline** | Tiered free-first web search and extraction router and backend manager for Hermes | `plugin.yaml:3` |
| **Public Repository URL** | `https://github.com/abhigyan-chatterjee/sluice` | Public GitHub repo on `main` |
| **Pinned Commit SHA** | `c5aac7134d6ae95a173bf7d7e2b42cce0e301624` (the tree the catalog entry pins; re-check against the tip of `main` at submit time) | pytest 552 passed / 10 skipped on this SHA; `hermes plugins validate` 16/16 |
| **License** | `MIT` | `LICENSE`, `plugin.yaml:5` |
| **Author Name** | Abhigyan Chatterjee | `plugin.yaml:4` |
| **Contact Email** | `officialpxrpose@gmail.com` / `abhi@nullbit.in` | Verified profile contact |
| **Hermes Compatibility** | `>=0.22.5` | `plugin.yaml:8` |
| **Python Dependencies** | `httpx>=0.27,<1`, `trafilatura>=1.8,<3`, `playwright>=1.45,<2`, `ddgs>=9,<10` | `plugin.yaml:11-15` |
| **Core Capabilities** | Tiered Search Provider (`provides_web_providers: [sluice]`), three-stage Anti-Bot Extraction Ladder, WAL SQLite Store with Hysteresis LRU, Desktop Management Panel, Hermes CLI integration | Verified in codebase |
| **Test Verification** | 552 tests passed, 10 skipped (live opt-in), 0 failed across 13 modules at `c5aac71`; hermetic fixtures with synthetic `HERMES_HOME`; 16/16 plugin validation checks passed | `tests/`, `pytest`, `hermes plugins validate` |
| **Demo Video URL** | *(Requires Abhi)* | Must be the **X post URL** carrying the video with @NousResearch tagged — see `submission/typeform.md` |
| **Discord Username** | *(Requires Abhi)* | Required form field; not recorded anywhere |
| **Form** | `https://form.typeform.com/to/FwJLvOln` | "Hermes Agent Plugin Hackathon Submission Form" |
| **Catalog PR URL** | `https://github.com/NousResearch/hermes-agent/pull/152754` | Open, awaiting maintainer review |
| **Confirmation Signature** | *(Requires Abhi)* | Abhigyan Chatterjee |
