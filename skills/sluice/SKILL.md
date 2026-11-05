---
name: sluice
description: Use when a web fetch is blocked or a search returns nothing useful.
version: 1.0.0
---

# Sluice: reading a block, and when the ladder climbs

Sluice is Hermes' web layer when it is the configured provider: one `web_search` and
`web_extract` surface over twelve backends, free first. Two things are worth knowing when
a page comes back empty or a search fails: **which backend answered** and **which rung of
the fetch ladder was tried**.

## Reading `hermes sluice doctor`

```
backend      tier     plan  key      status
searxng      local    -     n/a      ready
ddgs         keyless  -     keyless  ready
exa          keyless  free  keyless  ready
brave        free     free  missing  missing: BRAVE_API_KEY is not set
deepseek     paid     paid  missing  missing: DEEPSEEK_API_KEY is not set
```

- **tier** is the cheapest way that backend can run right now: `local` (SearXNG),
  `keyless` (anonymous public endpoint), `free` (a free-allowance key), `paid`.
- **key** is `n/a` (no key exists), `keyless` (anonymous path available), `set`, or
  `missing`.
- **status** is `ready` or the actionable reason it is not.
- The `browser engines` block below the table is separate: Playwright Chromium, packaged
  Chromium, the Camofox server and the Browserbase key. The ladder falls back without
  them; they make it climb further.

Every missing line prints the exact next command. Set API keys in Settings ▸ Plugins, not
in a shell: Sluice never writes a credential.

## The fetch ladder

Extraction is local only; no paid extract API is called by the router.

| Stage | What it is | Climb trigger |
|---|---|---|
| 0 | Policy: `robots.txt` (24 h cache), `X-Robots-Tag`, `Content-Signal` | a disallow stops here, with `blocked_by_policy` |
| 1 | Honest HTTP: `httpx` with `User-Agent: Sluice/1.0`, trafilatura | empty or near-empty text, or a block class |
| 2 | Real Chromium through Playwright (`--headless=new`, AutomationControlled disabled, host locale/timezone, cookies carried) | still a block class, or `js_required` |
| 3 | Camofox REST server, when health is up | Chromium was challenged too |
| 4 | Stop | 401/402/403, or nothing left to try |

Stop rules are hard: `401` (`auth_required`), `402` (`payment_required`) and `403`
(`forbidden`) stop immediately. `429` honours `Retry-After` and retries once. Sluice never
solves a CAPTCHA and never changes identity or IP after a refusal.

The returned `content` starts with one provenance line, e.g.
`[sluice: stage=chromium status=ok]`, so the stage survives Hermes' extract trimming.

## The nine block classes

| Class | Meaning | Useful next step |
|---|---|---|
| `ok` | page read | nothing |
| `js_required` | shell page, content injected by script | ladder climbs to Chromium |
| `rate_limited` | 429 | wait for `Retry-After`; the ladder retried once |
| `challenge` | Cloudflare, Akamai, DataDome, HUMAN, Kasada, Imperva, AWS WAF or Fastly marker | ladder climbs; if all rungs fail the site refuses automated access |
| `forbidden` | plain 403 with no challenge marker | stop; do not retry |
| `auth_required` | 401 | user must sign in; `browser.use_real_profile` is the user's switch |
| `payment_required` | 402, or a `crawler-price` header | stop; the page is paid content |
| `policy_blocked` | robots.txt, `X-Robots-Tag` or Content-Signal refuses | stop; quote the rule, do not fetch |
| `error` | transport or parse failure | check the message; a dead link is cached negative for 60 s |

A domain that keeps challenging is remembered for 24 h in `domain_memory`, so the next
fetch starts at the rung that worked last time. That memory is visible in the Sluice pane
and in `hermes sluice status`.

## Cache and circuits

- Search: 900 s general, 300 s news. Extract: 3600 s. Failed URL: negative 60 s.
- Three consecutive failures open a backend for 15 min; one quota/credit error opens it for
  60 min. A success resets both.
- `hermes sluice status` prints circuit, quota, p50/EMA, calls, spend and cache size.
- `hermes sluice cache clear` clears both the Sluice SQLite cache and Hermes' outer
  `cache/web` directory. The setup route turns the outer cache off so one TTL rules.

## Commands

```sh
hermes sluice doctor                      # inventory, free/paid, what is missing
hermes sluice status                      # circuit, quota, latency, cache, block memory
hermes sluice test --backend ddgs "query" # one backend, one query, latency
hermes sluice setup --apply               # route web_search/web_extract through Sluice
hermes sluice cache clear
```

## What Sluice never does

No CAPTCHA solving, no paywall or login circumvention, no forged identities, no automatic
installs of browsers or servers, and no silent fallback that hides which backend answered.
Every search row carries `backend`, `tier`, `cached`, `fallback_used` and `elapsed_ms`;
an extract item carries `block_class` and `stage`.
