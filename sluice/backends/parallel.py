"""Parallel: keyless MCP search and fetch; keyed search REST.

Contract: ``references/endpoints.md#parallel`` (lines 75-92) and PLAN.md:82-86. v1 does not
call a keyed REST extract: with a key set, search goes to ``POST /v1/search`` and extraction
still rides the MCP ``web_fetch`` tool (references/endpoints.md:91-92).

``session_id`` is a random per-process id used only for free-tier rate-limit correlation, and
the optional analytics ``model_name`` field is deliberately never sent.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import (
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_SEARCH_TIMEOUT,
    as_list,
    extract_error,
    extract_item,
    failure,
    joined_excerpts,
    mcp_call,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="parallel",
    label="Parallel",
    tiers=("keyless", "paid"),
    search=True,
    extract=True,
    secret_env="PARALLEL_API_KEY",
    summary="Keyless MCP search and fetch; keyed search REST. v1 does not call a keyed REST extract.",
    task="T5",
    settings=("PARALLEL_API_KEY",),
)

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
PARALLEL_API_URL = "https://api.parallel.ai"

#: Per-process, never persisted: free-tier correlation only (DESIGN.md:136-141).
SESSION_ID = uuid.uuid4().hex


class ParallelBackend(Backend):
    """Search is keyed REST when a key exists; extract is always the MCP fetch tool."""

    spec = SPEC
    capabilities = ("search", "extract")

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 20))
        if self.key:
            return self._search_keyed(query, safe_limit)
        return self._search_keyless(query, safe_limit)

    def _search_keyless(self, query: str, limit: int) -> dict[str, Any]:
        text, error = mcp_call(
            PARALLEL_MCP_URL,
            "web_search",
            {"objective": query, "search_queries": [query], "session_id": SESSION_ID},
            vendor="parallel",
            timeout=DEFAULT_SEARCH_TIMEOUT,
        )
        if error is not None:
            return error
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError:
            return failure("Parallel returned a search payload that is not JSON", kind="bad_payload")
        results = as_list(payload.get("results"))
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                joined_excerpts(hit.get("excerpts")),
                index + 1,
                date=hit.get("publish_date") or hit.get("published_date") or None,
            )
            for index, hit in enumerate(results[:limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyless"}

    def _search_keyed(self, query: str, limit: int) -> dict[str, Any]:
        data, error = request_json(
            "POST",
            f"{PARALLEL_API_URL}/v1/search",
            vendor="parallel",
            headers={"x-api-key": self.key},
            json_body={
                "objective": query,
                "search_queries": [query],
                "max_results": limit,
                "mode": "fast",
            },
            timeout=DEFAULT_SEARCH_TIMEOUT,
        )
        if error is not None:
            return error
        results = (data or {}).get("results") or []
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                joined_excerpts(hit.get("excerpts")),
                index + 1,
                date=hit.get("publish_date") or hit.get("published_date") or None,
            )
            for index, hit in enumerate(results[:limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed"}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        targets = [str(url) for url in urls]
        text, error = mcp_call(
            PARALLEL_MCP_URL,
            "web_fetch",
            {"urls": targets, "objective": "Full page content", "session_id": SESSION_ID},
            vendor="parallel",
            timeout=DEFAULT_FETCH_TIMEOUT,
        )
        if error is not None:
            return [extract_error(url, error["error"]) for url in targets]
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError:
            return [extract_error(url, "Parallel returned a fetch payload that is not JSON") for url in targets]

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in as_list(payload.get("results")):
            url = str(hit.get("url") or "")
            content = (
                hit.get("full_content")
                or hit.get("content")
                or joined_excerpts(hit.get("excerpts"))
            )
            if url:
                seen.add(url)
                items.append(extract_item(url, content or "", hit.get("title") or ""))
        for failure_entry in as_list(payload.get("errors")):
            url = str(failure_entry.get("url") or "")
            reason = failure_entry.get("error_type") or failure_entry.get("content") or "fetch failed"
            if url:
                seen.add(url)
                items.append(extract_error(url, str(reason)))
        # A URL the endpoint silently dropped still gets its own error item.
        items.extend(
            extract_error(url, "Parallel returned no content for this URL")
            for url in targets
            if url not in seen
        )
        return items
