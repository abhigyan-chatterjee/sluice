# Commercial bot management: detection signals and on-the-wire block signatures

**Scope.** Eight bot-management stacks, described only in terms of (a) what they inspect, (b) what a blocked or challenged response looks like at the HTTP layer, and (c) how long clearance lasts. Vendor documentation is preferred; where the vendor publishes nothing, this report relies on dated field reports and reverse-engineering write-ups and says so. All sources were accessed on **2026-11-05**.

---

## Cloudflare

**1. Every Cloudflare Challenge Page response carries `cf-mitigated: challenge`, and its content type is always `text/html`, regardless of the resource type requested.**
- Source: https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/
- Date: doc last updated 2026-05-05; accessed 2026-11-05.
- Confidence: **high**.
- Verified: read directly from Cloudflare's primary docs; the page states both the header value and the content-type invariant explicitly, and notes `challenge` is the only valid header value.

**2. A Challenge Page holds the request and evaluates the browser environment for automated signals; a Managed Challenge then dynamically picks the challenge type, and a Non-Interactive Challenge is normally resolved by JavaScript in under five seconds.**
- Source: https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/
- Date: doc last updated 2026-10-20; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Cloudflare docs describing the interception and the three challenge actions.

**3. Bot Fight Mode matches known-bot patterns and responds with "computationally expensive challenges" that force the client to perform CPU-intensive calculations; JavaScript Detections is automatically enabled with Bot Fight Mode and cannot be disabled.**
- Source: https://developers.cloudflare.com/bots/get-started/bot-fight-mode/
- Date: page undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Cloudflare docs, including the explicit limitation that Bot Fight Mode runs outside the Ruleset Engine and cannot be skipped by WAF rules.

**4. Cloudflare's bot score is 1–99 (1 = automated, 99 = human); heuristics assign 1 for high-confidence deterministic detections, the ML engine uses request headers, session characteristics and browser signals, and a missing or empty `User-Agent` is immediately scored 1.**
- Source: https://developers.cloudflare.com/bots/concepts/bot-score/
- Date: doc last updated 2026-08-26; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Cloudflare docs listing the detection engines and the explicit missing-UA rule.

**5. JA3/JA4 TLS fingerprints are available only to Enterprise Bot Management customers; they are computed during the TLS handshake and are absent for non-encrypted HTTP or when Bot Management is skipped.**
- Source: https://developers.cloudflare.com/bots/additional-configurations/ja3-ja4-fingerprint/
- Date: doc last updated 2026-05-06; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Cloudflare docs; the Enterprise-only and handshake-time constraints are stated outright.

**6. A solved challenge sets `cf_clearance`, whose default lifetime is 30 minutes; the lifetime is the zone's Challenge Passage setting (Cloudflare recommends 15–45 minutes), a few extra minutes are allowed for clock skew, and an extra hour is added for XHR requests.**
- Source: https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/
- Date: doc last updated 2026-05-05; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Cloudflare docs; all four timing details appear on the same page.

**7. `cf_clearance` is bound to the specific visitor and device and cannot be reused across machines; Turnstile tokens are separately valid for 300 seconds and are single-use.**
- Source: https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/ and https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
- Date: clearance doc last updated 2026-10-23; token doc last updated 2026-10-22; accessed 2026-11-05.
- Confidence: **high**.
- Verified: two primary Cloudflare docs; the binding language and the 300-second/single-use token rules are explicit. The docs say "visitor and device", not IP-and-UA; the IP/UA coupling is widely observed but not stated here.

**8. The HTTP status of a Cloudflare challenge is not a stable discriminator: Cloudflare's own 2023 example shows a Managed Challenge on a fetch returning 403, while a 2026 field report records current managed-challenge interstitials arriving as 2xx.**
- Source: https://blog.cloudflare.com/integrating-turnstile-with-the-cloudflare-waf-to-challenge-fetch-requests/ (2023-12-18) and https://github.com/Alih-b/Josty/issues/71 (2026-09-19)
- Date: sources dated 2023-12-18 and 2026-09-19; accessed 2026-11-05.
- Confidence: **high** that status alone is unreliable; **medium** on the exact 2xx frequency.
- Verified: Cloudflare's blog documents the 403 case with a screenshot-level description; the 2026 issue is a field survey of challenge handling that explicitly separates 2xx interstitials from 403 deny pages. Two independent observations conflict on status, which is itself the finding.

**9. A Cloudflare managed-challenge body is marked by `window._cf_chl_opt`, `Just a moment...`, `challenge-error-text`, a `/cdn-cgi/challenge-platform/.../chl_page/` script, and `__cf_chl_tk`; a Cloudflare deny page instead uses `cf-wrapper`, `cf-error-details`, `Attention Required!` and a Ray ID.**
- Source: https://github.com/Alih-b/Josty/issues/71 and https://community.cloudflare.com/t/javascript-challenge-shows-html-twice/540856
- Date: issue 2026-09-19; community post 2023-08; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: observed response bodies quoted in a field report and a Cloudflare community thread, cross-checked against each other. Cloudflare does not document these markers as a contract. The `cType` field inside `_cf_chl_opt` distinguishes at least the `non-interactive` and `interactive` variants in the quoted bodies.

**10. The `__cf_bm` cookie, set by Bot Management and Bot Fight Mode, expires after 30 minutes of continuous inactivity and carries encrypted bot-score information.**
- Source: https://developers.cloudflare.com/fundamentals/reference/policies-compliances/cloudflare-cookies/
- Date: doc last updated 2026-10-12; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Cloudflare cookie reference.

---

## Akamai Bot Manager

**11. Akamai's documented detection tiers are: validated-bot lists, customer-defined bot categories, transparent detection (header signatures, out-of-order headers, browser-version mismatches, bot-building frameworks), active detection (an interaction that only a browser completes), and Premier-only behavioral detection (movement patterns on transactional endpoints).**
- Source: https://techdocs.akamai.com/cloud-security/docs/detection-methods
- Date: doc says "Updated 8 months ago" (≈2026-03); accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Akamai technical documentation, read in full.

**12. Akamai exposes a 0–100 bot score (0 = human, 100 = bot) and groups responses into cautious, strict and aggressive segments, with actions including deny, tarpit and challenge.**
- Source: https://techdocs.akamai.com/cloud-security/docs/handle-adversarial-bots
- Date: doc says "Updated about 1 month ago" (≈2026-10); accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Akamai documentation; the score semantics and the three response segments are stated explicitly.

**13. Akamai's client-side sensor posts a `{"sensor_data": "..."}` body, usually to a `/_sec/` path such as `/_sec/cp_challenge/verify`, and the response rewrites the `_abck` cookie; `bm_sz` is set before any JavaScript runs and seeds the sensor encoding.**
- Source: https://blog.crawlex.net/blog/akamai-bot-manager-abck-cookie/
- Date: article dated 2026-06-04; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: one detailed practitioner write-up that explicitly flags the cookie byte layout as undocumented and derived from observed traffic; the `bm_sz`-as-seed claim is repeated in a companion article. No Akamai public document confirms the endpoint or payload shape.

**14. In observed traffic the Akamai cookie family has these lifetimes: `_abck` ≈365 days, `bm_sz` ≈4 hours, `ak_bmsc` ≈2 hours, plus shorter-lived `bm_sv` and `bm_mi`; a long `_abck` expiry does not mean the validated state lasts that long.**
- Source: https://blog.crawlex.net/blog/akamai-bmsz-pixel-challenge/ (2026-06-01) and https://www.cookie.is/bm_sz
- Date: article 2026-06-01; cookie catalogue entry undated, accessed 2026-11-05.
- Confidence: **medium**.
- Verified: two independent catalogues of observed cookies agree on the durations; the article states the trust state inside `_abck` lapses "in minutes to hours" without giving a vendor figure.

**15. Akamai's static denial is an HTTP 403 whose body contains "Access Denied" and an "Error ID" or "Reference #" string, with an `akamai-grn` response header; it may carry no `Set-Cookie` and no JavaScript challenge at all.**
- Source: https://github.com/madelponte/openwebui-tools/issues/2
- Date: issue created 2026-10-05; accessed 2026-11-05.
- Confidence: **high** for this observed shape; **medium** that it generalises to all Akamai properties.
- Verified: the issue quotes a live response from `www.aida.de` (403, body "Access Denied / Error ID: 0.946e1002.1791183855.cbd5720", `akamai-grn` header, `Server: AkamaiGHost`) and the maintainer's follow-up patch was tested against it. A separate write-up reports the same "Access Denied + Reference #" shape.

**16. A second Akamai layer can return HTTP 428 with a countdown for a `sec-cpt` proof-of-work interstitial, and a pixel beacon can mint `ak_bmsc` independently of the `_abck` handshake.**
- Source: https://blog.crawlex.net/blog/akamai-bmsz-pixel-challenge/
- Date: article 2026-06-01; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: one practitioner write-up citing public SDK parsers (`SecCptChallenge`, `ParsePixelHtmlVar`) and the `bazadebezolkohpepadr` page variable; Akamai publishes none of this. The 428 status itself is a real HTTP code (RFC 6585) repurposed here.

---

## DataDome

**17. DataDome's Protection API returns a decision through `X-DataDomeResponse`: 200 means allow, any other value means challenge and the edge must return the API's status, body and mapped headers verbatim without contacting the origin; the documented example is a 403 carrying `X-DataDome: protected`, `X-DD-B: 1` and `Set-Cookie: datadome=...`.**
- Source: https://docs.datadome.co/reference/validate-request
- Date: doc says "Updated about 2 months ago" (≈2026-09); accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary DataDome integration documentation, including the worked header example and the explicit fail-open rule when the header is missing.

**18. DataDome distinguishes response types in its own telemetry: `X-DataDome-Traffic-Rule-Response` takes `authorize`, `block` (slider), `hard_block` (block page) and `interstitial` (device check).**
- Source: https://docs.datadome.co/docs/logs-integration
- Date: doc says "Updated 3 months ago" (≈2026-08); accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary DataDome documentation listing the permitted values.

**19. The `datadome` cookie is a 128-byte (up to 512-byte) encrypted detection token with a one-year expiry and no PII.**
- Source: https://docs.datadome.co/docs/cookie-session-storage
- Date: doc says "Updated 21 days ago" (≈2026-10-15); accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary DataDome cookie reference. Note the distinction: one year is the browser cookie's storage life, not a documented verdict-validity window.

**20. DataDome's challenge and block pages load their scripts from `ct.captcha-delivery.com` and `geo.captcha-delivery.com`, which DataDome instructs customers to allow in CSP.**
- Source: https://docs.datadome.co/docs/javascript-tag and https://docs.datadome.co/changelog/change-needed-for-users-of-csp-directive-frame-src
- Date: docs undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary DataDome docs and changelog; the CSP guidance names the hosts explicitly as the place response-page scripts load from after a block.

**21. DataDome's server-side input set includes the full header list, client IP, cookies, session duration, active-session request count, time since previous request, and optionally TLS JA3/JA4; its scoring output includes bot name, bot family, confidence score and risk score.**
- Source: https://docs.datadome.co/reference/validate-request and https://docs.datadome.co/docs/logs-integration
- Date: API doc ≈2026-09; logs doc ≈2026-08; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary DataDome docs listing the payload fields and the enriched headers.

---

## HUMAN Security (PerimeterX)

**22. HUMAN's own cookie table gives `_px3` (and `_px`/`_px2`) a 5.5-minute expiry, `_pxhd` one year, `_pxvid` one year, and `pxcts` session scope.**
- Source: https://docs.perimeterx.com/pxconsole/docs/cookies
- Date: page undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary HUMAN documentation with an explicit cookie/lifetime table. The 5.5-minute value is the browser cookie's expiry, not a separately documented server-side verdict TTL.

**23. A blocked AJAX/fetch request can return a JSON Advanced Blocking Response instead of HTML; the object carries `appId`, `jsClientSrc`, `firstPartyEnabled`, `vid`, `uuid`, `hostUrl` and `blockScript`, and the challenge is rendered into an element with id `px-captcha`.**
- Source: https://docs.humansecurity.com/applications/advanced-blocking-response
- Date: page undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary HUMAN documentation, including the JSON schema, the `Accept: application/json` trigger, and the explicit instruction that the client handles 403 responses without redirecting.

**24. HUMAN's block page and integrated challenge use a press-and-hold interaction; the built-in block page, a customized block page, or an ABR-rendered challenge are the three delivery modes.**
- Source: https://docs.humansecurity.com/applications-and-accounts/docs/human-challenge-integration-testing-process
- Date: page undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary HUMAN testing documentation describing the challenge UI and the `x-px-captcha-testing` bypass header used to automate tests.

**25. HUMAN combines sensor telemetry, server-side IP/request scoring and a client-side JavaScript challenge; the `_px3` clearance token is issued after the sensor posts to the collector.**
- Source: https://scrapfly.io/blog/posts/how-to-bypass-anti-bot-protection and https://github.com/Pr0t0ns/PerimeterX-Reverse
- Date: Scrapfly article undated (2026); GitHub repo undated; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: two independent practitioner sources agree on the collector-then-`_px3` sequence; HUMAN's own docs confirm the cookies and challenge but not the scoring pipeline. Reports that `_px3` is HMAC-bound to the User-Agent are practitioner claims, not vendor documentation.

---

## Kasada

**26. Kasada's own architecture description is: hundreds of hidden client-side sensors, client-data validation for automation/tampering, "proof of execution" inside a highly obfuscated virtual machine, and anomaly detection in under 2 ms.**
- Source: https://www.kasada.io/bot-defense
- Date: page undated; accessed 2026-11-05.
- Confidence: **high** for what Kasada claims; **medium** for the specific mechanisms, which Kasada does not detail.
- Verified: primary vendor product page, read in full.

**27. In the wire protocol, requests carry `x-kpsdk-ct` (session token), `x-kpsdk-cd` (per-request proof-of-work data), and often `x-kpsdk-v`/`x-kpsdk-st`; a client without valid values gets HTTP 429, sometimes 403.**
- Source: https://github.com/ading2210/vercel-llm-api/issues/22 (2023-11-08), https://github.com/TheWebScrapingClub/webscraping-from-0-to-hero/blob/main/Pages/5.Antibot/Kasada.md, https://blog.crawlex.net/blog/kasada-kpsdk-token-vm/ (2026-05-20)
- Date: sources dated 2023-11-08, undated, and 2026-05-20; accessed 2026-11-05.
- Confidence: **high** that 429 is the characteristic block; **medium** on the full header set.
- Verified: the 2023 GitHub issue independently reports "if the headers' values aren't in any way valid, the API will respond with a 429"; the 2026 write-up and a scraping-community page report the same 429-first pattern and the `x-kpsdk-*` headers.

**28. Kasada's challenge flow is `/fp` probe → `/ips.js` VM script → binary POST to `/tl` → `x-kpsdk-ct` issued; `x-kpsdk-ct` is reused for the session and `x-kpsdk-cd` is regenerated per request.**
- Source: https://blog.crawlex.net/blog/kasada-kpsdk-token-vm/
- Date: article 2026-05-20; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: one detailed practitioner write-up citing third-party SDK integrations; Kasada publishes no endpoint or token documentation. The `/tl` endpoint and `application/octet-stream` body are corroborated by solver repositories, which are commercial and therefore weaker evidence.

**29. Practitioner estimates put the `x-kpsdk-ct` session window around 30 minutes (shorter on hardened sites), with `x-kpsdk-cd` single-use and tied to a server timestamp with a freshness window of a few seconds.**
- Source: https://blog.crawlex.net/blog/kasada-kpsdk-token-vm/
- Date: article 2026-05-20; accessed 2026-11-05.
- Confidence: **low**.
- Verified: single practitioner estimate, explicitly presented as observed rather than documented. No vendor figure exists.

---

## Imperva / Incapsula

**30. Imperva's edge sets `visid_incap_<site-id>` as a persistent visitor identifier and `incap_ses_<site-id>_<slot>` as a session cookie, often with `nlbi_*` for load balancing; protected responses carry `X-Iinfo` and sometimes `X-CDN: Imperva` or `Incapsula`.**
- Source: https://blog.crawlex.net/blog/imperva-incapsula-bot-detection/ (2026-05-18) and https://scrapfly.io/blog/posts/how-to-bypass-imperva-incapsula-anti-scraping
- Date: article 2026-05-18; Scrapfly article undated; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: two independent practitioner write-ups agree on the cookie names and the `X-Iinfo`/`X-CDN` headers; Imperva's public docs do not publish a cookie reference. The site-ID suffix is visible in the cookie names themselves.

**31. An Imperva block can arrive as HTTP 200 with a very small body: "Request unsuccessful. Incapsula incident ID: <id>" — roughly 83 characters in one measured case — so status code alone will miss it.**
- Source: https://github.com/D4Vinci/Scrapling/issues/472
- Date: issue created 2026-09-30; accessed 2026-11-05.
- Confidence: **high** for this observed case; **medium** that 200 is the common case.
- Verified: the issue reports identical output across four fetch modes (`curl_cffi` Chrome impersonation, Playwright, stealth, and stealth with Cloudflare solving), all HTTP 200 with the 83-character Incapsula body. A separate write-up reports both 403 and 200 block pages, so both shapes occur.

**32. Imperva runs two interrogation generations: an older `___utmvc` RC4-encrypted JavaScript challenge served under `/_Incapsula_Resource`, and a newer `reese84` sensor token posted to a site-specific path; the cookie present tells you which generation a property uses.**
- Source: https://blog.crawlex.net/blog/imperva-incapsula-bot-detection/
- Date: article 2026-05-18; accessed 2026-11-05.
- Confidence: **medium**.
- Verified: one detailed practitioner write-up; corroborated by solver documentation that lists `reese84`, `visid_incap_*` and `incap_ses_*` as the required cookie set. Imperva publishes no format documentation.

---

## AWS WAF (Bot Control)

**33. AWS WAF's Challenge action returns HTTP 202 with `x-amzn-waf-action: challenge`; the CAPTCHA action returns HTTP 405 with `x-amzn-waf-action: captcha`; both include a JavaScript interstitial only when the request's `Accept` header contains `text/html`, and neither response carries CORS headers.**
- Source: https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html
- Date: AWS docs, current version; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary AWS documentation stating each status code, header value and the HTML-only interstitial condition.

**34. Bot Control's Common level uses static request analysis and blocks bots it cannot verify; the Targeted level adds browser interrogation, fingerprinting, behavioral heuristics, ML-based anomaly detection, rate limiting, CAPTCHA and background challenges, with `TGT_`/`TGT_ML_` rule names.**
- Source: https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-bot.html
- Date: AWS docs, current version; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary AWS documentation describing both protection levels and the rule-name conventions.

**35. AWS WAF token immunity defaults to 300 seconds for both Challenge and CAPTCHA; the challenge minimum is 300 seconds, the CAPTCHA minimum is 60 seconds, and the maximum for both is 259,200 seconds (three days).**
- Source: https://docs.aws.amazon.com/waf/latest/developerguide/waf-tokens-immunity-times.html
- Date: AWS docs, current version; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary AWS documentation with all four numbers stated explicitly.

**36. The default Block action response is HTTP 403 Forbidden; customers can substitute a custom status and body.**
- Source: https://repost.aws/knowledge-center/waf-block-respond-403-forbidden-error
- Date: AWS re:Post article reviewed 2026-06-16; accessed 2026-11-05.
- Confidence: **high**.
- Verified: AWS-owned support article stating the default 403 and the custom-response override. It does not specify the default body text, and any custom response replaces it.

---

## Fastly (Next-Gen WAF and Bot Management)

**37. Fastly Next-Gen WAF's default block response is HTTP 406 Not Acceptable; the status is an agent response code and can be changed per rule to 301, 302, or any 400–599.**
- Source: https://docs.fastly.com/en/ngwaf/about-agent-response-codes and https://docs.fastly.com/signalsciences/using-signal-sciences/agent-mode/about-the-agent-mode/
- Date: docs undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: two primary Fastly docs pages agree that 406 is the default block and that it is configurable. This is the clearest vendor-published block status in the set.

**38. Fastly Bot Management uses JA3/JA4 client fingerprinting, verified-bot lists, and client challenges that can be dynamic, interactive (CAPTCHA-like) or non-interactive (JavaScript proof-of-work); an optional client-side snippet targets headless browsers.**
- Source: https://docs.fastly.com/products/bot-management
- Date: doc undated; accessed 2026-11-05.
- Confidence: **high**.
- Verified: primary Fastly product documentation listing each detection and challenge type.

**39. Fastly's challenge and client-detection state is carried in `_fs_ch_st_` (challenge start), `_fs_ch_cp_` (challenge complete) and `_fs_cd_cp_` (client-side detection complete) cookies, scoped to the exact FQDN rather than the registrable domain.**
- Source: https://docs.fastly.com/products/bot-management
- Date: doc undated; accessed 2026-11-05.
- Confidence: **high** for the cookie names and scope; **unknown** for lifetime.
- Verified: primary Fastly documentation names all three cookies and states the FQDN scoping limitation, including the warning that solving on `www.example.com` does not validate `api.example.com`.

---

## What remains uncertain

- **Cloudflare challenge status codes.** Cloudflare documents `cf-mitigated` and `text/html` but not a status code. A 2023 Cloudflare blog example shows 403; a 2026 field report shows 2xx interstitials. The split by plan, challenge type, and request mode (`Accept`, navigation vs fetch) is not published.
- **Cloudflare Bot Fight Mode's exact response.** The docs describe "computationally expensive challenges" but do not state whether every BFM action is a Challenge Page carrying `cf-mitigated`, nor whether BFM ever returns a hard deny page instead.
- **Akamai `_abck` internals and true trust lifetime.** The cookie byte layout, the `~-1~`/`~0~` state markers, the sensor endpoint, and the "minutes to hours" trust window all come from reverse-engineering, not Akamai. Akamai's Bot Manager reference documentation is customer-login-gated.
- **Akamai `bm_sz`/`ak_bmsc` universality.** Both are configuration-dependent; not every protected property runs the pixel challenge, and the 4-hour/2-hour figures come from observed-cookie catalogues, not Akamai.
- **DataDome body markers.** `captcha-delivery.com` is documented as the response-page host, but the exact HTML/JSON envelope of a DataDome block is customer-customisable (logo, colours, domain templates), so body strings are not a stable contract. The `datadome` cookie's one-year expiry is storage life, not a documented verdict TTL.
- **HUMAN `_px3` validity.** 5.5 minutes is the browser cookie's expiry per HUMAN's table. Whether the server-side risk verdict expires on the same clock, and whether the token is cryptographically bound to IP and User-Agent, are practitioner claims without vendor confirmation.
- **Kasada token lifetimes.** The ~30-minute CT window, the single-use CD rule, and the few-second timestamp freshness are all practitioner estimates. Kasada publishes no token or endpoint documentation, and its own site describes mechanisms only at marketing altitude.
- **Imperva block status.** The 200-OK, 83-character incident-ID page is confirmed by one detailed field report; other reports describe 403 pages and larger branded block pages. The split by property, path, and bot score is unknown, and `X-Iinfo`'s four-segment format is undocumented.
- **AWS WAF block body.** Only the status (403) and the challenge/CAPTCHA headers and codes are documented. The default body is described as "Forbidden" by an AWS support article but is not a formal contract, and Bot Control's individual rule logic is deliberately withheld by AWS.
- **Fastly challenge cookie lifetime.** Fastly documents the cookie names and FQDN scope but not how long `_fs_ch_cp_` remains valid, nor what the body of a 406 block looks like (the agent response code is separate from the edge's HTTP response, which Fastly says may differ).
- **No vendor publishes a machine-readable block signature.** Every identification in this report is a combination of status, headers, cookie names and body markers, and several vendors deliberately customise or randomise the visible layer. Single-signal detection will produce both false positives and false negatives.
- **Source-quality caveat.** Cloudflare, AWS and Fastly claims rest on vendor documentation. Akamai, DataDome's page bodies, HUMAN's scoring, Kasada and Imperva rely partly or wholly on dated practitioner write-ups and GitHub field reports; those claims are marked medium or low and should be treated as observed behaviour, not contractual behaviour.
