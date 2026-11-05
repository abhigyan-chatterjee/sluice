# Network-stack fingerprinting: why page fetches get blocked, and what tooling can actually reproduce

**Scope and method.** This section covers TLS, HTTP/2 and HTTP/3 client fingerprinting as a cause of bot blocking, plus the libraries that claim to reproduce a browser's network signature. Sources were fetched on 2026-11-05; no live fingerprint tests were run, so every claim is documentary. Vendor-authored sources are marked in the verification note. "Impersonation" here means protocol-level fidelity, not pretending to be a human: none of these tools supplies human behaviour, and fingerprint matching is only one input to a bot score.

**Bottom line for a page-fetching plugin.** A browser User-Agent without a matching TLS ClientHello, HTTP/2 SETTINGS/order and client-hint set is a mixed fingerprint, and the network layers are checked before headers (claims 1-4). Protocol-level impersonation is available in curl_cffi, tls-client and rnet/wreq; requests, httpx, aiohttp and Node fetch/undici are not drop-in browser substitutes (claims 31-41). None of them supplies JavaScript execution, TCP/IP shape or behaviour, so JS challenges and score-based blocks remain (claim 42). Treat every profile as perishable and verify it against an echo endpoint and the target (claims 43-44).

## 1. Why the network stack is the first filter

**1. The TLS ClientHello is sent in cleartext before any HTTP request, so a server can classify the client library before it reads a URL or a single header.**
Source: https://lwthiker.com/networks/2022/06/17/tls-fingerprinting.html — Confidence: high — Verification: The curl-impersonate author's protocol walkthrough (2022-06-17) lists the ClientHello fields and states this is the first message of the connection; the ordering is protocol-defined, not an inference.

**2. Cloudflare explicitly uses JA3/JA4 fingerprints to tell a browser from a Python script, and says they underpin its bot-management models.**
Source: https://blog.cloudflare.com/ja4-signals — Confidence: high — Verification: Direct statement in Cloudflare's post dated 2024-08-12; Cloudflare sells Bot Management, so this is a vendor describing its own product.

**3. Fingerprints alone are not sufficient: Cloudflare says they can be easily spoofed and combines them with inter-request JA4 Signals computed over the previous hour of global traffic.**
Source: https://blog.cloudflare.com/ja4-signals — Confidence: high — Verification: The 2024-08-12 post lists JA4 Signals such as browser_ratio_1h, reqs_quantile_1h and h2h3_ratio_1h; vendor source, but the architectural point (fingerprint plus behaviour) is explicit.

**4. Akamai's HTTP/2 fingerprint uses the SETTINGS frame, the WINDOW_UPDATE increment and PRIORITY frames; it identifies the HTTP/2 implementation and is not, by itself, enough entropy to track a specific user.**
Source: https://blackhat.com/docs/eu-17/materials/eu-17-Shuster-Passive-Fingerprinting-Of-HTTP2-Clients-wp.pdf — Confidence: high — Verification: Akamai's Black Hat EU 2017 white paper, based on more than 10 million HTTP/2 connections; vendor research and the constants are dated, but the method is the origin of the "Akamai fingerprint" format still used today.

## 2. TLS: JA3, JA4 and JA4H

**5. JA3 hashes the ordered lists of cipher suites, extensions and curves; Chrome's early-2023 randomization of TLS extension order made JA3 unreliable for identifying current Chrome.**
Source: https://blog.cloudflare.com/ja4-signals — Confidence: high — Verification: Cloudflare's 2024-08-12 post describes the Chromium change and three failure modes of JA3; FoxIO's Q&A links the corresponding chromestatus entry.

**6. JA4 is built from protocol (t/q/d), TLS version, SNI presence, cipher count, extension count, the first ALPN value, a truncated SHA-256 of sorted ciphers, and a truncated SHA-256 of sorted extensions (minus SNI and ALPN) plus signature algorithms in their original order; GREASE values are ignored.**
Source: https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md — Confidence: high — Verification: The FoxIO technical-details page gives the exact format and worked examples, including the t13d1516h2_8daaf6152771_e5627efa2ab1 example.

**7. Sorting makes JA4 resistant to Chrome's extension shuffle, but FoxIO still warns that fingerprints change roughly once a year as TLS libraries are updated.**
Source: https://github.com/FoxIO-LLC/ja4 — Confidence: high — Verification: FoxIO's Q&A says fingerprints "will change as application TLS libraries are updated, about once a year" and explains that sorting the extensions is what defeats the shuffle.

**8. JA4 encodes the first ALPN value in its a-section; "00" means no ALPN, which FoxIO says may indicate the client is not a web browser.**
Source: https://foxio.io/blog/ja4-network-fingerprinting — Confidence: high — Verification: FoxIO's launch post (2023-09-26) explains the ALPN field and explicitly notes that a lack of ALPN may indicate a non-browser, while h2 alone does not prove a browser.

**9. JA4 itself is BSD-3-Clause, but JA4H, JA4S and the rest of JA4+ are patent-pending under the FoxIO License 1.1, and monetized use requires an OEM license.**
Source: https://github.com/FoxIO-LLC/ja4 — Confidence: high — Verification: FoxIO's README states the split licence terms plainly; relevant if a commercial plugin embeds JA4H/JA4S rather than only JA4.

**10. JA4H fingerprints the HTTP client per request; FoxIO treats a missing Accept-Language as a clear indication that the client is not human-interactive.**
Source: https://foxio.io/blog/ja4-network-fingerprinting — Confidence: high — Verification: FoxIO's launch post describes JA4H_ab as the application fingerprint for a given HTTP method and makes the Accept-Language point directly; vendor source, and the public JA4H technical page is much thinner than the blog.

## 3. HTTP/2: SETTINGS, priority and header order

**11. HTTP/2 clients differ in which SETTINGS parameters they send, their order and values, the WINDOW_UPDATE increment, and the PRIORITY frames they send for streams that are never opened. Akamai's examples include Chrome 58 at 1:65536;3:1000;4:6291456|15663105|0, Firefox 53 at 1:65536;4:131072;5:16384|12517377|3:0:0:201,5:0:0:101,7:0:0:1,9:0:7:1,11:0:3:1, and a Go client at 2:0;4:4194304;6:10485760|1073741824|0.**
Source: https://blackhat.com/docs/eu-17/materials/eu-17-Shuster-Passive-Fingerprinting-Of-HTTP2-Clients-wp.pdf — Confidence: high — Verification: The 2017 white paper's sample fingerprints are reported observations from Akamai edge logs; treat the exact constants as historical, not as current browser values.

**12. Pseudo-header order is client-specific: Chrome sends :method, :authority, :scheme, :path; Firefox sends :method, :path, :authority, :scheme; Safari sends :method, :scheme, :path, :authority; Go sends :authority, :method, :path, :scheme.**
Source: https://blackhat.com/docs/eu-17/materials/eu-17-Shuster-Passive-Fingerprinting-Of-HTTP2-Clients-wp.pdf — Confidence: high — Verification: Akamai's table of pseudo-header order for Chrome 58, Firefox 53, Safari 10.1, curl 7.54 and Go-http-client 2.0; the specific versions are old, but the mechanism is unchanged.

**13. Akamai's published fingerprint format is SETTINGS|WINDOW_UPDATE|PRIORITY|PSEUDO_HEADER_ORDER; it does not include the order of ordinary headers.**
Source: https://blackhat.com/docs/eu-17/materials/eu-17-Shuster-Passive-Fingerprinting-Of-HTTP2-Clients-wp.pdf — Confidence: high — Verification: The paper defines the format as S[;]|WU|P[,]#|PS[,] and says pseudo-header order is appended as m/p/a/s; ordinary-header order is a separate signal, not part of this string.

**14. HTTP/2 requires field names to be converted to lowercase, so header-name case cannot distinguish clients on HTTP/2.**
Source: https://www.rfc-editor.org/rfc/rfc9113.html — Confidence: high — Verification: RFC 9113 (2022) Section 8.2 says "Field names MUST be converted to lowercase when constructing an HTTP/2 message"; this is a protocol requirement, not a vendor observation.

**15. HTTP/2 requires pseudo-header fields to appear before all regular fields but does not prescribe an order among the regular fields, so their order is observable and implementation-specific.**
Source: https://www.rfc-editor.org/rfc/rfc9113.html — Confidence: medium — Verification: RFC 9113 Section 8.3 mandates pseudo-headers first but contains no ordering rule for ordinary fields; the "therefore observable" half is an inference from the absence of a rule, and detectors do use it.

**16. HTTP/1.1 field names are case-insensitive, but both the casing and the line order are still observable on the wire; Go's net/textproto canonicalizes names to forms such as User-Agent, so library output can differ from a browser's.**
Source: https://www.rfc-editor.org/rfc/rfc9110.html — Confidence: medium — Verification: RFC 9110 Section 5.1 says field names are case-insensitive; Go's CanonicalMIMEHeaderKey documentation describes the canonicalization. Casing is a weak signal compared with order and header set, and HTTP/2 removes it entirely.

## 4. Header consistency: UA, client hints, language and fetch metadata

**17. Chrome's User-Agent is reduced: Chrome 101 froze minor/build/patch to 0.0.0, Chrome 107 froze desktop platform strings, and Chrome 113 completed the rollout; detailed version and OS data moved to User-Agent Client Hints.**
Source: https://www.chromium.org/updates/ua-reduction — Confidence: high — Verification: Chromium's UA-reduction page documents each phase and the post-reduction format; a "Chrome 152.0.1234.56" UA is therefore not a current Chrome UA on the affected platforms.

**18. Sec-CH-UA is a low-entropy client hint sent by default without the server sending Accept-CH; it may contain deliberately fake brands; high-entropy hints are gated by browser policy.**
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Sec-CH-UA — Confidence: high — Verification: MDN states the header is sent by default, that fake brands may appear in any position, and that high-entropy hints are gated; MDN's UA-CH API page describes the low/high-entropy split.

**19. A request that claims Chrome in the User-Agent but omits or contradicts Sec-CH-UA (brand, major version, platform) is internally inconsistent; matching HTTP headers alone is not enough.**
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Sec-CH-UA — Confidence: high — Verification: The consistency requirement follows directly from MDN's description of default low-entropy hints and brand/version semantics; how strictly a given site scores the mismatch is not public.

**20. Browsers set Accept-Language from their active UI language, add language-only fallback tags with q-values in Chrome and Safari, and may reduce to a single language in Safari (always) and Chrome incognito.**
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language — Confidence: high — Verification: MDN documents this behavior and gives the en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7 example.

**21. A missing Accept-Language is a documented bot signal, and a language list that contradicts the egress IP's locale is a common heuristic; no primary vendor document quantifying the locale-mismatch weight was found.**
Source: https://foxio.io/blog/ja4-network-fingerprinting — Confidence: medium — Verification: FoxIO states the missing-header point directly (2023-09-26); the IP/locale mismatch half is inference from content-negotiation semantics, not a published scoring rule.

**22. Locale/IP mismatch is a heuristic rather than a protocol violation, so it should be treated as a scoring feature, not a deterministic block.**
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language — Confidence: low — Verification: MDN explains how browsers choose Accept-Language but says nothing about server-side geo-consistency checks; no primary vendor source quantifying this was located.

**23. Sec-Fetch-* headers are browser-generated and forbidden to scripts, and Sec-Fetch-Site is sent only to potentially trustworthy URLs; a request that claims to be Chrome but lacks them is inconsistent.**
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Sec-Fetch-Site — Confidence: high — Verification: MDN marks Sec-Fetch-Site as a forbidden request header, describes the browser-generated values, and notes the trustworthy-URL restriction.

## 5. HTTP/3, ALPN and connection reuse

**24. HTTP/3 uses the ALPN token h3; if UDP is blocked, clients should fall back to TCP-based HTTP/2 or HTTP/1.1.**
Source: https://www.rfc-editor.org/rfc/rfc9114.html — Confidence: high — Verification: RFC 9114 (2022) says the token "h3" is used in ALPN and that connectivity problems blocking UDP should cause a fallback to TCP-based versions.

**25. JA4 has a QUIC variant (q) that fingerprints the TLS ClientHello carried inside QUIC; it does not hash the values of the QUIC transport parameters themselves.**
Source: https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md — Confidence: medium — Verification: The JA4 specification defines the q prefix for QUIC and describes reading the ClientHello; the transport-parameter limitation is a scope inference, since JA4's inputs are ClientHello fields only.

**26. HTTP/2 and HTTP/3 fingerprints are separate profiles: the curl-impersonate fork marks only some browser targets as having dedicated H3 fingerprints and explicitly says HTTP/3 support in the binary does not imply a matching browser H3 fingerprint.**
Source: https://github.com/lexiforest/curl-impersonate — Confidence: high — Verification: The fork README's H3 column and note 4 say exactly this; the project is the maintained fork of curl-impersonate and sells commercial support.

**27. tls-client's v1.16.0 release notes say only chrome_144, chrome_144_PSK, firefox_147, firefox_147_PSK and firefox_148 carry HTTP/3 settings; every other profile sends a minimal SETTINGS frame that does not match the browser it imitates, although TLS and HTTP/2 fingerprints are unaffected.**
Source: https://github.com/bogdanfinn/tls-client/releases — Confidence: high — Verification: The "Known Issues" section of the v1.16.0 release (2026-09-02) states this precisely; a concrete example of a vendor's broader "HTTP/3 fingerprinting" feature being profile-limited.

**28. Cloudflare says JA3/JA4 are calculated during the TLS handshake and are not calculated again on resumed TLS sessions, so fingerprints can be absent on reused connections.**
Source: https://developers.cloudflare.com/bots/concepts/ja3-ja4-fingerprint — Confidence: high — Verification: Cloudflare's JA3/JA4 docs (last updated 2026-05-06) list TLS Session Resumption as a reason the field can be empty; this is about visibility, not about the client changing its fingerprint.

**29. The HTTP/2 fingerprint is per connection: SETTINGS is the first frame after the client connection preface and is not re-sent per request, so connection reuse pins the fingerprint and a fresh connection exposes it again.**
Source: https://www.rfc-editor.org/rfc/rfc9113.html — Confidence: high — Verification: RFC 9113 Section 3.4 requires the client preface to be followed by a SETTINGS frame; Akamai's method reads that connection-level exchange. The reuse implication is architectural, not a separate vendor claim.

**30. ALPN is part of JA4; a client that offers h2 but speaks HTTP/1.1, or claims Chrome while offering no ALPN, is internally inconsistent at the TLS layer.**
Source: https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md — Confidence: medium — Verification: JA4's a-section records the first ALPN value, and RFC 9113 uses "h2" as the TLS ALPN identifier; the mismatch-detection use is an inference from those two facts, not a published detector rule.

## 6. Tooling: what can and cannot reproduce a browser

**31. curl-impersonate (original) patches curl to compile against BoringSSL or NSS, reproduces browser TLS extensions and HTTP/2 settings, and ships wrapper scripts with browser headers; its newest profiles are Chrome 116, Firefox 117 and Safari 15.5.**
Source: https://github.com/lwthiker/curl-impersonate — Confidence: high — Verification: The README lists the exact patches and the supported-browser table; "identical handshakes" is the maintainer's claim, not an independent test, and the profile list is stale relative to 2026 browsers.

**32. The lexiforest fork is the active line: curl-impersonate 2.3.0 on curl 8.23.0, profiles through Chrome 152, Safari 27 and Firefox 155, plus ECH, ZSTD, X25519MLKEM, extension-order permutation and some HTTP/3 fingerprints; commercial support is sold via impersonate.pro.**
Source: https://github.com/lexiforest/curl-impersonate — Confidence: high — Verification: The fork README (accessed 2026-11-05) documents each enhancement and the commercial-support link; vendor source.

**33. Profile data can be wrong even in purpose-built tools: issue #215 shows Safari targets reusing Chrome's SETTINGS and WINDOW_UPDATE, and the fork README still says the original Safari fingerprints are not correct.**
Source: https://github.com/lwthiker/curl-impersonate/issues/215 — Confidence: high — Verification: Issue #215 (opened 2023-12-27, still open) gives the wrong and expected Akamai strings; the fork README's note 2 links to it. This is the strongest single argument for verifying a profile against an echo endpoint before trusting it.

**34. curl_cffi exposes curl-impersonate to Python with a requests-like API, JA3/TLS and HTTP/2 impersonation, HTTP/3 since 0.11.4 (fingerprints and proxies since 0.15.0), and custom ja3/akamai strings; JavaScript challenges require a separate tool (brimp).**
Source: https://github.com/lexiforest/curl_cffi — Confidence: high — Verification: The README's feature table and notes give the version boundaries and the brimp pointer; vendor source, with a commercial fingerprint database at impersonate.pro.

**35. Python's common clients do not provide browser impersonation: requests and aiohttp have no HTTP/2, httpx has optional HTTP/2 but no fingerprint control, and curl_cffi's comparison table marks fingerprints unsupported for all three.**
Source: https://github.com/lexiforest/curl_cffi — Confidence: high — Verification: The README comparison table marks http/2 and fingerprints per client, and httpx's own docs say HTTP/2 is opt-in; the table is vendor-authored, but the HTTP/2 facts are independently documented.

**36. A 2022 measurement of Python requests on OpenSSL found 43 cipher suites versus Chrome's 16, no ALPS, no certificate compression and no GREASE; the author says cipher-suite control is essentially all Python exposes.**
Source: https://lwthiker.com/networks/2022/06/17/tls-fingerprinting.html — Confidence: high — Verification: The comparison table and text in the 2022-06-17 post are explicit; treat exact counts as version-specific, since OpenSSL and Chrome have both changed since.

**37. bogdanfinn/tls-client is a Go client built on uTLS/fhttp with browser profiles, explicit header ordering, HTTP/1.1, HTTP/2 and HTTP/3, plus Python, Node and C# bindings through a shared library.**
Source: https://github.com/bogdanfinn/tls-client — Confidence: high — Verification: The README lists the features and the language bindings; the shared-library/FFI path is what most non-Go integrations use.

**38. tls-client's release notes show recurring wire-level bugs: header order corrupted by a pooled sorter, a cached transport reused after ALPN changed on reconnect, and HTTP/3 SETTINGS missing for most profiles.**
Source: https://github.com/bogdanfinn/tls-client/releases — Confidence: high — Verification: These are in the v1.16.0 changelog and known-issues section (2026-09-02); they show that even a mature impersonation library regresses at the exact layers detectors inspect.

**39. rnet (now published as wreq-python) is a Rust/BoringSSL Python client with 100+ device profiles and fine-grained TLS/HTTP2 settings; its maintainers argue that string-based JA3/JA4/Akamai emulation is unreliable and model the underlying settings directly.**
Source: https://github.com/0x676e67/rnet — Confidence: high — Verification: The README states the design rationale and profile count; GitHub redirects 0x676e67/rnet to 0x676e67/wreq-python, so the old name is a redirect, not a separate live project. Vendor claim.

**40. Node's built-in fetch/undici has no browser-grade TLS impersonation: the open undici issue dates from 2023, and in 2025 a maintainer said 100% matching is impossible in Node because OpenSSL is not configurable enough, with Go-based clients the practical workaround.**
Source: https://github.com/nodejs/undici/issues/1983 — Confidence: high — Verification: Issue #1983 was created 2023-03-02 and remains open; the 2025-04-03 comment by HTTP Toolkit's maintainer states the OpenSSL limitation. This is a maintainer statement about Node, not a benchmark.

**41. Real-world case: Node 12-16 got an immediate 403 from Zalando's Akamai-fronted API even with browser headers; reordering the TLS cipher list changed the fingerprint and returned 200.**
Source: https://httptoolkit.com/blog/tls-fingerprinting-node-js — Confidence: high — Verification: HTTP Toolkit's December 2021 post reproduces the 403 and the cipher-reorder fix; single-site case study, but a clean demonstration that headers were not the blocking variable.

**42. None of these libraries reproduces the TCP/IP stack, JavaScript execution or inter-request behaviour; curl-impersonate's changes are explicitly TLS and HTTP/2, and Cloudflare's own scoring adds behavioural signals.**
Source: https://github.com/lwthiker/curl-impersonate — Confidence: medium — Verification: The curl-impersonate README scopes its patches to TLS extensions and HTTP/2 settings, and Cloudflare's JA4 Signals post describes behaviour beyond the handshake; "none" is a survey conclusion, not a formal proof about every library.

**43. Profiles are version snapshots: FoxIO says fingerprints change about yearly, and the fork's profile list is version-specific (Chrome 151/152, Safari 27, Firefox 153-155). Pinning without updating turns a browser fingerprint into a stale one.**
Source: https://github.com/FoxIO-LLC/ja4 — Confidence: high — Verification: FoxIO's Q&A gives the yearly-drift warning; the fork README's current profile table shows the version-specific targets. The two sources are independent of each other.

**44. Verification is practical: both curl-impersonate projects point users at fingerprint echo endpoints such as tls.browserleaks.com/json to compare JA3/JA4 and HTTP/2 output against the claimed browser.**
Source: https://github.com/lexiforest/curl-impersonate — Confidence: high — Verification: The fork README's basic-usage section suggests curl_firefox133 https://tls.browserleaks.com/json; the original README's tests/signatures directory is the same idea in test form.

## Tooling at a glance (summary of claims 31-42)

| Tool | Language / binding | What it can reproduce | What it cannot, or where it breaks |
|---|---|---|---|
| curl-impersonate (original) | C, libcurl | Browser TLS ClientHello and HTTP/2 SETTINGS/priority/header order; wrapper headers | No JavaScript or browser APIs; newest profiles are Chrome 116 / Firefox 117 / Safari 15.5; the maintainer's "identical" claim is not independently audited |
| curl-impersonate (lexiforest fork) | C, libcurl; Python via curl_cffi; Node via impers | Newer profiles through Chrome 152 / Safari 27 / Firefox 155, ECH, extension permutation, some HTTP/3 fingerprints | Only some profiles carry dedicated H3 fingerprints; commercial profiles and support are paid; no JavaScript |
| curl_cffi | Python | curl-impersonate's TLS/JA3 and HTTP/2 impersonation with a requests-like API; custom ja3/akamai strings; HTTP/3 | No JavaScript; custom string fingerprints still have to be tested; commercial fingerprint database |
| tls-client | Go; Python/Node/C# via shared library | uTLS-based browser profiles, explicit header order, HTTP/1.1, HTTP/2 and HTTP/3 | HTTP/3 SETTINGS only on five profiles in v1.16.0; a history of header-order and ALPN-cache bugs; no JavaScript |
| rnet / wreq | Python on Rust/BoringSSL | Fine-grained TLS and HTTP/2 settings, 100+ device profiles | No JavaScript; project itself argues that copying JA3/JA4/Akamai strings is unreliable |
| requests / httpx / aiohttp | Python | Ordinary HTTP; httpx can opt into HTTP/2 | No ClientHello or SETTINGS control, so the wire signature is Python/OpenSSL, not a browser; requests and aiohttp have no HTTP/2 |
| Node fetch / undici | Node | Ordinary HTTP/1.1 and HTTP/2 | No browser-grade TLS impersonation; OpenSSL does not expose enough knobs, per the undici issue; Go sidecars are the usual workaround |

## What remains uncertain

- **Detector weighting is unpublished.** Cloudflare publishes signals and Bot Score examples, but not the thresholds or the weight of JA4 versus HTTP/2 versus header consistency. Any claim that a specific mismatch "will" be blocked is a guess; the safe statement is that it is observable and scoreable.
- **How much regular-header order matters in HTTP/2.** Akamai's published format stops at pseudo-header order; commercial detectors may compare full header order, but no public primary specification says how heavily. The same applies to HTTP/1.1 header casing.
- **Current constants age fast.** The Akamai examples date from 2017 and the JA4 examples from 2023-2024. Chrome and Firefox fingerprint changes, A/B-tested profile variants (`chrome133a` in the fork) and Safari's profile corrections mean any hard-coded string in this report should be re-measured before use.
- **Whether TLS extension permutation is now required.** JA4 sorts extensions, so it does not care about Chrome's shuffle, but detectors using JA3, TS1 or exact-order matching do. The fork exposes a permutation switch; whether a given target requires it is target-specific.
- **QUIC transport parameters are underspecified here.** JA4's q variant reads the QUIC ClientHello; full QUIC fingerprinting also involves transport parameters and HTTP/3 SETTINGS, which are less standardised publicly and are only partially covered by the tools surveyed.
- **Locale/IP consistency is the weakest claim.** The browser behaviour is documented; the server-side weighting is not. Treat it as a soft signal, not a blocking rule.
- **No hands-on validation was performed.** Every claim comes from documentation, issues or vendor posts fetched on 2026-11-05. A plugin should validate candidate profiles against a live echo endpoint and against the actual target before treating a fingerprint as "browser-like".

## Sources

All URLs accessed 2026-11-05.

- TLS fingerprinting (lwthiker, 2022-06-17) — https://lwthiker.com/networks/2022/06/17/tls-fingerprinting.html
- Cloudflare JA4 signals (2024-08-12) — https://blog.cloudflare.com/ja4-signals
- Akamai: Passive Fingerprinting of HTTP/2 Clients (2017) — https://blackhat.com/docs/eu-17/materials/eu-17-Shuster-Passive-Fingerprinting-Of-HTTP2-Clients-wp.pdf
- JA4 Technical Details — https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4.md
- JA4+ Network Fingerprinting (FoxIO) — https://github.com/FoxIO-LLC/ja4
- FoxIO: JA4+ Network Fingerprinting (2023-09-26) — https://foxio.io/blog/ja4-network-fingerprinting
- RFC 9113: HTTP/2 — https://www.rfc-editor.org/rfc/rfc9113.html
- RFC 9110: HTTP Semantics — https://www.rfc-editor.org/rfc/rfc9110.html
- Chromium: User-Agent Reduction — https://www.chromium.org/updates/ua-reduction
- MDN: Sec-CH-UA — https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Sec-CH-UA
- MDN: Accept-Language — https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language
- MDN: Sec-Fetch-Site — https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Sec-Fetch-Site
- RFC 9114: HTTP/3 — https://www.rfc-editor.org/rfc/rfc9114.html
- curl-impersonate (lexiforest fork) — https://github.com/lexiforest/curl-impersonate
- tls-client releases — https://github.com/bogdanfinn/tls-client/releases
- Cloudflare JA3/JA4 fingerprint docs — https://developers.cloudflare.com/bots/concepts/ja3-ja4-fingerprint
- curl-impersonate (original) — https://github.com/lwthiker/curl-impersonate
- curl-impersonate issue #215 — https://github.com/lwthiker/curl-impersonate/issues/215
- curl_cffi — https://github.com/lexiforest/curl_cffi
- bogdanfinn/tls-client — https://github.com/bogdanfinn/tls-client
- rnet / wreq-python — https://github.com/0x676e67/rnet
- undici issue #1983 — https://github.com/nodejs/undici/issues/1983
- Fighting TLS fingerprinting with Node.js (2021-12) — https://httptoolkit.com/blog/tls-fingerprinting-node-js
