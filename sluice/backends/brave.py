"""Brave: search only; the API caps ``count`` at 20.

Contract: ``references/endpoints.md#brave`` (lines 128-138) and PLAN.md:88-93.

Two different keys exist in the wild and they are **not interchangeable**:

* ``BRAVE_API_KEY`` — this adapter, the Search API subscription token.
* ``BRAVE_SEARCH_API_KEY`` — Hermes' own free-tier provider key.

A user who set only the second one gets a message naming the trap instead of "invalid key".
``422 SUBSCRIPTION_TOKEN_INVALID`` is a bad key; ``402 USAGE_LIMIT_EXCEEDED`` is the free
allowance being spent. Neither is reported as "query too long" (references/endpoints.md:136-137).
"""

from __future__ import annotations

from typing import Any, Sequence

from ..config import secret
from . import Backend, BackendSpec
from ._http import (
    call_timeout,
    DEFAULT_SEARCH_TIMEOUT,
    SIGNUP_URLS,
    decode_response,
    failure,
    send,
    search_row,
)

SPEC = BackendSpec(
    id="brave",
    label="Brave",
    tiers=("free", "paid"),
    search=True,
    extract=False,
    secret_env="BRAVE_API_KEY",
    summary="Search only; the API caps count at 20.",
    task="T6",
    settings=("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY"),
)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

TWO_KEY_TRAP = (
    "BRAVE_API_KEY is not set, but BRAVE_SEARCH_API_KEY is. They are different keys: "
    "BRAVE_SEARCH_API_KEY is Hermes' free-tier Brave key, and this backend needs the Search "
    "API subscription token as BRAVE_API_KEY. Copy it into Settings ▸ Plugins (or unset the "
    "other key and use Hermes' brave-free provider instead)."
)


class BraveBackend(Backend):
    """Search only. Maps 422 to a bad key and 402 to a spent allowance."""

    spec = SPEC
    capabilities = ("search",)

    @property
    def alternate_key(self) -> str:
        alt = str(self.settings.get("BRAVE_SEARCH_API_KEY") or "").strip()
        return alt or secret("BRAVE_SEARCH_API_KEY")

    def missing_message(self) -> str:
        if self.key:
            return ""
        if self.alternate_key:
            return TWO_KEY_TRAP
        return (
            f"BRAVE_API_KEY is not set. Get a key at {SIGNUP_URLS['BRAVE_API_KEY']} "
            "(the Search API subscription token, not BRAVE_SEARCH_API_KEY)."
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
        effective = call_timeout(kwargs, DEFAULT_SEARCH_TIMEOUT)
        params: dict[str, Any] = {"q": query, "count": max(1, min(int(limit), 20))}
        if freshness:
            params["freshness"] = freshness
        response, error = send(
            "GET",
            BRAVE_ENDPOINT,
            vendor="brave",
            params=params,
            headers={
                "X-Subscription-Token": self.key,
                "Accept": "application/json",
            },
            timeout=effective,
        )
        if error is not None:
            body = decode_response(response) if response is not None else ""
            status = error.get("status")
            if status == 422 or "SUBSCRIPTION_TOKEN_INVALID" in body:
                return failure(
                    "Brave rejected the subscription token (422 SUBSCRIPTION_TOKEN_INVALID). "
                    f"Check BRAVE_API_KEY at {SIGNUP_URLS['BRAVE_API_KEY']}.",
                    status=422,
                    kind="auth",
                )
            if status == 402 or "USAGE_LIMIT_EXCEEDED" in body:
                return failure(
                    "Brave's free allowance is spent (402 USAGE_LIMIT_EXCEEDED); skipped for "
                    "up to an hour.",
                    quota=True,
                    out_of_credit=True,
                    status=402,
                )
            return error

        assert response is not None
        try:
            data = response.json()
        except ValueError:
            return failure("Brave returned a non-JSON body; expected JSON.", kind="bad_payload")
        hits = ((data.get("web") or {}).get("results") or []) if isinstance(data, dict) else []
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("description") or "",
                index + 1,
                date=hit.get("age") or hit.get("page_age") or None,
            )
            for index, hit in enumerate(hits[: max(1, min(int(limit), 20))])
        ]
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "mode": "keyed"}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError("brave is search-only; extraction runs through the fetch ladder")
