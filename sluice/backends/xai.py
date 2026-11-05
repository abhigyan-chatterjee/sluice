"""xAI: LLM-mediated search through /responses with the web_search tool; parse the JSON block, then annotations, then citations.

Contract: ``references/endpoints.md#xai`` (lines 150-159) and PLAN.md:88-93. Implemented
in T6. Fragile by construction: it is a model answering with citations, not a search index (references/endpoints.md:158).
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="xai",
    label="xAI",
    tiers=("paid",),
    search=True,
    extract=False,
    secret_env="XAI_API_KEY",
    summary="LLM-mediated search through /responses with the web_search tool; parse the JSON block, then annotations, then citations.",
    task="T6",
)


class XaiBackend(Backend):
    """Fragile by construction: it is a model answering with citations, not a search index (references/endpoints.md:1"""

    spec = SPEC
