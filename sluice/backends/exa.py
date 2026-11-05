"""Exa: keyless MCP (``mcp.exa.ai``) and keyed REST behind one entry.

Contract: ``references/endpoints.md#exa`` (lines 55-73) and PLAN.md:74-80. One vendor
entry, two access modes; the router picks the mode from whether a key is configured
(DESIGN.md:136-141).

Keyless replies come back as an SSE stream of ``data:`` frames whose text is a list of
``---``-separated blocks with ``Title:``, ``URL:``, ``Published:``, ``Author:`` and
``Highlights:`` lines. CRLF is normalized, and ``N/A`` is a sentinel rather than content.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import (
    call_timeout,
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_SEARCH_TIMEOUT,
    extract_error,
    extract_item,
    failure,
    mcp_call,
    parse_exa_search_text,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="exa",
    label="Exa",
    tiers=("keyless", "free", "paid"),
    search=True,
    extract=True,
    secret_env="EXA_API_KEY",
    summary="Keyless MCP (mcp.exa.ai) and keyed REST behind one entry.",
    task="T4",
    settings=("EXA_API_KEY",),
)

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
EXA_API_URL = "https://api.exa.ai"


class ExaBackend(Backend):
    """One vendor entry, two access modes."""

    spec = SPEC
    capabilities = ("search", "extract")

    # ── search ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 20))
        effective = call_timeout(kwargs, DEFAULT_SEARCH_TIMEOUT)
        if self.key:
            return self._search_keyed(query, safe_limit, effective)
        return self._search_keyless(query, safe_limit, effective)

    def _search_keyless(self, query: str, limit: int, timeout: float = DEFAULT_SEARCH_TIMEOUT) -> dict[str, Any]:
        text, error = mcp_call(
            EXA_MCP_URL,
            "web_search_exa",
            {"query": query, "numResults": limit},
            vendor="exa",
            timeout=timeout,
        )
        if error is not None:
            return error
        rows = parse_exa_search_text(text or "", limit)
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyless"}

    def _search_keyed(self, query: str, limit: int, timeout: float = DEFAULT_SEARCH_TIMEOUT) -> dict[str, Any]:
        data, error = request_json(
            "POST",
            f"{EXA_API_URL}/search",
            vendor="exa",
            headers={"x-api-key": self.key},
            json_body={
                "query": query,
                "numResults": limit,
                "type": "auto",
                "contents": {"highlights": True},
            },
            timeout=timeout,
        )
        if error is not None:
            return error
        results = (data or {}).get("results") or []
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                " ".join(hit.get("highlights") or []) or hit.get("text") or "",
                index + 1,
                date=hit.get("publishedDate") or None,
                score=hit.get("score"),
            )
            for index, hit in enumerate(results[:limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed"}

    # ── extract ─────────────────────────────────────────────────────────────────

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        targets = [str(url) for url in urls]
        if self.key:
            return self._extract_keyed(targets)
        return self._extract_keyless(targets)

    def _extract_keyed(self, urls: list[str]) -> list[dict[str, Any]]:
        data, error = request_json(
            "POST",
            f"{EXA_API_URL}/contents",
            vendor="exa",
            headers={"x-api-key": self.key},
            json_body={"urls": urls, "text": True},
            timeout=DEFAULT_FETCH_TIMEOUT,
        )
        if error is not None:
            return [extract_error(url, error["error"]) for url in urls]
        results = (data or {}).get("results") or []
        by_url = {str(hit.get("url") or ""): hit for hit in results}
        items: list[dict[str, Any]] = []
        for url in urls:
            hit = by_url.get(url)
            if hit is None:
                items.append(extract_error(url, "Exa returned no content for this URL"))
                continue
            items.append(
                extract_item(url, hit.get("text") or "", hit.get("title") or "")
            )
        return items

    def _extract_keyless(self, urls: list[str]) -> list[dict[str, Any]]:
        """One MCP fetch per URL: the tool accepts a list but returns one merged text."""
        items: list[dict[str, Any]] = []
        for url in urls:
            text, error = mcp_call(
                EXA_MCP_URL,
                "web_fetch_exa",
                {"urls": [url]},
                vendor="exa",
                timeout=DEFAULT_FETCH_TIMEOUT,
            )
            if error is not None:
                items.append(extract_error(url, error["error"]))
                continue
            items.append(extract_item(url, text or "", _first_title(text or "")))
        return items


_TITLE_LINE = re.compile(r"^(?:#\s+|Title:\s*)(.+)$", re.MULTILINE)


def _first_title(text: str) -> str:
    match = _TITLE_LINE.search(text or "")
    return match.group(1).strip() if match else ""
