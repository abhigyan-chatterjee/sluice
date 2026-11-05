"""Firecrawl: v2 search and scrape.

Contract: ``references/endpoints.md#firecrawl`` (lines 94-103) and PLAN.md:82-86. The
keyless call sends **no** ``Authorization`` header at all; a key turns the same request into
a bearer call against the same paths and bodies.

Search rows arrive under ``data.web``, occasionally ``data`` as a list, or ``results``
(references/endpoints.md:99-100), so the extractor accepts all three shapes.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import (
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_SEARCH_TIMEOUT,
    as_list,
    extract_error,
    extract_item,
    failure,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="firecrawl",
    label="Firecrawl",
    tiers=("keyless", "free", "paid"),
    search=True,
    extract=True,
    secret_env="FIRECRAWL_API_KEY",
    summary="v2 search and scrape; the keyless call sends no Authorization header at all.",
    task="T5",
    settings=("FIRECRAWL_API_KEY", "FIRECRAWL_API_URL"),
)

FIRECRAWL_API_URL = "https://api.firecrawl.dev"


class FirecrawlBackend(Backend):
    """Search rows arrive under data.web, data as a list, or results."""

    spec = SPEC
    capabilities = ("search", "extract")

    @property
    def base_url(self) -> str:
        return str(self.settings.get("FIRECRAWL_API_URL") or FIRECRAWL_API_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        return headers

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 20))
        data, error = request_json(
            "POST",
            f"{self.base_url}/v2/search",
            vendor="firecrawl",
            headers=self._headers(),
            json_body={"query": query, "limit": safe_limit},
            timeout=DEFAULT_SEARCH_TIMEOUT,
        )
        if error is not None:
            return error
        hits = _search_hits(data)
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("description") or hit.get("snippet") or "",
                index + 1,
                date=hit.get("publishedDate") or hit.get("date") or None,
            )
            for index, hit in enumerate(hits[:safe_limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed" if self.key else "keyless"}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for url in [str(item) for item in urls]:
            data, error = request_json(
                "POST",
                f"{self.base_url}/v2/scrape",
                vendor="firecrawl",
                headers=self._headers(),
                json_body={"url": url, "formats": ["markdown"]},
                timeout=DEFAULT_FETCH_TIMEOUT,
            )
            if error is not None:
                items.append(extract_error(url, error["error"]))
                continue
            payload = (data or {}).get("data") if isinstance(data, dict) else None
            payload = payload if isinstance(payload, dict) else {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            content = payload.get("markdown") or payload.get("html") or ""
            if not content:
                items.append(extract_error(url, "Firecrawl returned no page content"))
                continue
            items.append(
                extract_item(
                    str(metadata.get("sourceURL") or metadata.get("url") or url),
                    str(content),
                    str(metadata.get("title") or ""),
                )
            )
        return items


def _search_hits(data: Any) -> list[dict[str, Any]]:
    """Every shape Firecrawl has been seen to return, in order of likelihood."""
    if isinstance(data, list):
        return [hit for hit in data if isinstance(hit, dict)]
    if not isinstance(data, dict):
        return []
    inner = data.get("data")
    for candidate in (
        inner.get("web") if isinstance(inner, dict) else None,
        inner if isinstance(inner, list) else None,
        inner.get("results") if isinstance(inner, dict) else None,
        data.get("web"),
        data.get("results"),
    ):
        hits = as_list(candidate)
        if hits:
            return [hit for hit in hits if isinstance(hit, dict)]
    return []
