"""Firecrawl: v2 search and scrape; the keyless call sends no Authorization header at all.

Contract: ``references/endpoints.md#firecrawl`` (lines 94-103) and PLAN.md:82-86. Implemented
in T5. Search rows arrive under data.web, occasionally data as a list, or results.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="firecrawl",
    label="Firecrawl",
    tiers=("keyless", "free", "paid"),
    search=True,
    extract=True,
    secret_env="FIRECRAWL_API_KEY",
    summary="v2 search and scrape; the keyless call sends no Authorization header at all.",
    task="T5",
)


class FirecrawlBackend(Backend):
    """Search rows arrive under data.web, occasionally data as a list, or results."""

    spec = SPEC
