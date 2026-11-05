"""Perplexity: ``POST /search`` for results, ``/sdk/content/snippets`` for extraction.

Contract: ``references/endpoints.md#perplexity`` (lines 140-148) and PLAN.md:88-93.
``search_context_size: low`` keeps snippets at description length; ``high`` returns multi-KB
page excerpts that belong in ``web_extract``, not in a result row.

Snippets are requested with words taken from the target URLs, because the endpoint ranks
content against a query and a URL-only request has no relevance signal.
"""

from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlparse

from . import Backend, BackendSpec
from ._http import (
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_SEARCH_TIMEOUT,
    SIGNUP_URLS,
    as_list,
    extract_error,
    extract_item,
    failure,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="perplexity",
    label="Perplexity",
    tiers=("paid",),
    search=True,
    extract=True,
    secret_env="PERPLEXITY_API_KEY",
    summary="POST /search for results, /sdk/content/snippets for extraction.",
    task="T6",
    settings=("PERPLEXITY_API_KEY", "PERPLEXITY_BASE_URL"),
)

PERPLEXITY_API_URL = "https://api.perplexity.ai"
MAX_SEARCH_RESULTS = 20
#: Snippet budgets: backend limits are max_tokens 1-16384 and 1-4096 per page.
MAX_TOKENS = 16384
MAX_TOKENS_PER_PAGE = 4096


def query_for_urls(urls: Sequence[str]) -> str:
    """Derive a relevance query from URL path words (``/bloom-filter`` -> ``bloom filter``)."""
    words: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        for token in (
            parsed.path.replace("-", " ").replace("_", " ").replace("/", " ").split()
        ):
            if token.lower() not in words and not token.isdigit():
                words.append(token.lower())
    return " ".join(words[:12]) or " ".join(urls)


class PerplexityBackend(Backend):
    """Snippets are queried with words taken from the target URLs."""

    spec = SPEC
    capabilities = ("search", "extract")

    @property
    def base_url(self) -> str:
        return str(self.settings.get("PERPLEXITY_BASE_URL") or PERPLEXITY_API_URL).rstrip("/")

    def missing_message(self) -> str:
        if self.key:
            return ""
        return f"PERPLEXITY_API_KEY is not set. Get a key at {SIGNUP_URLS['PERPLEXITY_API_KEY']}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        missing = self.missing_message()
        if missing:
            return failure(missing, kind="config")
        safe_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        data, error = request_json(
            "POST",
            f"{self.base_url}/search",
            vendor="perplexity",
            headers=self._headers(),
            json_body={
                "query": query,
                "max_results": safe_limit,
                "search_context_size": "low",
            },
            timeout=DEFAULT_SEARCH_TIMEOUT,
        )
        if error is not None:
            return error
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("snippet") or "",
                index + 1,
                date=hit.get("date") or hit.get("published_date") or None,
            )
            for index, hit in enumerate(as_list((data or {}).get("results"))[:safe_limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed"}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        targets = [str(url) for url in urls]
        missing = self.missing_message()
        if missing:
            return [extract_error(url, missing) for url in targets]
        data, error = request_json(
            "POST",
            f"{self.base_url}/sdk/content/snippets",
            vendor="perplexity",
            headers=self._headers(),
            json_body={
                "query": query_for_urls(targets),
                "urls": targets,
                "max_tokens": MAX_TOKENS,
                "max_tokens_per_page": MAX_TOKENS_PER_PAGE,
            },
            timeout=DEFAULT_FETCH_TIMEOUT,
        )
        if error is not None:
            return [extract_error(url, error["error"]) for url in targets]
        by_url = {
            str(hit.get("url") or ""): hit
            for hit in as_list((data or {}).get("results"))
            if isinstance(hit, dict)
        }
        items: list[dict[str, Any]] = []
        for url in targets:
            entry = by_url.get(url) or {}
            text = str(entry.get("text") or "")
            reason = entry.get("error")
            if reason or not text:
                items.append(extract_error(url, str(reason) if reason else "no content returned"))
                continue
            items.append(extract_item(url, text, str(entry.get("title") or "")))
        return items
