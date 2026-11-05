"""Tavily: X-Tavily-Access-Mode: keyless without a key, bearer token with one; X-Client-Name: hermes-agent always.

Contract: ``references/endpoints.md#tavily`` (lines 105-116) and PLAN.md:82-86. Implemented
in T5. Extract failures arrive in failed_results and failed_urls, never as an exception.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="tavily",
    label="Tavily",
    tiers=("keyless", "free", "paid"),
    search=True,
    extract=True,
    secret_env="TAVILY_API_KEY",
    summary="X-Tavily-Access-Mode: keyless without a key, bearer token with one; X-Client-Name: hermes-agent always.",
    task="T5",
)


class TavilyBackend(Backend):
    """Extract failures arrive in failed_results and failed_urls, never as an exception."""

    spec = SPEC
