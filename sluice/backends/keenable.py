"""Keenable: public endpoints without a key, keyed paths with one.

Contract: ``references/endpoints.md#keenable`` (lines 118-126) and PLAN.md:82-86.
``X-Keenable-Title: hermes-agent`` is mandatory on every request; fetch is a ``GET`` with
the target URL as a query parameter.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import (
    call_timeout,
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_SEARCH_TIMEOUT,
    as_list,
    extract_error,
    extract_item,
    request_json,
    search_row,
)

SPEC = BackendSpec(
    id="keenable",
    label="Keenable",
    tiers=("keyless", "paid"),
    search=True,
    extract=True,
    secret_env="KEENABLE_API_KEY",
    summary="Public /public endpoints without a key, keyed paths with one; the X-Keenable-Title header is mandatory.",
    task="T5",
    settings=("KEENABLE_API_KEY", "KEENABLE_API_URL"),
)

KEENABLE_API_URL = "https://api.keenable.ai"
TITLE_HEADER = "hermes-agent"


class KeenableBackend(Backend):
    """Fetch is a GET with the target URL as a query parameter."""

    spec = SPEC
    capabilities = ("search", "extract")

    @property
    def base_url(self) -> str:
        return str(self.settings.get("KEENABLE_API_URL") or KEENABLE_API_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"X-Keenable-Title": TITLE_HEADER, "Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        return headers

    def _paths(self) -> tuple[str, str]:
        suffix = "" if self.key else "/public"
        return f"/v1/search{suffix}", f"/v1/fetch{suffix}"

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        path, _ = self._paths()
        safe_limit = max(1, min(int(limit), 20))
        effective = call_timeout(kwargs, DEFAULT_SEARCH_TIMEOUT)
        data, error = request_json(
            "POST",
            f"{self.base_url}{path}",
            vendor="keenable",
            headers=self._headers(),
            json_body={"query": query, "max_results": safe_limit},
            timeout=effective,
        )
        if error is not None:
            return error
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("snippet") or hit.get("description") or "",
                index + 1,
                date=hit.get("date") or hit.get("published_date") or None,
            )
            for index, hit in enumerate(as_list((data or {}).get("results"))[:safe_limit])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed" if self.key else "keyless"}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        _, path = self._paths()
        items: list[dict[str, Any]] = []
        for url in [str(item) for item in urls]:
            data, error = request_json(
                "GET",
                f"{self.base_url}{path}",
                vendor="keenable",
                headers=self._headers(),
                params={"url": url},
                timeout=DEFAULT_FETCH_TIMEOUT,
            )
            if error is not None:
                items.append(extract_error(url, error["error"]))
                continue
            payload = data or {}
            content = payload.get("content") or payload.get("text") or ""
            if not content:
                items.append(extract_error(url, "Keenable returned no content for this URL"))
                continue
            items.append(
                extract_item(
                    payload.get("url") or url,
                    content,
                    payload.get("title") or "",
                )
            )
        return items
