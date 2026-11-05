"""TinyFish: Paid; source-only results, X-API-Key header.

Contract: ``references/endpoints.md#tinyfish`` (lines 177-184) and PLAN.md:88-93. Implemented
in T6. 401 with error_code INVALID_API_KEY names the bad key rather than a generic failure.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="tinyfish",
    label="TinyFish",
    tiers=("paid",),
    search=True,
    extract=False,
    secret_env="TINYFISH_API_KEY",
    summary="Paid; source-only results, X-API-Key header.",
    task="T6",
)


class TinyfishBackend(Backend):
    """401 with error_code INVALID_API_KEY names the bad key rather than a generic failure."""

    spec = SPEC
