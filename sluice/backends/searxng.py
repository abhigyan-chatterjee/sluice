"""SearXNG: Self-hosted metasearch over SEARXNG_URL; the instance must enable search.formats: [html, json].

Contract: ``references/endpoints.md#searxng`` (lines 39-45) and PLAN.md:74-80. Implemented
in T4. Search only: extraction is the ladder's job, not a vendor's.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="searxng",
    label="SearXNG",
    tiers=("local",),
    search=True,
    extract=False,
    secret_env=None,
    summary="Self-hosted metasearch over SEARXNG_URL; the instance must enable search.formats: [html, json].",
    task="T4",
)


class SearxngBackend(Backend):
    """Search only: extraction is the ladder's job, not a vendor's."""

    spec = SPEC
