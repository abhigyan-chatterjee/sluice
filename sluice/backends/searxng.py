"""SearXNG: self-hosted metasearch over ``SEARXNG_URL``.

Contract: ``references/endpoints.md#searxng`` (lines 39-45) and PLAN.md:74-80. Search only:
extraction is the ladder's job, not a vendor's.

The instance must enable JSON output. SearXNG ships ``search.formats: [html]`` by default,
which makes ``format=json`` return the HTML page; the parse failure says so explicitly
rather than reporting "invalid JSON". ``docker/searxng/`` in this repository is a working
compose file with the right ``settings.yml``.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import Backend, BackendSpec
from ._http import call_timeout, failure, request_json, search_row

SPEC = BackendSpec(
    id="searxng",
    label="SearXNG",
    tiers=("local",),
    search=True,
    extract=False,
    secret_env=None,
    summary="Self-hosted metasearch over SEARXNG_URL; the instance must enable search.formats: [html, json].",
    task="T4",
    settings=("searxng_url",),
)

JSON_DISABLED_HINT = (
    "SearXNG did not return JSON for format=json. Enable it in the instance's settings.yml "
    "(search.formats: [html, json]) and restart the container; docker/searxng/settings.yml "
    "in this repository already does."
)


class SearxngBackend(Backend):
    """Search only: extraction is the ladder's job, not a vendor's."""

    spec = SPEC
    capabilities = ("search",)

    @property
    def base_url(self) -> str:
        return str(self.settings.get("searxng_url") or "").strip().rstrip("/")

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not self.base_url:
            error = str(self.settings.get("searxng_url_error") or "").strip()
            return failure(
                error
                or (
                    "SEARXNG_URL is not set. Point it at a running SearXNG instance, for example "
                    "http://localhost:8080 (docker/searxng/ ships one with JSON enabled)."
                )
            )
        effective = call_timeout(kwargs, 15.0)
        params: dict[str, Any] = {"q": query, "format": "json", "pageno": 1}
        if freshness:
            params["time_range"] = freshness
        data, error = request_json(
            "GET",
            f"{self.base_url}/search",
            vendor="searxng",
            params=params,
            headers={"Accept": "application/json"},
            timeout=effective,
        )
        if error is not None:
            if error.get("kind") == "bad_payload":
                error["error"] = JSON_DISABLED_HINT
            return error
        if not isinstance(data, dict):
            return failure(JSON_DISABLED_HINT, kind="bad_payload")

        raw = data.get("results") or []
        try:
            ordered = sorted(raw, key=lambda row: float(row.get("score") or 0), reverse=True)
        except (TypeError, ValueError, AttributeError):
            ordered = raw
        rows = [
            search_row(
                hit.get("url") or "",
                hit.get("title") or "",
                hit.get("content") or "",
                index + 1,
                source=hit.get("engine") or None,
                score=hit.get("score"),
                date=hit.get("publishedDate") or None,
            )
            for index, hit in enumerate(ordered[: max(0, limit)])
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
            "raw_count": len(raw),
        }

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "searxng is search-only; extraction runs through the fetch ladder (sluice.ladder)"
        )
