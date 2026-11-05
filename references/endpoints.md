# Vendor endpoints, v1

Exact request and response shapes for every Sluice backend. Verified against the
installed Hermes providers (v0.22.6) and the working web-search-plus overlay modules on
this machine, not from memory. Where a vendor has no documented REST extract endpoint, the
table says so and the ladder falls through instead of guessing.

Conventions used by every adapter:

- JSON over HTTPS through `httpx`, `timeout=10` for search, `30` for fetch and scrape.
- Search rows are normalized to `{url, title, description, position}`, with `date`,
  `source` and `score` kept when the vendor supplies them. The router adds `backend` and
  `tier`.
- Extract items are `{url, title, content}`; a per-URL failure is `{url, error}` and never
  fails the batch.
- A missing key is a configuration error, not a network error: return a failure envelope
  naming the env var and the signup URL.
- HTTP 402, and any 400/403/429 whose body mentions credits or quota, is a quota failure
  with `out_of_credit=True` semantics: no retry, open the circuit for an hour.
- 429 without credit wording is a rate limit: honour `Retry-After`, retry once.

## Search-only vs extract-capable

| Vendor | Search | Extract | Notes |
|---|---|---|---|
| searxng | yes | no | self-hosted; extraction is the ladder's job |
| ddgs | yes | no | local package, child process |
| exa | yes | yes | keyless MCP and keyed REST |
| parallel | yes | keyed only via MCP fetch | REST extract not used in v1 |
| firecrawl | yes | yes | keyless and keyed |
| tavily | yes | yes | keyless and keyed |
| keenable | yes | yes | public and keyed |
| brave | yes | no | search only |
| perplexity | yes | yes | snippets through `/sdk/content/snippets` |
| xai | yes | no | LLM-mediated, fragile |
| deepseek | yes | yes | paid, 128-char query cap |
| tinyfish | yes | no | paid, source-only results |

## searxng

- Base: `SEARXNG_URL`, no key. Requires `search.formats: [html, json]` in `settings.yml`.
- `GET {base}/search` params `{"q": query, "format": "json", "pageno": 1}`,
  `Accept: application/json`, timeout 15.
- Response: `results[]` with `url`, `title`, `content`, `engine`, `score`, optional `publishedDate`.
- A JSON parse failure usually means the format is not enabled; say so in the error.

## ddgs

- No key, no HTTP. `from ddgs import DDGS`; `DDGS(timeout=10).text(query, max_results=n)`.
- Fields: `title`, `href` or `url`, `body` or `snippet`. Map `timelimit` from freshness:
  day `d`, week `w`, month `m`, year `y`.
- Runs in a disposable child process with a hard kill and a 12 s wall clock. Native code
  can hold the GIL; a thread timeout may never fire.

## exa

Keyless (MCP): `POST https://mcp.exa.ai/mcp`, header
`Accept: application/json, text/event-stream`.

- JSON-RPC body `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"web_search_exa","arguments":{"query":q,"numResults":n}}}`.
- Fetch tool `web_fetch_exa`, arguments `{"urls":[u]}`.
- Response is a JSON envelope or SSE lines starting `data: `. Take the first
  `result.content[].text`; raise on `error` or `result.isError`.
- Search text is `---`-separated blocks with `Title:`, `URL:`, `Published:`, `Author:`,
  `Highlights:` lines. Normalize CRLF before splitting. `N/A` is a sentinel, not content.

Keyed: `EXA_API_KEY`, header `x-api-key`.

- Search `POST https://api.exa.ai/search`
  `{"query": q, "numResults": n, "type": "auto", "contents": {"highlights": true}}`.
  Response `results[]` with `url`, `title`, `highlights[]`, optional `publishedDate`.
- Extract `POST https://api.exa.ai/contents` `{"urls": [...], "text": true}`.
  Response `results[]` with `url`, `title`, `text`.

## parallel

Keyless (MCP): `POST https://search.parallel.ai/mcp`, same JSON-RPC envelope.

- Search tool `web_search`, arguments
  `{"objective": q, "search_queries": [q], "session_id": <random per process>}`.
- Fetch tool `web_fetch`, arguments `{"urls": [...], "objective": "Full page content", "session_id": ...}`.
- The text payload is JSON with `results[]` (`url`, `title`, `excerpts[]`); fetch results
  use `full_content` or `content` or `excerpts`, plus `errors[]`.
- Do not send the optional analytics `model_name` field.

Keyed: `PARALLEL_API_KEY`, header `x-api-key`.

- Search `POST https://api.parallel.ai/v1/search`
  `{"objective": q, "search_queries": [q], "max_results": n, "mode": "fast"}`.
  Response `results[]` with `url`, `title`, `excerpts[]`, `publish_date`.
- v1 does not call a keyed REST extract; use the keyless MCP fetch tool when a Parallel
  key is set, or let the ladder fall through.

## firecrawl

Keyless: no `Authorization` header at all.

- Search `POST https://api.firecrawl.dev/v2/search` `{"query": q, "limit": n}`.
  Results under `data.web` (occasionally `data` as a list, or `results`).
- Scrape `POST https://api.firecrawl.dev/v2/scrape` `{"url": u, "formats": ["markdown"]}`.
  Content is `data.markdown`, title is `data.metadata.title`.

Keyed: `FIRECRAWL_API_KEY`, `Authorization: Bearer <key>`, same paths and bodies.

## tavily

Headers always include `X-Client-Name: hermes-agent`.

- Keyless: `X-Tavily-Access-Mode: keyless`, no bearer token.
- Keyed: `TAVILY_API_KEY`, `Authorization: Bearer <key>`.
- Base `https://api.tavily.com` (override `TAVILY_BASE_URL`).
- Search `POST /search` `{"query": q, "max_results": n, "include_raw_content": false, "include_images": false}`.
  Results `results[]` with `url`, `title`, `content`, `score`.
- Extract `POST /extract` `{"urls": [...], "include_images": false}`.
  Results `results[]` with `url`, `title`, `raw_content` or `content`; failures in
  `failed_results[]` and `failed_urls[]`.

## keenable

Headers always include `X-Keenable-Title: hermes-agent` (mandatory).

- Public: no bearer. Keyed: `KEENABLE_API_KEY`, `Authorization: Bearer <key>`.
- Search `POST https://api.keenable.ai/v1/search` (public path `/v1/search/public`)
  `{"query": q, "max_results": n}`.
- Fetch `GET https://api.keenable.ai/v1/fetch` (public path `/v1/fetch/public`),
  params `{"url": u}`.

## brave

- `GET https://api.search.brave.com/res/v1/web/search`
  params `{"q": q, "count": min(n, 20)}` (the API caps at 20).
- Headers `X-Subscription-Token: <BRAVE_API_KEY>`, `Accept: application/json`.
- Response `web.results[]` with `title`, `url`, `description`, optional `age`.
- Two different keys exist in the wild: `BRAVE_API_KEY` (this adapter) and
  `BRAVE_SEARCH_API_KEY` (Hermes' free tier). They are not interchangeable.
- `422 SUBSCRIPTION_TOKEN_INVALID` means a bad key; `402 USAGE_LIMIT_EXCEEDED` means the
  free allowance is spent. Say which, not "query too long".
- Search only.

## perplexity

- Key `PERPLEXITY_API_KEY`, `Authorization: Bearer <key>`, base `https://api.perplexity.ai`
  (override `PERPLEXITY_BASE_URL`).
- Search `POST /search` `{"query": q, "max_results": n, "search_context_size": "low"}`.
  Response `results[]` with `title`, `url`, `snippet`.
- Extract `POST /sdk/content/snippets`
  `{"query": <words from the URLs>, "urls": [...], "max_tokens": ..., "max_tokens_per_page": ...}`.
  Response `results[]` with `url`, `text`.

## xai

- Key through the existing xAI credentials, `Authorization: Bearer <key>`, base
  `https://api.x.ai/v1` (override via the credential record).
- `POST /responses` with
  `{"model": "grok-build-0.1", "input": [{"role": "user", "content": <prompt>}], "tools": [{"type": "web_search"}], "include": ["no_inline_citations"]}`.
- The prompt asks for `{"results": [{"title", "url", "description"}]}` JSON.
- Parse the output text for that JSON first; fall back to `url_citation` annotations, then
  to `citations`. This is LLM-mediated search: keep it last in its tier and expect noise.
- Search only.

## deepseek

- Key `DEEPSEEK_API_KEY`, `Authorization: Bearer <key>`, base `https://api.deepseek.com`.
- Search `POST /v1/tools/search`
  `{"query": q, "count": n, "ori_question": q, "localization": "", "need_content": false}`.
  Response rows: `rerank_score` (0 to 3), `title`, `url`, `snippet`, `summary`, `content`,
  `site_name`, `site_icon`, `date` (RFC 3339), `authority` (-1 to 4).
- Query is 1 to 128 Unicode characters. Clip on a word boundary and report the truncation
  in metadata. `site:`, `-site:`, quoted phrases, `lang:`, `after:`, `before:` are
  forwarded intact.
- `count` 1 to 50, default 10. `need_content=false` leaves `content` empty; use `summary`.
- Extract `POST /v1/tools/fetch` `{"url": u}`, one URL per request, returns
  `{"title", "content"}`.
- Billing: $0.75 per 1,000 successful search requests. HTTP 402 is insufficient balance;
  404 is no usable content; 403 is a policy refusal. QPS 100 search, 25 fetch.

## tinyfish

- Key `TINYFISH_API_KEY`, header `X-API-Key: <key>`, `Accept: application/json`.
- `GET https://api.search.tinyfish.ai/` with params `query`, `domain_type=web`, and
  `max_results` when supported; source-only results.
- Response `results[]` with `url`, `title`, `snippet`, optional `date`, `site_name`,
  `publisher`, `position`; plus `total_results` and `page` in metadata.
- `401 {"error_code":"INVALID_API_KEY"}` means a bad key. Search only.

## Failure message contract

Every adapter returns, on failure, an error string a user can act on:

- missing key: `"<VENDOR>_API_KEY is not set. Get a key at <signup url>"`
- quota: `"<Vendor> quota is exhausted; skipped for up to an hour"`
- rate limit: `"<Vendor> rate-limited; retrying after <n>s"`
- policy: `"robots.txt disallows <path> for Sluice (<rule>)"`

No adapter raises across the provider boundary.
