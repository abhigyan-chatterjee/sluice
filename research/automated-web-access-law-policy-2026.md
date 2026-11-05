# Automated web access: robots.txt, AI crawlers, terms of service, and the case law

Compiled 5 November 2026. Each claim reports what the cited source says; URLs were checked on that date. Confidence reflects source quality and how directly the source supports the claim, not whether the underlying law is settled.

---

## 1. robots.txt: what the standard actually says

**1.** RFC 9309 (September 2022) is the IETF Standards-Track specification of the Robots Exclusion Protocol, formalising Martijn Koster's 1994 convention and adding definition language, error handling and caching rules.
Source: https://www.rfc-editor.org/rfc/rfc9309.html
Confidence: high
Note: Standards-Track status matters — but see claim 2 for what the standard itself disclaims.

**2.** The RFC states directly that robots.txt rules "are not a form of access authorization."
Source: https://www.rfc-editor.org/rfc/rfc9309.html (§1)
Confidence: high
Note: The single most load-bearing sentence for any automated client: the protocol disclaims the inference "allowed by robots.txt = authorised to access."

**3.** The same document says the protocol "is not a substitute for valid content security measures," warns that listing paths in robots.txt makes them publicly discoverable, and directs operators who want real control to HTTP authentication.
Source: https://www.rfc-editor.org/rfc/rfc9309.html (§3)
Confidence: high
Note: The standard's own security guidance is "use auth," not "use robots.txt."

**4.** Error handling is asymmetric: a 4xx response means the crawler MAY access any resource; a 5xx means the file is "undefined" and the crawler MUST assume complete disallow; a cached copy SHOULD NOT be used for more than 24 hours.
Source: https://www.rfc-editor.org/rfc/rfc9309.html (§§2.3.1.3–2.3.1.4, 2.4)
Confidence: high
Note: A missing robots.txt and an unreachable one mean opposite things.

**5.** Crawl-delay is not part of RFC 9309. Some operators honour it as a non-standard extension (Anthropic documents `Crawl-delay`), others explicitly do not (Apple: "Applebot does not follow crawl-delay").
Source: https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler ; https://support.apple.com/en-us/119829
Confidence: high
Note: Do not assume a crawl-delay is honoured; 429 with Retry-After is the standardised signal.

**6.** A federal appellate opinion describes compliance as voluntary: "Adherence to the rules in a robots.txt file is voluntary; malicious bots may deliberately choose not to honor robots.txt rules and may in turn be punished with a denial of access to the website in question."
Source: hiQ Labs v. LinkedIn, 31 F.4th 1180 (9th Cir. 2022), n.3 — https://cdn.ca9.uscourts.gov/datastore/opinions/2022/04/18/17-16783.pdf
Confidence: high
Note: Judicial confirmation that robots.txt is a request, with denial of access as the remedy — not a legal entitlement.

## 2. X-Robots-Tag

**7.** X-Robots-Tag is an HTTP response header that can carry any rule available to a robots meta tag, and is the recommended mechanism for controlling indexing of non-HTML resources such as PDFs, images and video, where a meta tag cannot be placed.
Source: Google Search Central, https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag
Confidence: high
Note: Per-URL and attached to the response, so it reaches resources a path rule in robots.txt would miss.

**8.** No IETF RFC defines X-Robots-Tag. Google's documentation is the de facto reference, and it cautions that these rules "may not be treated the same by all other search engines."
Source: https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag
Confidence: medium-high
Note: Vendor documentation, not a standard — unlike robots.txt, there is no normative text and no conformance requirement.

**9.** Google's `nosnippet` rule now also "prevent[s] the content from being used as a direct input for AI Overviews and AI Mode," so a snippet-control directive doubles as an AI-input control; where rules conflict, the more restrictive applies.
Source: https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag
Confidence: high
Note: Indexing directives are being quietly repurposed as AI-use directives.

**10.** Apple similarly lets publishers opt out of content being used in "broad world knowledge answers" by applying `nosnippet`, separately from disallowing Applebot-Extended for training.
Source: https://support.apple.com/en-us/119829
Confidence: high
Note: Two distinct opt-outs — model training versus answer grounding — on the same page.

## 3. The 2026 AI crawler landscape

**11.** Google-Extended is a standalone robots.txt product token with no separate HTTP user-agent string; it controls whether crawled content may be used to train Gemini models and for grounding, and Google states it "does not impact a site's inclusion in Google Search nor is it used as a ranking signal."
Source: https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers (page last updated 2026-10-30)
Confidence: high
Note: With no distinct UA, Google-Extended cannot be enforced at the network layer — only honoured.

**12.** OpenAI separates purposes into distinct agents: GPTBot (training), OAI-SearchBot (search visibility), OAI-AdsBot (ad landing-page validation) and ChatGPT-User (user-triggered fetches); each robots.txt setting is independent.
Source: https://developers.openai.com/api/docs/bots
Confidence: high
Note: The clearest vendor-side example of "separate bots for separate activities."

**13.** OpenAI says of ChatGPT-User: "Because these actions are initiated by a user, robots.txt rules may not apply."
Source: https://developers.openai.com/api/docs/bots
Confidence: high
Note: A vendor asserting that user-directed agent fetches sit outside the crawler bargain — directly relevant to any agent plugin.

**14.** Anthropic runs three robots — ClaudeBot (training), Claude-User (user-initiated retrieval), Claude-SearchBot (search indexing) — and states that its bots "respect anti-circumvention technologies (e.g., we will not attempt to bypass CAPTCHAs for the sites we crawl.)"
Source: https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler
Confidence: high
Note: A major lab's published line: honouring robots.txt includes declining to solve bot challenges.

**15.** Since 30 October 2026 Anthropic also honours a machine-readable "no AI training" preference based on the IETF AIPREF work, with a rule naming ClaudeBot taking priority over the general signal.
Source: same
Confidence: high
Note: First large-vendor adoption of the AIPREF-style signal, ahead of the standard's completion.

**16.** CCBot is Common Crawl's crawler; `User-agent: CCBot / Disallow: /` stops future crawling, and Common Crawl notes this removes content from the corpus downstream model builders use — not from models already trained.
Source: https://commoncrawl.org/ccbot ; https://commoncrawl.org/faq
Confidence: high
Note: Opt-out is prospective only; the opposite is a common misconception.

**17.** Apple separates search crawling (Applebot) from foundation-model training (Applebot-Extended); disallowing Applebot-Extended leaves search visibility intact.
Source: https://support.apple.com/en-us/119829
Confidence: high
Note: Same pattern as Google-Extended — the opt-out token is separate from the crawler.

**18.** Cloudflare reported that by June 2026 more than 50% of traffic on its network was non-human, that 52% of crawler requests were for AI training (up from 22% in spring 2025), and that mixed-use crawlers accounted for over 36% of activity.
Source: https://blog.cloudflare.com/agentic-internet-bot-report (1 July 2026)
Confidence: medium-high
Note: Vendor-network data, not a census — the direction and the mixed-use share matter more than the absolute figure.

## 4. Cloudflare: defaults, pay-per-crawl, and what it calls legitimate

**19.** On 3 July 2024 Cloudflare shipped a one-click "AI Scrapers and Crawlers" block for all plans including free, and noted that robots.txt blocks depend on the bot honouring them while "user agents are trivial for bot operators to change."
Source: https://blog.cloudflare.com/declaring-your-aindependence-block-ai-bots-scrapers-and-crawlers-with-a-single-click/
Confidence: high
Note: Edge enforcement exists precisely because robots.txt is unenforceable.

**20.** On 1 July 2025 ("Content Independence Day") Cloudflare changed the default for new domains so that AI training crawlers are blocked unless the owner opts in, and launched pay-per-crawl in private beta.
Source: https://blog.cloudflare.com/content-independence-day-no-ai-crawl-without-compensation/ ; https://blog.cloudflare.com/agentic-internet-bot-report
Confidence: high
Note: Default flipped from opt-in to opt-out for new zones — a business policy, not a legal rule.

**21.** Pay-per-crawl uses HTTP 402 with a `crawler-price` header; a crawler may reply with `crawler-exact-price` or `crawler-max-price`, and a successful paid fetch returns 200 with `crawler-charged`. Crawler identity is established with Web Bot Auth signatures, and Cloudflare acts as Merchant of Record.
Source: https://blog.cloudflare.com/introducing-pay-per-crawl/ (1 July 2025)
Confidence: high
Note: A price offer layered on HTTP, not an access-control mechanism.

**22.** Cloudflare states that a publisher choosing "charge" for a crawler with no billing relationship is "the functional equivalent of a network level block (an HTTP 403 Forbidden response where no content is returned)."
Source: same
Confidence: high
Note: In this design the paywall degrades to a refusal — the price is a commercial layer in front of a block.

**23.** On 1 July 2026 Cloudflare replaced "AI bot" with a behaviour taxonomy (Search, Agent, Training, plus Transact, Data Collection, Security Testing and others), and announced that from 15 September 2026 new domains default to blocking Training and Agent on pages that display ads while leaving Search allowed; multi-purpose crawlers are judged on all their behaviours, so blocking Training also blocks Googlebot, Applebot and Bingbot for those customers.
Source: https://blog.cloudflare.com/content-independence-day-ai-options
Confidence: high
Note: The most consequential 2026 shift: blocking "training" now catches search crawlers that also train.

**24.** Cloudflare's current documentation page states that for new domains "Search, Agents, and Training use Allow (do not block) by default," which conflicts with the July 2026 announcement of ad-page blocking defaults.
Source: https://developers.cloudflare.com/bots/additional-configurations/block-ai-bots/
Confidence: medium
Note: Either the docs are stale or the rollout changed; treat any specific default as zone-dependent and verify per site.

**25.** Cloudflare's published test for a good crawler: be transparent (unique user agent, declared IP ranges or Web Bot Auth, contact information); be a well-behaved netizen (no flooding, no stealth); serve a clear purpose; separate bots for separate activities; and "follow the rules" — check robots.txt, stay within rate limits, never bypass security protections.
Source: https://blog.cloudflare.com/perplexity-is-using-stealth-undeclared-crawlers-to-evade-website-no-crawl-directives/ (4 August 2025)
Confidence: high
Note: The clearest vendor statement of the abusive/legitimate boundary, written as enforcement criteria.

**26.** Cloudflare de-listed Perplexity as a verified bot after observing it switch to a Chrome-impersonating user agent, rotate IPs and ASNs, and ignore or skip robots.txt once blocked.
Source: same
Confidence: high
Note: The abusive pattern is evasion after refusal — not the act of crawling.

**27.** Cloudflare defines "Verified" as two things: "1) you declare yourself honestly, and 2) you don't abuse the trust you've earned."
Source: https://blog.cloudflare.com/good-and-bad-agentic-behaviors/ (7 August 2026)
Confidence: high
Note: Identity plus conduct; either alone is insufficient.

**28.** Cloudflare has also shipped site-side measures that are ethically contested, including "AI Labyrinth" options to maze, summarise, or "poison" content served to unauthorised bots with deliberately fake data.
Source: same
Confidence: high
Note: Worth naming: the abuse line runs both ways, and serving falsified data to agents is a site-side choice with its own consequences.

## 5. Emerging "agent-friendly" standards

**29.** Cloudflare's Content Signals Policy (24 September 2025) adds a `Content-Signal:` line inside robots.txt with three signals — search, ai-input, ai-train — expressed as `search=yes, ai-train=no`, released under CC0, with text asserting that restrictions "ARE EXPRESS RESERVATIONS OF RIGHTS UNDER ARTICLE 4" of the EU DSM Directive.
Source: https://blog.cloudflare.com/content-signals-policy/
Confidence: high
Note: A preference vocabulary layered into comments, with a legal-rights claim attached.

**30.** Cloudflare is explicit that content signals "express preferences; they are not technical countermeasures against scraping," and recommends pairing them with WAF rules and bot management.
Source: same
Confidence: high
Note: Vendors do not claim these signals bind anyone.

**31.** The IETF AIPREF working group is standardising a vocabulary for AI usage preferences and how to attach it (including via RFC 9309 robots.txt and HTTP header fields); its charter places "technical enforcement of preferences," authentication and authorisation of crawlers, registries and auditing explicitly out of scope.
Source: https://datatracker.ietf.org/wg/aipref/about/
Confidence: high
Note: The standards body has deliberately separated expressing a preference from enforcing one.

**32.** As of 13 September 2026 the vocabulary is still draft-ietf-aipref-vocab-08, an active Internet-Draft whose own note says its contents "DO NOT REFLECT CONSENSUS of the Working Group either in whole or part."
Source: https://datatracker.ietf.org/doc/draft-ietf-aipref-vocab/
Confidence: high
Note: No published RFC yet; do not cite AIPREF as settled standard.

**33.** Google says it "intends to support the AIPREF shared machine-readable training preference signal (with `user-agent: __usage_ai-training`) in early 2027."
Source: https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers (updated 2026-10-30)
Confidence: high
Note: A dated, named commitment — the first concrete adoption timeline from a major crawler.

**34.** Web Bot Auth is an IETF draft family (Ed25519 keys, HTTP Message Signatures, a key directory at `/.well-known/http-message-signatures-directory`) that Cloudflare uses to verify bots and, since August 2025, "signed agents" acting for an end user rather than a single company; the first cohort included ChatGPT agent, Goose, Browserbase and Anchor Browser.
Source: https://developers.cloudflare.com/bots/reference/bot-verification/web-bot-auth/ ; https://blog.cloudflare.com/signed-agents
Confidence: high
Note: This, not robots.txt, is the emerging allowlist: cryptographic identity with a registration step.

**35.** RSL (Really Simple Licensing), launched 10 September 2025 with Reddit, Yahoo, Ziff Davis, Fastly and others, is an open standard for declaring how AI systems may access, use and license content — a licensing layer rather than an access-control one.
Source: https://rslstandard.org/press/rsl-standard
Confidence: medium
Note: Industry consortium, not IETF; independent adoption is unclear.

## 6. Terms of service versus automated access

**36.** A site's terms and the CFAA are separate questions. hiQ won the CFAA argument and still lost on contract: in November 2022 the Northern District of California granted LinkedIn summary judgment on breach of its user agreement for automated scraping and for creating fake profiles via crowdsourced workers, holding that hiQ had expressly agreed to the terms when it created its account.
Source: https://www.zwillgen.com/alternative-data/hiq-v-linkedin-wrapped-up-web-scraping-lessons-learned/
Confidence: high
Note: The case widely described as legalising scraping ended in a contract loss.

**37.** The hiQ litigation closed in December 2022 with a consent judgment: permanent injunction against scraping, deletion of source code, data and algorithms, and $500,000 in damages to LinkedIn.
Source: same
Confidence: high
Note: hiQ's CFAA wins were preliminary-injunction holdings; the final outcome was a contract loss.

**38.** In Meta v. Bright Data (N.D. Cal., 23 January 2024) the court read Meta's terms as governing "your use" of its products and held that "Bright Data did not 'use' Facebook and Instagram when it engaged in public logged-off scraping" — so no breach for logged-off scraping of public data. It also rejected a survival clause.
Source: https://www.fbm.com/publications/major-decision-affects-law-of-scraping-and-online-data-collection-meta-platforms-v-bright-data/
Confidence: high
Note: Turns on the wording of the terms and on whether the visitor has an account — not a general licence to ignore terms.

**39.** Meta then dismissed its remaining claim and waived its right to appeal the summary judgment, so Meta v. Bright Data is a district-court ruling with no appellate precedent behind it.
Source: https://techcrunch.com/2024/02/26/meta-drops-lawsuit-against-web-scraping-firm-bright-data-that-sold-millions-of-instagram-records/
Confidence: high
Note: Frequently cited as if binding appellate law; it is not.

**40.** In X Corp. v. Bright Data (N.D. Cal., 9 May 2024) the court held that the Copyright Act preempted X's state-law claims, including breach of contract, insofar as they would give X control over copying of publicly available content.
Source: https://www.skadden.com/insights/publications/2024/05/district-court-adopts-broad-view
Confidence: medium-high
Note: A second district court moving the same way: contract cannot do work copyright reserves.

**41.** Southwest Airlines v. Kiwi.com (N.D. Tex., 30 September 2021) granted a preliminary injunction on a breach-of-contract theory for scraping fare data, finding assent because Kiwi had bought tickets on the site and knew of the terms — even though hiQ had recently cast doubt on CFAA liability for public data.
Source: https://www.proskauer.com/blog/southwest-airlines-wins-injunction-barring-travel-site-from-scraping
Confidence: high
Note: Assent can be established by conduct, not only by clicking "I agree."

**42.** Register.com v. Verio (2d Cir. 2004) held that a party who repeatedly accesses a site with knowledge of posted terms can be bound by them even without clickwrap assent.
Source: https://law.justia.com/cases/federal/appellate-courts/F3/356/393/539823/
Confidence: high
Note: The browsewrap foundation for "you knew the terms and kept going."

**43.** In Ziff Davis v. OpenAI (S.D.N.Y., 15 December 2025) the court held that robots.txt directives are not a "technological measure that effectively controls access" under DMCA §1201: they "do not 'effectively control' access ... any more than a sign requesting that visitors 'keep off the grass' effectively controls access to a lawn," and disregarding them "is not 'circumvention'." Leave to amend was denied on 18 December.
Source: https://blog.ericgoldman.org/archives/2025/12/are-robots-txt-instructions-legally-binding-ziff-davis-v-openai.htm (quoting 2025 WL 3635559) ; https://www.courthousenews.com/judge-advances-digital-publisher-ziff-davis-chatgpt-copyright-infringement-claims/
Confidence: high
Note: The first direct holding on robots.txt's legal force — and it cuts against treating robots.txt as a lock.

**44.** Reddit's suit against Anthropic over scraping survived early challenges: the N.D. Cal. remanded it to state court on 30 March 2026, and a San Francisco judge allowed contract, interference-with-contract and UCL claims to proceed.
Source: https://www.loeb.com/en/insights/publications/2026/04/reddit-inc-v-anthropic-pbc ; https://mashable.com/tech/judge-allows-most-of-reddit-lawsuit-against-anthropic-proceed
Confidence: medium
Note: Live 2026 litigation at the pleadings stage; the contract theory is the one doing the work.

**45.** By contrast, in Reddit v. SerpApi (S.D.N.Y., 31 July 2026) the court declined to dismiss DMCA §1201(a)(1) claims against SerpApi and Perplexity for bypassing Google's SearchGuard challenge to harvest Reddit content, while dismissing the §1201(b) trafficking claim against SerpApi and the unjust-enrichment and unfair-competition claims.
Source: https://www.mlex.com/mlex/articles/2508237/us-judge-allows-reddit-anti-circumvention-claims-vs-perplexity-ai-serpapi ; docket https://www.courtlistener.com/docket/71720563/reddit-inc-v-serpapi-llc/
Confidence: medium
Note: The contrast with Ziff Davis is factual — Reddit pleaded specific licence terms obliging it to guard against unauthorised access.

## 7. United States: the CFAA after Van Buren

**46.** Van Buren v. United States (3 June 2021) adopted a "gates-up-or-down inquiry — one either can or cannot access a computer system, and one either can or cannot access certain areas within the system," rejecting purpose-based "exceeds authorized access" liability.
Source: https://www.law.cornell.edu/supremecourt/text/19-783
Confidence: high
Note: The gate is technical, not contractual.

**47.** hiQ Labs v. LinkedIn (9th Cir., 18 April 2022) held on remand that Van Buren "reinforces" the conclusion that scraping public profiles likely is not access "without authorization," because a computer hosting public webpages "has erected no gates to lift or lower in the first place."
Source: https://cdn.ca9.uscourts.gov/datastore/opinions/2022/04/18/17-16783.pdf ; https://www.eff.org/deeplinks/2022/04/scraping-public-websites-still-isnt-crime-court-appeals-declares
Confidence: high
Note: Preliminary-injunction posture ("serious questions"), not a final merits judgment.

**48.** Facebook v. Power Ventures (9th Cir. 2016) held that a cease-and-desist letter plus an IP block revoked authorisation, so continued access could violate the CFAA — even where users had consented.
Source: https://www.mintz.com/insights-center/viewpoints/2016-07-19-facebook-v-vachani-user-authorization-can-be-revoked-service
Confidence: high
Note: The bridge between hiQ and liability: a technical block converts open access into gated access.

**49.** In Google v. SerpApi (N.D. Cal., 20 July 2026) the court dismissed Google's §1201 claims without leave to amend insofar as they concerned search results containing no copyrighted content, holding the DMCA does not apply where the work a measure controls is not protected by copyright; claims as to results containing licensed images were dismissed with leave to amend. The court also found Google had standing and had adequately alleged circumvention.
Source: https://caselaw.findlaw.com/court/us-dis-crt-n-d-cal/350669.html
Confidence: high
Note: A bot challenge is only a §1201 measure if it gates a copyrighted work — a narrow but real hook.

**50.** EFF's reading of hiQ: the decision "place[s] important limits" on the CFAA but "scrupulously avoid[s] defining the contours of what counts as 'authorization'," and EFF argues violations should require circumvention of effective technical barriers rather than breach of a written agreement.
Source: https://www.eff.org/deeplinks/2022/04/scraping-public-websites-still-isnt-crime-court-appeals-declares
Confidence: high (for what EFF argues)
Note: Advocacy position, clearly labelled as such.

## 8. European Union: data protection and database rights, in brief

**51.** Scraping publicly accessible personal data is still processing of personal data under the GDPR and needs an Article 6 basis. CNIL's position is that "data scraping is not prohibited per se, but must be analysed on a case-by-case basis," with legitimate interest available for building training datasets subject to conditions.
Source: https://www.cnil.fr/en/legal-basis-legitimate-interest-focus-sheet-measures-implement-case-data-collection-web-scraping (5 January 2026)
Confidence: high
Note: "Public" changes the risk calculus, not the legal category.

**52.** CNIL requires controllers to "exclude from the collection websites that clearly oppose the scraping of their content ... by using robots.txt exclusion protocols or implementing CAPTCHA," treating both as expressions of opposition a compliant scraper must honour.
Source: same
Confidence: high
Note: The strongest regulatory statement found that robots.txt is not mere etiquette: ignoring it is a data-protection compliance failure.

**53.** The EU sui generis database right (Directive 96/9/EC, Art. 7(1)) protects a maker who shows "qualitatively and/or quantitatively a substantial investment in either the obtaining, verification or presentation of the contents" against "extraction and/or re-utilization of the whole or of a substantial part" of the contents; the term is 15 years (Art. 10), renewed by substantial new investment.
Source: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:31996L0009
Confidence: high
Note: Protects investment in the collection, not the underlying facts.

**54.** In PR Aviation v. Ryanair (CJEU, 15 January 2015, C-30/14) the Court held that where a database is not protected by copyright or the sui generis right, the operator may nonetheless restrict its use by contract — website terms can supply protection the IP right does not.
Source: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A62014CJ0030
Confidence: high
Note: The EU counterpart to the US contract debate, and markedly more operator-friendly.

**55.** Article 4 of the DSM Directive (EU) 2019/790 permits text and data mining of lawfully accessible works unless rights holders reserve their rights "in a machine-readable manner"; recital 18 says that includes "metadata and terms and conditions of a website or a service."
Source: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32019L0790 (recital 18), applied in https://www.cnil.fr/en/legal-basis-legitimate-interest-focus-sheet-measures-implement-case-data-collection-web-scraping
Confidence: medium-high
Note: The legal reason robots.txt and site terms carry more weight in the EU than in the US.

## 9. Rate limit, bot challenge, paywall, auth wall: four different signals

**56.** 429 Too Many Requests means "the user has sent too many requests in a given amount of time ('rate limiting')"; the response SHOULD explain the condition and MAY include Retry-After. It says nothing about permission.
Source: RFC 6585 §4, https://www.rfc-editor.org/rfc/rfc6585
Confidence: high
Note: A pacing signal. Backing off is etiquette; continuing at pace burdens the server but is not unauthorised access.

**57.** 403 Forbidden means "the server understood the request but refused to process it," and "authenticating or re-authenticating makes no difference" — the refusal is policy, not identity.
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/403 (RFC 9110 §15.5.4)
Confidence: high
Note: The "you are not welcome" response; continuing past it is the clearest available signal of bad intent.

**58.** 401 Unauthorized is a challenge: the origin sends a WWW-Authenticate header with at least one challenge, and the client may retry with credentials.
Source: RFC 9110 §11.3, https://www.rfc-editor.org/rfc/rfc9110.html
Confidence: high
Note: 401 is the authentication wall and the "gate" in CFAA terms; 403 is not.

**59.** 402 Payment Required is a "nonstandard response status code reserved for future use," with no standard semantics; RFC 9110 §15.5.3 reserves the code but does not define its use.
Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/402
Confidence: high
Note: Any pay-per-crawl protocol built on 402 is a private convention, not a standard.

**60.** A bot challenge (JavaScript proof-of-work, CAPTCHA) is a technical measure a client can either solve or circumvent. Courts treat circumvention as actionable only where the measure controls access to a copyrighted work (Ziff Davis; Google v. SerpApi), while major vendors treat bypassing CAPTCHAs as out of bounds (Anthropic).
Source: claims 43, 49 and 14 above
Confidence: medium-high
Note: These categories are not a severity ladder but different things: pacing (429), refusal (403), identity (401), price (402), proof-of-human (challenge).

---

## Where the ethical and legal line sits

The law draws its line at a technical gate, not at a preference. On the US side, Van Buren and hiQ put public, unauthenticated pages largely outside the CFAA, and Ziff Davis v. OpenAI went further and held that robots.txt is not a lock at all — it is a "keep off the grass" sign. But that is only the criminal-statute question, and it is the least important one in practice. Contract, copyright, DMCA §1201, GDPR and the EU database right all survive hiQ intact, and hiQ itself ended in a permanent injunction and a $500,000 payment on a contract theory. The realistic exposure for an automated client is not prosecution; it is a breach-of-contract claim, an account termination, a §1201 claim where a challenge gated copyrighted material, or a data-protection finding in Europe, where robots.txt and site terms function as a machine-readable reservation of rights and, per CNIL, as an opt-out a compliant scraper is affirmatively required to respect.

The ethical line sits one step earlier, and it is narrower and clearer than the legal one: it is not "may I fetch this" but "have I been told not to, and did I route around the refusal." Every serious vendor statement converges on the same criteria. Cloudflare's good-crawler test is transparency of identity, a clear and separated purpose, rate discipline, honouring robots.txt, and never bypassing security protections; its definition of "verified" is that you declare yourself honestly and do not abuse the trust that earns. It de-listed Perplexity not for crawling but for switching to a Chrome-impersonating user agent and rotating IPs and ASNs after being blocked. Anthropic publishes that it will not solve CAPTCHAs. OpenAI publishes that user-initiated agent fetches may fall outside robots.txt — a claim that is a vendor's assertion, not settled law, and one a plugin should treat as a warning rather than a permission slip.

The four signals are routinely conflated and should not be. A 429 is a request to slow down; complying is professional courtesy, and the standardised response is to honour Retry-After. A 403 is a refusal: the server understood and said no, and re-authenticating will not change it. A 401 is an identity gate — the only one of the four that maps cleanly onto the CFAA's "gates up or down," and the one you must not walk around. A 402 is a price, which in Cloudflare's implementation collapses into a 403 when no billing relationship exists, so "paywall" and "block" are the same wall from the client's side. A bot challenge is a fifth thing: a proof-of-human that is legally a technological measure, whose circumvention is actionable only when it gates copyrighted material, and which the major labs have publicly undertaken not to defeat. For a well-behaved agent the operating rule is therefore short: identify honestly and persistently, read and honour robots.txt and any Content-Signal, respect 429 and Retry-After, stop at 401 and 403, never solve or outsource a CAPTCHA, never rotate identity to escape a block, prefer published APIs and feeds where they exist, and treat "the vendor says robots.txt doesn't apply to agents" as an argument to be tested, not a licence.

## What remains uncertain

- **Whether breaching robots.txt has any legal consequence outside §1201.** Ziff Davis settles the DMCA question and nothing else. Eric Goldman's assessment after that ruling was that "we still don't know if they are legally effective instructions," and no appellate court has decided whether robots.txt can constitute a CFAA "gate" or support trespass to chattels.
- **Whether Ziff Davis survives appeal or persuades other courts.** It is a single district-court decision, and its "keep off the grass" reasoning sits in tension with any future argument that a robots.txt directive is a condition of access.
- **Whether Cloudflare's September 2026 defaults are actually live.** The July 2026 announcement (Training and Agent blocked on ad pages for new domains) and the current documentation page (all three Allow by default) cannot both be current. Any claim about "what Cloudflare does by default" should be verified per zone.
- **Whether AIPREF becomes an RFC, and in what shape.** The vocabulary is at draft-08 with an explicit no-consensus notice, and the working group has placed enforcement, crawler authentication and auditing out of scope. Google's "early 2027" is a statement of intent, not a shipped feature.
- **Whether the "logged-off" reasoning generalises.** Meta v. Bright Data turned on the phrase "your use" and on Bright Data holding no account; whether a differently worded terms-of-service binds an anonymous visitor is untested, and X Corp. v. Bright Data suggests copyright preemption may cut the other way.
- **Whether user-directed agent fetches really fall outside robots.txt.** OpenAI asserts it for ChatGPT-User; Anthropic treats Claude-User as a separate, controllable robot. No court has ruled on the distinction, and it is the single most consequential open question for agent plugins.
- **How Reddit v. Anthropic and Reddit v. SerpApi end.** Both are at the pleadings stage; the §1201 claims that survived in SerpApi did so because Reddit pleaded specific licence terms, which may or may not be provable.
- **Whether Web Bot Auth becomes a de facto allowlist.** If verification becomes the price of admission, unregistered agents may be treated as bots by default — an access regime run by infrastructure providers rather than by courts or standards bodies.
- **Adoption of the preference layers.** Content-Signal is CC0 and easy to emit, RSL has publisher backing, and neither has an enforcement mechanism or published adoption data. Independent auditing of whether AI labs actually honour these signals is essentially absent; compliance is self-reported.
- **Coverage beyond the US and EU.** UK, Brazilian, Japanese, Korean and Indian positions on scraping, database rights and TDM exceptions were not examined here and may diverge materially.
