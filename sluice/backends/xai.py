"""xAI: LLM-mediated search through ``/responses`` with the ``web_search`` tool.

Contract: ``references/endpoints.md#xai`` (lines 150-159) and PLAN.md:88-93. Fragile by
construction: it is a model answering with citations, not a search index, so it sorts last
inside its tier and its rows are normalised defensively.

Parsing order (references/endpoints.md:157):
1. a JSON object with a ``results`` array in the output text (the prompt asks for exactly
   that);
2. ``url_citation`` annotations, with the surrounding text as the description;
3. the raw ``citations`` list of URLs.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import (
    call_timeout,
    DEFAULT_SEARCH_TIMEOUT,
    SIGNUP_URLS,
    failure,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="xai",
    label="xAI",
    tiers=("paid",),
    search=True,
    extract=False,
    secret_env="XAI_API_KEY",
    summary="LLM-mediated search through /responses with the web_search tool; parse the JSON block, then annotations, then citations.",
    task="T6",
    settings=("XAI_API_KEY", "XAI_BASE_URL"),
)

XAI_API_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-build-0.1"
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def build_prompt(query: str, limit: int) -> str:
    return (
        "Use the web_search tool to find current information for the query below, then "
        "respond with ONLY a single JSON object — no prose, no markdown fences, no inline "
        "citation links — matching this exact schema:\n\n"
        '{"results": [{"title": "string", "url": "string", "description": "1-2 sentence summary"}]}\n\n'
        f"Return at most {limit} results, ordered by relevance, with absolute https:// URLs. "
        'If no usable results exist, return {"results": []}.\n\n'
        f"Query: {query}"
    )


class XaiBackend(Backend):
    """Search only, and the last resort inside the paid tier."""

    spec = SPEC
    capabilities = ("search",)

    @property
    def base_url(self) -> str:
        return str(self.settings.get("XAI_BASE_URL") or XAI_API_URL).rstrip("/")

    def missing_message(self) -> str:
        if self.key:
            return ""
        return (
            f"XAI_API_KEY is not set. Get a key at {SIGNUP_URLS['XAI_API_KEY']} (xAI web "
            "search is LLM-mediated and needs the paid Responses API)."
        )

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
        safe_limit = max(1, min(int(limit), 20))
        effective = call_timeout(kwargs, DEFAULT_SEARCH_TIMEOUT)
        data, error = request_json(
            "POST",
            f"{self.base_url}/responses",
            vendor="xai",
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            json_body={
                "model": DEFAULT_MODEL,
                "input": [{"role": "user", "content": build_prompt(query, safe_limit)}],
                "tools": [{"type": "web_search"}],
                "include": ["no_inline_citations"],
            },
            timeout=effective,
        )
        if error is not None:
            return error
        payload = data or {}
        envelope = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(envelope, dict):
            message = envelope.get("message") or envelope.get("code") or "unknown error"
            return failure(f"xAI returned an error envelope: {message}", kind="vendor")

        text_blocks, annotations = _collect_output(payload)
        from_json = _rows_from_json(text_blocks, safe_limit)
        if from_json:
            rows, parse_mode = from_json, "json"
        else:
            from_annotations = _rows_from_annotations(
                annotations, "\n".join(text_blocks), safe_limit
            )
            if from_annotations:
                rows, parse_mode = from_annotations, "annotations"
            else:
                rows, parse_mode = _rows_from_citations(payload.get("citations"), safe_limit), "citations"
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
            "parse_mode": parse_mode,
        }

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError("xai is search-only; extraction runs through the fetch ladder")


def _collect_output(payload: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """``(text_blocks, annotations)`` from ``output[].content[]`` message chunks."""
    if not isinstance(payload, dict):
        return [], []
    texts: list[str] = []
    annotations: list[dict[str, Any]] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for chunk in item.get("content") or []:
            if not isinstance(chunk, dict) or chunk.get("type") != "output_text":
                continue
            text = chunk.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
            for annotation in chunk.get("annotations") or []:
                if isinstance(annotation, dict):
                    annotations.append(annotation)
    return texts, annotations


def _rows_from_json(text_blocks: list[str], limit: int) -> list[dict[str, Any]]:
    for block in text_blocks:
        candidates = [block]
        match = _JSON_BLOCK.search(block)
        if match and match.group(0) != block:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            results = parsed.get("results") if isinstance(parsed, dict) else None
            if not isinstance(results, list):
                continue
            rows: list[dict[str, Any]] = []
            for entry in results:
                if not isinstance(entry, dict):
                    continue
                url = str(entry.get("url") or "").strip()
                if not url:
                    continue
                rows.append(
                    search_row(
                        url,
                        str(entry.get("title") or ""),
                        str(entry.get("description") or ""),
                        len(rows) + 1,
                    )
                )
                if len(rows) >= limit:
                    break
            if rows:
                return rows
    return []


def _rows_from_annotations(
    annotations: list[dict[str, Any]], joined_text: str, limit: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for annotation in annotations:
        if annotation.get("type") != "url_citation":
            continue
        url = str(annotation.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        description = ""
        start, end = annotation.get("start_index"), annotation.get("end_index")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(joined_text):
            description = joined_text[max(0, start - 200) : start].strip()[-200:]
        rows.append(search_row(url, "", description, len(rows) + 1))
        if len(rows) >= limit:
            break
    return rows


def _rows_from_citations(citations: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(citations, list):
        return []
    rows = [
        search_row(str(url), "", "", index + 1)
        for index, url in enumerate(citations[:limit])
        if isinstance(url, str) and url.strip()
    ]
    return rows
