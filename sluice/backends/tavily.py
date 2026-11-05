"""Tavily: keyed or opt-in keyless search and extract.

Contract: ``references/endpoints.md#tavily`` (lines 105-116) and PLAN.md:82-86.

``X-Client-Name: hermes-agent`` is sent on every request. With a key the call is
``Authorization: Bearer``; without one it is ``X-Tavily-Access-Mode: keyless`` and no bearer
header. Extract failures arrive in ``failed_results`` / ``failed_urls`` and become per-URL
error items, never an exception.
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
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="tavily",
    label="Tavily",
    tiers=("keyless", "free", "paid"),
    search=True,
    extract=True,
    secret_env="TAVILY_API_KEY",
    summary="X-Tavily-Access-Mode: keyless without a key, bearer token with one; X-Client-Name: hermes-agent always.",
    task="T5",
    settings=("TAVILY_API_KEY", "TAVILY_BASE_URL"),
)

TAVILY_API_URL = "https://api.tavily.com"
CLIENT_NAME = "hermes-agent"


class TavilyBackend(Backend):
    """Extract failures arrive in failed_results and failed_urls, never as an exception."""

    spec = SPEC
    capabilities = ("search", "extract")

    @property
    def base_url(self) -> str:
        return str(self.settings.get("TAVILY_BASE_URL") or TAVILY_API_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"X-Client-Name": CLIENT_NAME, "Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            headers["X-Tavily-Access-Mode"] = "keyless"
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
            f"{self.base_url}/search",
            vendor="tavily",
            headers=self._headers(),
            json_body={
                "query": query,
                "max_results": safe_limit,
                "include_raw_content": False,
                "include_images": False,
            },
            timeout=DEFAULT_SEARCH_TIMEOUT,
        )
        if error is not None:
            return error
        results = as_list((data or {}).get("results"))
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("content") or "",
                index + 1,
                score=hit.get("score"),
            )
            for index, hit in enumerate(results[:safe_limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed" if self.key else "keyless"}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        targets = [str(url) for url in urls]
        data, error = request_json(
            "POST",
            f"{self.base_url}/extract",
            vendor="tavily",
            headers=self._headers(),
            json_body={"urls": targets, "include_images": False},
            timeout=DEFAULT_FETCH_TIMEOUT,
        )
        if error is not None:
            return [extract_error(url, error["error"]) for url in targets]

        items: list[dict[str, Any]] = []
        for hit in as_list((data or {}).get("results")):
            items.append(
                extract_item(
                    hit.get("url") or "",
                    hit.get("raw_content") or hit.get("content") or "",
                    hit.get("title") or "",
                )
            )
        for failed in as_list((data or {}).get("failed_results")):
            url = str(failed.get("url") or "")
            reason = failed.get("error") or "extraction failed"
            items.append(extract_error(url, str(reason)))
        for url in as_list((data or {}).get("failed_urls")):
            items.append(extract_error(str(url), "extraction failed"))
        seen = {item["url"] for item in items}
        items.extend(
            extract_error(url, "Tavily returned no content for this URL")
            for url in targets
            if url not in seen
        )
        return items
