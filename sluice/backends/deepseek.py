"""DeepSeek: paid search and fetch.

Contract: ``references/endpoints.md#deepseek`` (lines 161-175) and PLAN.md:88-93.

* The query is 1-128 Unicode characters. It is clipped on a word boundary and the
  truncation is reported as ``query_truncated`` in the result metadata.
* ``count`` is 1-50. ``need_content=false`` leaves ``content`` empty, so the description
  comes from ``summary``.
* ``POST /v1/tools/fetch`` takes exactly one URL per request. 402 is insufficient balance,
  404 is no usable content, 403 is a policy refusal.

Billing: $0.75 per 1,000 successful search requests (references/endpoints.md:174).
"""

from __future__ import annotations

from typing import Any, Sequence

from . import KNOWN_PRICE_PER_CALL_USD, Backend, BackendSpec
from ._http import (
    call_timeout,
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_SEARCH_TIMEOUT,
    SIGNUP_URLS,
    as_list,
    extract_error,
    extract_item,
    failure,
    query_within_chars,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="deepseek",
    label="DeepSeek",
    tiers=("paid",),
    search=True,
    extract=True,
    secret_env="DEEPSEEK_API_KEY",
    summary="Paid; queries clip at 128 Unicode characters on a word boundary, and the truncation is reported in metadata.",
    task="T6",
    settings=("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
)

DEEPSEEK_API_URL = "https://api.deepseek.com"
MAX_QUERY_CHARS = 128
MAX_COUNT = 50


class DeepseekBackend(Backend):
    """Billed per successful search; 402 insufficient balance, 403 a policy refusal."""

    spec = SPEC
    capabilities = ("search", "extract")

    @property
    def base_url(self) -> str:
        return str(self.settings.get("DEEPSEEK_BASE_URL") or DEEPSEEK_API_URL).rstrip("/")

    def missing_message(self) -> str:
        if self.key:
            return ""
        return f"DEEPSEEK_API_KEY is not set. Get a key at {SIGNUP_URLS['DEEPSEEK_API_KEY']}"

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
        clipped, truncated = query_within_chars(query, MAX_QUERY_CHARS)
        if not clipped:
            return failure("DeepSeek needs a non-empty query", kind="config")
        count = max(1, min(int(limit), MAX_COUNT))
        effective = call_timeout(kwargs, DEFAULT_SEARCH_TIMEOUT)
        data, error = request_json(
            "POST",
            f"{self.base_url}/v1/tools/search",
            vendor="deepseek",
            headers=self._headers(),
            json_body={
                "query": clipped,
                "count": count,
                "ori_question": clipped,
                "localization": "",
                "need_content": False,
            },
            timeout=effective,
        )
        if error is not None:
            if error.get("status") == 404:
                error["error"] = "DeepSeek found no usable results for that query (HTTP 404)"
            elif error.get("status") == 403:
                error["error"] = (
                    "DeepSeek refused the query on policy grounds (HTTP 403); rephrase it or "
                    "use another backend."
                )
            return error

        results = as_list((data or {}).get("results")) or as_list((data or {}).get("data"))
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("summary") or hit.get("snippet") or hit.get("content") or "",
                index + 1,
                source=hit.get("site_name") or None,
                date=hit.get("date") or None,
                score=hit.get("rerank_score"),
                authority=hit.get("authority"),
            )
            for index, hit in enumerate(results[:count])
        ]
        return {
            "ok": True,
            "results": rows,
            "items": [],
            "error": None,
            "quota": False,
            "out_of_credit": False,
            "retry_after": None,
            "status": 200,
            "kind": "ok",
            "mode": "keyed",
            "query_truncated": truncated,
            "query_sent": clipped,
            "cost_usd": KNOWN_PRICE_PER_CALL_USD["deepseek"],
        }

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        targets = [str(url) for url in urls]
        missing = self.missing_message()
        if missing:
            return [extract_error(url, missing) for url in targets]
        items: list[dict[str, Any]] = []
        for url in targets:
            data, error = request_json(
                "POST",
                f"{self.base_url}/v1/tools/fetch",
                vendor="deepseek",
                headers=self._headers(),
                json_body={"url": url},
                timeout=DEFAULT_FETCH_TIMEOUT,
            )
            if error is not None:
                if error.get("status") == 404:
                    error["error"] = "DeepSeek has no usable content for that URL (HTTP 404)"
                items.append(extract_error(url, error["error"]))
                continue
            payload = data or {}
            content = payload.get("content") or payload.get("text") or ""
            if not content:
                items.append(extract_error(url, "DeepSeek returned no content for this URL"))
                continue
            items.append(extract_item(url, content, payload.get("title") or ""))
        return items
