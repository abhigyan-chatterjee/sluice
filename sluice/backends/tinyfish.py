"""TinyFish: paid, source-only search results.

Contract: ``references/endpoints.md#tinyfish`` (lines 177-184) and PLAN.md:88-93.
``X-API-Key`` carries the credential; a 401 with ``error_code: INVALID_API_KEY`` names the bad
key instead of reporting a generic HTTP failure.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import (
    call_timeout,
    DEFAULT_SEARCH_TIMEOUT,
    SIGNUP_URLS,
    as_list,
    decode_response,
    failure,
    send,
    search_row,
)

SPEC = BackendSpec(
    id="tinyfish",
    label="TinyFish",
    tiers=("paid",),
    search=True,
    extract=False,
    secret_env="TINYFISH_API_KEY",
    summary="Paid; source-only results, X-API-Key header.",
    task="T6",
    settings=("TINYFISH_API_KEY",),
)

TINYFISH_ENDPOINT = "https://api.search.tinyfish.ai/"


class TinyfishBackend(Backend):
    """401 INVALID_API_KEY is reported as a bad key, not as a generic failure."""

    spec = SPEC
    capabilities = ("search",)

    def missing_message(self) -> str:
        if self.key:
            return ""
        return f"TINYFISH_API_KEY is not set. Get a key at {SIGNUP_URLS['TINYFISH_API_KEY']}"

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
        effective = call_timeout(kwargs, DEFAULT_SEARCH_TIMEOUT)
        response, error = send(
            "GET",
            TINYFISH_ENDPOINT,
            vendor="tinyfish",
            params={
                "query": query,
                "domain_type": "web",
                "max_results": max(1, min(int(limit), 50)),
            },
            headers={"X-API-Key": self.key, "Accept": "application/json"},
            timeout=effective,
        )
        if error is not None:
            body = decode_response(response) if response is not None else ""
            if error.get("status") in (401, 403) or "INVALID_API_KEY" in body:
                return failure(
                    "TinyFish rejected the API key (401 INVALID_API_KEY). "
                    f"Check TINYFISH_API_KEY at {SIGNUP_URLS['TINYFISH_API_KEY']}.",
                    status=error.get("status") or 401,
                    kind="auth",
                )
            return error

        assert response is not None
        try:
            data = response.json()
        except ValueError:
            return failure("TinyFish returned a non-JSON body; expected JSON.", kind="bad_payload")
        results = as_list((data or {}).get("results"))
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("snippet") or hit.get("description") or "",
                hit.get("position") or index + 1,
                date=hit.get("date") or None,
                source=hit.get("site_name") or hit.get("publisher") or None,
            )
            for index, hit in enumerate(results[: max(1, min(int(limit), 50))])
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
            "total_results": (data or {}).get("total_results"),
            "page": (data or {}).get("page"),
        }

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError("tinyfish is search-only; extraction runs through the fetch ladder")
