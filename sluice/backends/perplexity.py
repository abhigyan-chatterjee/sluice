"""Perplexity: POST /search for results, /sdk/content/snippets for extraction.

Contract: ``references/endpoints.md#perplexity`` (lines 140-148) and PLAN.md:88-93. Implemented
in T6. Snippets are queried with words taken from the target URLs.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="perplexity",
    label="Perplexity",
    tiers=("paid",),
    search=True,
    extract=True,
    secret_env="PERPLEXITY_API_KEY",
    summary="POST /search for results, /sdk/content/snippets for extraction.",
    task="T6",
)


class PerplexityBackend(Backend):
    """Snippets are queried with words taken from the target URLs."""

    spec = SPEC
