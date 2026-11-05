# Why agents get blocked, and what Sluice should do about it

Research compiled 2026-11-05 for the Sluice Hermes plugin. Four source reports live in
`research/`: vendor block signatures, browser fingerprint measurements, network-stack
fingerprinting, and the legal and policy landscape. This document is the synthesis and the
feature spec that follows from it.

## 1. The blocking stack, layer by layer

A request can be refused at five independent layers. Fixing one does nothing for the
others, which is why "we set a browser user agent" is the most common wasted afternoon in
scraping.

**Network identity.** The TCP/IP shape, the TLS ClientHello and the HTTP/2 settings frame
identify the client before any URL is read. A Chrome UA sent over a Python TLS stack is a
contradiction that bot managers score. JA3 was the first hash of the ClientHello; Chrome's
2023 extension-order randomisation broke a lot of JA3 matching, and JA4 sorts extensions
and adds ALPN and signature algorithms, so it survives that. Akamai's HTTP/2 fingerprint
covers SETTINGS, WINDOW_UPDATE, PRIORITY and pseudo-header order, not ordinary headers.

**Client environment.** Once JavaScript runs, the page can inspect the browser. The 2026
web-scale study found 46% of surveyed sites probe JavaScript properties that exist only in
automated browsers. Measured on this machine (Playwright 1.64.1, Chromium 153, Chrome 155):
every headless mode still says `HeadlessChrome` in the UA; Playwright sets
`navigator.webdriver` to true through `--remote-debugging-pipe`; the old headless shell has
no `window.chrome`, zero plugins and no `Accept-Language` header; all headless runs render
WebGL through SwiftShader; the shell's Notification permission disagrees with the
permissions API. Two dozen cheap tells, none of them exotic.

**Behaviour.** Request rate, cadence, navigation depth, mouse and keyboard shape, session
length. CDP-dispatched input reports `isTrusted: true`, so event trust is not a
discriminator; timing and trajectory are.

**Vendor decision systems.** Cloudflare, Akamai, DataDome, HUMAN, Kasada, Imperva, AWS WAF
and Fastly each combine the layers above with their own scoring and challenge. Their public
signals are in `research/bot-management-block-signatures.md`; the short version is that the
status code alone will mislead you.

**Policy.** robots.txt, `X-Robots-Tag`, Content-Signal, AI crawler tokens and pay-per-crawl.
Separate from detection, and the layer where the legal line actually sits.

## 2. What a block looks like on the wire

Status codes are not reliable on their own. Cloudflare challenges can arrive as 2xx or 403.
Imperva can return 200 with an 83-byte incident page. AWS WAF Challenge is 202, its CAPTCHA
is 405. Akamai's interstitial is 428 with a `sec-cpt` header. Kasada characteristically
answers 429. Fastly's default block is 406.

The reliable signal is a vendor-specific marker:

| Vendor | Strongest signal | Also seen |
|---|---|---|
| Cloudflare | `cf-mitigated: challenge` | `__cf_bm`, `cf_clearance` (~30 min), `cf_chl_*` |
| Akamai | `akamai-grn` on a 403 | `_abck`, `bm_sz`, `sensor_data`, 428 `sec-cpt` |
| DataDome | `x-datadome: protected` | `datadome` cookie, `captcha-delivery.com` URLs |
| HUMAN | `px-captcha` | `_px3` (minutes), `_pxhd`/`_pxvid` (a year) |
| Kasada | `x-kpsdk-ct` / `-cd` / `-st` | 429, proof-of-work VM |
| Imperva | `x-iinfo`, `visid_incap`/`incap_ses` | 200 with an 83-byte body |
| AWS WAF | `x-amzn-waf-action` | 202 challenge, 405 CAPTCHA |
| Fastly | `_fs_ch_*` / `_fs_cd_cp_*` | 406 block |

Cookie names appear on successful responses too, so they are diagnostic, not decisive.

## 3. What actually moves the needle

Ranked by evidence, strongest first:

1. **Correct HTTP headers.** The 2026 study's header-spoofing experiment attributes 75% of
   Chromium-headless-only blocks to header-level signals alone. Accept-Language, consistent
   client hints, and a user agent that matches the engine do most of the work.
2. **A real browser instead of a headless shell.** The full Chrome build has
   `window.chrome`, PDF plugins, consistent permissions and normal headers. The shell has
   none of that. Playwright's `headless: true` uses the shell by default; the ladder must not.
3. **`--disable-blink-features=AutomationControlled`.** Measured: this is what actually
   clears `navigator.webdriver`, and it leaves the native getter intact. A JavaScript
   override does not: the descriptor and `toString` give it away, and a main-frame-only
   patch leaks in same-origin iframes.
4. **Consistency over randomness.** Fingerprint-inconsistency research shows per-load
   randomisation of canvas, audio and fonts makes a client look worse, not better. One
   stable, plausible profile beats a different fake every visit.
5. **IP, locale, timezone agreement.** A datacenter IP in Frankfurt with `Asia/Calcutta`
   timezone and `en-GB` locale is internally inconsistent. Match what you can honestly match.
6. **Pacing.** Honour 429 and `Retry-After`; per-domain rate limits; no bursts.
7. **TLS impersonation in the HTTP client** (curl_cffi and friends) reproduces the network
   layer but not the JavaScript layer, and it is a claim to be a browser that the rest of
   the client does not honour.

## 4. What does not work, or is a trap

- **User-agent spoofing alone.** Headers are 75% of the headless gap, but a spoofed UA over
  a Python TLS stack and a headless JS environment is three contradictions at once.
- **JavaScript patches in the main frame only.** They leak in iframes and workers, and the
  descriptor shape gives them away.
- **Rotating identity after a refusal.** This is precisely the behaviour Cloudflare
  documented when it de-listed Perplexity: a Chrome-impersonating UA, rotating IPs and
  ASNs, and skipped robots.txt after a block. It is also what gets a whole client class
  banned at the edge.
- **CAPTCHA farms and solver services.** Anthropic publishes that it will not solve
  CAPTCHAs for the sites it crawls. Any client doing it is opting out of the good-crawler
  category permanently.
- **Per-load fingerprint randomisation.** It creates temporal inconsistency, which is
  itself a detection signal.

## 5. Where the line sits

The sources converge on a narrow, practical rule. It is not "may I fetch this" but "was I
told not to, and did I route around the refusal".

- RFC 9309 says robots.txt rules "are not a form of access authorization", and that the
  protocol is not a security mechanism. A 4xx means crawlable; a 5xx means treat as
  disallowed; a cached copy is good for at most 24 hours. `Crawl-delay` is not in the RFC;
  429 with `Retry-After` is the standard signal.
- hiQ v. LinkedIn: compliance is voluntary, and the case that supposedly legalised scraping
  ended in a contract loss, a permanent injunction and $500,000.
- Meta v. Bright Data: logged-off scraping of public pages did not breach those particular
  terms, in one district court, with no appellate ruling behind it.
- Cloudflare's published test for a good crawler: identify honestly, serve a clear purpose,
  separate bots by activity, stay within rate limits, honour robots.txt, never bypass
  security protections.
- The status codes mean different things and must not be conflated: 429 means slow down;
  403 is a refusal and re-authenticating will not change it; 401 is an identity gate and
  walking around it is the one that maps onto the CFAA; 402 is a price that collapses into a
  403 when there is no billing relationship.

So the operating rule for Sluice: identify honestly, honour robots.txt and Content-Signal,
respect 429, stop at 401, 402 and 403, never solve or outsource a CAPTCHA, never rotate
identity to escape a refusal, and prefer a real browser over a disguised HTTP client.

## 6. The feature: Fetch Ladder

Sluice's extract chain becomes a classifier plus a ladder. The classifier names what
happened; the ladder decides what, if anything, to try next.

### 6.1 Block classifier

Deterministic, offline, no guessing. Inputs: status, headers, cookie names, body markers,
body size, content type. Output: one class.

| Class | Triggers | Action |
|---|---|---|
| `ok` | usable content | return |
| `js_required` | 200 but a shell page, `<noscript>`, or tiny body | climb |
| `rate_limited` | 429, or 503 with `Retry-After` | wait, retry once |
| `challenge` | vendor header, cookie or body marker | climb |
| `forbidden` | 403 with no challenge marker | stop, report |
| `auth_required` | 401, or a login redirect | stop, offer real profile |
| `payment_required` | 402, or `crawler-price` | stop, report price |
| `policy_blocked` | robots.txt or Content-Signal disallow | stop, `blocked_by_policy` |
| `error` | 5xx after one retry | stop, report |

### 6.2 Ladder

0. **Policy.** Fetch and cache robots.txt per origin (24 h), evaluate the path and our
   token, read `X-Robots-Tag` and any Content-Signal. A disallow returns
   `blocked_by_policy` with the rule quoted, not a silent failure.
1. **Honest HTTP.** `httpx`, honest `Sluice/1.0` UA, correct `Accept`, `Accept-Language`
   from the host locale, gzip, per-domain pacing, shared cookie jar.
2. **Real Chromium.** Playwright with the full Chrome build, not the headless shell;
   `--disable-blink-features=AutomationControlled`; locale and timezone from the host;
   a realistic viewport; cookies carried over from stage 1; 15 s cap.
3. **Camofox.** The local Camoufox server when installed, for pages that challenge the
   Chromium session.
4. **Stop.** Return the last response plus the block report. Never loop.

### 6.3 Rules the ladder enforces

- A 403 is never retried in the same session, and never retried with a different identity.
- A 401 or a login redirect stops the ladder and points at the real-profile switch, which
  the user owns.
- A 402 is reported with the advertised price, never paid automatically.
- No CAPTCHA solving, no solver service, no proxy rotation, no identity switching after a
  refusal.
- A per-domain memory of `(class, stage that worked)` with a 24 h TTL, so a site that always
  challenges starts at the browser rung next time instead of burning the budget on stage 1.
- Every extract result carries its class and final stage in the one-line content header, and
  the full chain in SQLite and the pane.

### 6.4 Why this is a plugin feature and not a prompt

The classifier needs response headers, cookies and body bytes. The ladder needs process
control over a browser and a server. The per-domain memory needs durable state across
sessions. None of that is reachable from a prompt or a skill.

## 7. Sources

Primary, verified during synthesis:

- Gundelach, Mühlhauser, Herrmann, *Detecting Bot Detection*, arXiv:2606.14525 (2026):
  15% vs 7% soft-block rates, 82% attributable to bot detection, Cloudflare 37% and Akamai
  26%, 75% of headless-only blocks from header signals, 46% of sites probing JS.
- Cloudflare, *Perplexity is using stealth, undeclared crawlers* (2025-08-04): the
  good-crawler criteria and the de-listing pattern.
- RFC 9309, *Robots Exclusion Protocol*: "not a form of access authorization".
- hiQ Labs v. LinkedIn, 31 F.4th 1180 (9th Cir. 2022) and the 2022 consent judgment.

Full citation lists with per-claim confidence: `research/bot-management-block-signatures.md`,
`research/browser-fingerprint-report.txt`, `research/network-fingerprinting-report.md`,
`research/automated-web-access-law-policy-2026.md`.
