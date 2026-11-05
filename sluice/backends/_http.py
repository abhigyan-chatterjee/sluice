"""Shared HTTP and MCP plumbing for every vendor adapter.

Conventions fixed by ``references/endpoints.md:8-20``:

* JSON over HTTPS through ``httpx``, ``timeout=10`` for search and ``30`` for fetch/scrape.
* Search rows normalize to ``{url, title, description, position}`` plus ``date``,
  ``source`` and ``score`` when the vendor supplies them.
* Extract items are ``{url, title, content}``; a per-URL failure is ``{url, error}``.
* A missing key is a configuration error naming the env var and the signup URL.
* HTTP 402, and any 400/403/429 whose body mentions credits or quota, is a quota failure
  with ``out_of_credit=True``: no retry, and the router opens the circuit for an hour.
* 429 without credit wording honours ``Retry-After`` and retries once.
* No adapter raises across the provider boundary.

Every helper here returns a ``(value, failure)`` pair instead of raising, so an adapter
body is straight-line code with exactly one error path.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping

import httpx

#: references/endpoints.md:10.
DEFAULT_SEARCH_TIMEOUT = 10.0
DEFAULT_FETCH_TIMEOUT = 30.0

#: Honest, stable client id. The built-in providers send vendor-specific strings; Sluice
#: identifies itself the same way in the HTTP ladder and in API calls.
USER_AGENT = "Sluice/1.0 (+https://github.com/abhigyan-chatterjee/sluice)"

#: Signup / key-management page per credential, from the providers Hermes ships and each
#: vendor's own console. A missing key message always points at one of these.
SIGNUP_URLS: dict[str, str] = {
    "EXA_API_KEY": "https://exa.ai",
    "PARALLEL_API_KEY": "https://parallel.ai",
    "FIRECRAWL_API_KEY": "https://firecrawl.dev",
    "TAVILY_API_KEY": "https://app.tavily.com/home",
    "KEENABLE_API_KEY": "https://keenable.ai",
    "BRAVE_API_KEY": "https://api-dashboard.search.brave.com/app/keys",
    "PERPLEXITY_API_KEY": "https://www.perplexity.ai/account/api",
    "XAI_API_KEY": "https://console.x.ai",
    "DEEPSEEK_API_KEY": "https://platform.deepseek.com/api_keys",
    "TINYFISH_API_KEY": "https://www.tinyfish.ai",
}

VENDOR_LABELS: dict[str, str] = {
    "searxng": "SearXNG",
    "ddgs": "DuckDuckGo",
    "exa": "Exa",
    "parallel": "Parallel",
    "firecrawl": "Firecrawl",
    "tavily": "Tavily",
    "keenable": "Keenable",
    "brave": "Brave Search",
    "perplexity": "Perplexity",
    "deepseek": "DeepSeek",
    "tinyfish": "TinyFish",
    "xai": "xAI",
}

#: Wording that turns a 400/403/429 into a quota verdict rather than a rate limit.
_QUOTA_WORDS = (
    "credit",
    "credits",
    "quota",
    "insufficient",
    "balance",
    "billing",
    "payment required",
    "usage limit",
    "usage_limit",
    "out of funds",
    "exceeded your",
    "plan limit",
    "spend limit",
)

#: A Retry-After longer than this is reported instead of slept: the router's total budget
#: is 8 s, and a 60 s sleep inside a request is a hang, not a retry.
MAX_RETRY_SLEEP_SECONDS = 5.0


def new_client(
    timeout: float = DEFAULT_SEARCH_TIMEOUT, headers: Mapping[str, str] | None = None
) -> httpx.Client:
    """The one place a vendor HTTP client is built, so tests can swap the transport."""
    base_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if headers:
        base_headers.update(headers)
    return httpx.Client(timeout=timeout, follow_redirects=True, headers=base_headers)


def missing_key_message(env: str, vendor: str, *, note: str = "") -> str:
    """``"<VENDOR>_API_KEY is not set. Get a key at <signup url>"`` (endpoints.md:190)."""
    label = VENDOR_LABELS.get(vendor, vendor.title())
    url = SIGNUP_URLS.get(env, "")
    tail = f" Get a key at {url}" if url else ""
    extra = f" {note}" if note else ""
    return f"{env} is not set.{tail}{extra} {label} is skipped until it is configured."


def failure(
    message: str,
    *,
    quota: bool = False,
    out_of_credit: bool = False,
    retry_after: float | None = None,
    status: int | None = None,
    kind: str = "error",
) -> dict[str, Any]:
    """The single failure shape every adapter and the router understand."""
    return {
        "ok": False,
        "results": [],
        "items": [],
        "error": message,
        "quota": bool(quota),
        "out_of_credit": bool(out_of_credit),
        "retry_after": retry_after,
        "status": status,
        "kind": kind,
    }


def success(
    *,
    results: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
    **meta: Any,
) -> dict[str, Any]:
    return {
        "ok": True,
        "results": results or [],
        "items": items or [],
        "error": None,
        "quota": False,
        "out_of_credit": False,
        "retry_after": None,
        "status": 200,
        "kind": "ok",
        **meta,
    }


def search_row(
    url: str,
    title: str = "",
    description: str = "",
    position: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    """A normalized search row; empty optional fields are dropped, never sent as ``None``."""
    row: dict[str, Any] = {
        "url": (url or "").strip(),
        "title": (title or "").strip(),
        "description": (description or "").strip(),
        "position": int(position),
    }
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            row[key] = value
    return row


def extract_item(url: str, content: str = "", title: str = "") -> dict[str, Any]:
    return {"url": url, "title": title or "", "content": content or ""}


def extract_error(url: str, message: str) -> dict[str, Any]:
    return {"url": url, "title": "", "content": "", "error": message}


def quota_wording(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in _QUOTA_WORDS)


def retry_after_seconds(headers: Mapping[str, str] | None) -> float | None:
    """Parse ``Retry-After`` (delta seconds or an HTTP date) into seconds."""
    if not headers:
        return None
    raw = ""
    for key, value in headers.items():
        if key.lower() == "retry-after":
            raw = str(value).strip()
            break
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(raw)
        import datetime as _dt

        now = _dt.datetime.now(when.tzinfo or _dt.timezone.utc)
        return max(0.0, (when - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _header_map(response: httpx.Response) -> dict[str, str]:
    return {key: value for key, value in response.headers.items()}


def http_failure(
    vendor: str, status: int, body: str, headers: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Map one non-2xx response to the contract's actionable failure."""
    label = VENDOR_LABELS.get(vendor, vendor.title())
    snippet = " ".join((body or "").split())[:200]
    retry_after = retry_after_seconds(headers)
    if status == 402 or (status in (400, 403, 429) and quota_wording(body)):
        return failure(
            f"{label} quota is exhausted; skipped for up to an hour",
            quota=True,
            out_of_credit=True,
            status=status,
            retry_after=retry_after,
        )
    if status == 401:
        return failure(
            f"{label} rejected the API key (HTTP 401). Check the key in Settings ▸ Plugins.",
            status=status,
            kind="auth",
        )
    if status == 403:
        return failure(
            f"{label} refused the request (HTTP 403): {snippet}" if snippet else f"{label} refused the request (HTTP 403)",
            status=status,
            kind="policy",
        )
    if status == 404:
        return failure(f"{label} has no content for that request (HTTP 404)", status=status, kind="not_found")
    if status == 429:
        wait = f" Retry-After: {retry_after:.0f}s." if retry_after else ""
        return failure(
            f"{label} rate-limited (HTTP 429); the single retry was refused.{wait}".strip(),
            status=status,
            retry_after=retry_after,
            kind="rate_limited",
        )
    if status >= 500:
        return failure(
            f"{label} server error (HTTP {status}): {snippet}" if snippet else f"{label} server error (HTTP {status})",
            status=status,
            kind="server",
        )
    return failure(
        f"{label} returned HTTP {status}: {snippet}" if snippet else f"{label} returned HTTP {status}",
        status=status,
    )


def decode_response(response: httpx.Response) -> str:
    """Body text decoded as UTF-8; SSE and JSON are UTF-8 by spec."""
    try:
        return response.content.decode(response.encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        return response.text


def send(
    method: str,
    url: str,
    *,
    vendor: str,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    json_body: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_SEARCH_TIMEOUT,
    client: httpx.Client | None = None,
) -> tuple[httpx.Response | None, dict[str, Any] | None]:
    """One request, no exceptions: ``(response, failure)``."""
    label = VENDOR_LABELS.get(vendor, vendor.title())
    owns_client = client is None
    http = client or new_client(timeout)
    try:
        response = http.request(method, url, params=params, json=json_body, headers=headers)
    except httpx.TimeoutException:
        return None, failure(f"{label} timed out after {timeout:.0f}s", kind="timeout")
    except httpx.HTTPError as exc:
        return None, failure(f"{label} request failed: {exc}", kind="transport")
    finally:
        if owns_client:
            http.close()
    if response.status_code >= 400:
        return response, http_failure(vendor, response.status_code, decode_response(response), _header_map(response))
    return response, None


def request_json(
    method: str,
    url: str,
    *,
    vendor: str,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    json_body: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_SEARCH_TIMEOUT,
    client: httpx.Client | None = None,
    retry_rate_limit: bool = True,
    sleep: Any = time.sleep,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Request and decode JSON. A 429 without quota wording is retried once."""
    response, error = send(
        method,
        url,
        vendor=vendor,
        headers=headers,
        params=params,
        json_body=json_body,
        timeout=timeout,
        client=client,
    )
    if error is not None:
        retry_after = error.get("retry_after")
        if (
            retry_rate_limit
            and error.get("kind") == "rate_limited"
            and retry_after is not None
            and retry_after <= MAX_RETRY_SLEEP_SECONDS
        ):
            sleep(retry_after)
            response, error = send(
                method,
                url,
                vendor=vendor,
                headers=headers,
                params=params,
                json_body=json_body,
                timeout=timeout,
                client=client,
            )
        if error is not None:
            return None, error
    assert response is not None
    try:
        return response.json(), None
    except ValueError:
        content_type = response.headers.get("content-type", "unknown")
        return None, failure(
            f"{VENDOR_LABELS.get(vendor, vendor.title())} returned a non-JSON body "
            f"(HTTP {response.status_code}, content-type {content_type}); expected JSON.",
            status=response.status_code,
            kind="bad_payload",
        )


# ── MCP (remote tool calls over JSON-RPC) ──────────────────────────────────────────


def parse_mcp_body(body: str) -> tuple[str | None, dict[str, Any] | None]:
    """First text item from an MCP ``tools/call`` reply: JSON envelope or SSE ``data:``.

    Mirrors the framing the built-in keyless client accepts: a plain JSON body, or one
    ``data: {...}`` line per SSE frame. SSE frames are split on CRLF/CR/LF only, never on
    ``str.splitlines`` (it also breaks on U+2028, which occurs inside page text).
    """

    def _from_payload(payload: str) -> tuple[str | None, dict[str, Any] | None]:
        payload = payload.strip()
        if not payload.startswith("{"):
            return None, None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None, None
        error = data.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            return None, failure(f"MCP tool error: {message}", kind="mcp")
        result = data.get("result") or {}
        texts = [
            item.get("text", "")
            for item in (result.get("content") or [])
            if isinstance(item, dict)
        ]
        if result.get("isError"):
            joined = " ".join(text for text in texts if text) or "MCP tool call failed"
            return None, failure(joined, kind="mcp")
        for text in texts:
            if text:
                return str(text), None
        return "", None

    candidates = []
    stripped = body.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    candidates.extend(
        line[len("data:") :] for line in re.split(r"\r\n|\r|\n", body) if line.startswith("data:")
    )
    for candidate in candidates:
        text, error = _from_payload(candidate)
        if error is not None:
            return None, error
        if text:
            return text, None
    return None, failure("MCP response contained no text content", kind="bad_payload")


def mcp_call(
    url: str,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    vendor: str,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    client: httpx.Client | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """POST a JSON-RPC ``tools/call`` and return its first text content item."""
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        request_headers.update(headers)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": dict(arguments)},
    }
    response, error = send(
        "POST",
        url,
        vendor=vendor,
        headers=request_headers,
        json_body=payload,
        timeout=timeout,
        client=client,
    )
    if error is not None:
        return None, error
    assert response is not None
    return parse_mcp_body(decode_response(response))


# ── Exa text payload ──────────────────────────────────────────────────────────────

_EXA_LABELS = ("Title:", "URL:", "Highlights:", "Published:", "Author:")


def parse_exa_search_text(text: str, limit: int = 5) -> list[dict[str, Any]]:
    """Parse Exa's ``---``-separated ``Title:/URL:/Published:/Author:/Highlights:`` blocks.

    CRLF is normalized before splitting (endpoints.md:64-65), ``N/A`` is a sentinel and
    never becomes content, and a block without a URL is dropped.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    rows: list[dict[str, Any]] = []
    for block in normalized.split("\n---\n"):
        title = url = published = source = ""
        highlights: list[str] = []
        in_highlights = False
        for line in (raw.strip() for raw in block.split("\n")):
            if line.startswith("Title:"):
                title = _clean_exa_value(line[len("Title:") :])
            elif line.startswith("URL:"):
                url = _clean_exa_value(line[len("URL:") :])
            elif line.startswith("Published:"):
                published = _clean_exa_value(line[len("Published:") :])
            elif line.startswith("Author:"):
                source = _clean_exa_value(line[len("Author:") :])
            elif in_highlights and line and not line.startswith(_EXA_LABELS):
                highlights.append(line)
            if line.startswith(_EXA_LABELS):
                in_highlights = line.startswith("Highlights:")
        if url:
            rows.append(
                search_row(
                    url,
                    title,
                    " ".join(highlights),
                    len(rows) + 1,
                    date=published or None,
                    source=source or None,
                )
            )
        if limit and len(rows) >= limit:
            break
    return rows


def _clean_exa_value(value: str) -> str:
    text = value.strip()
    return "" if text.upper() in ("N/A", "NONE", "NULL") else text


# ── misc helpers ──────────────────────────────────────────────────────────────────


def first_present(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def joined_excerpts(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def query_within_chars(query: str, max_chars: int) -> tuple[str, bool]:
    """Clip on a word boundary to ``max_chars`` Unicode characters.

    DeepSeek caps its query at 128 characters (endpoints.md:169). A hard slice would cut a
    word in half and change the meaning of the last term, so the clip happens at the last
    space that fits; a single 128+ character token is hard-sliced because there is no
    boundary to respect.
    """
    text = (query or "").strip()
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    cut = head.rfind(" ")
    if cut > max_chars // 2:
        head = head[:cut]
    return head.rstrip(), True


def call_timeout(kwargs: Mapping[str, Any] | None, default: float) -> float:
    """Per-call timeout from the router's budget, clamped to the vendor default.

    The router's per-attempt window is shorter than every vendor default in this repository,
    so this only ever shortens a request. A backend whose transport is slower than the
    global default can widen its own window (``BackendSpec.budget_ms``), and the clamp still
    keeps the call at or below the vendor's own limit. A budget never makes a vendor call
    slower than its own limit.
    """
    raw = (kwargs or {}).get("timeout")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.5, min(default, value))
