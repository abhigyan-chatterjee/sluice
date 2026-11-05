"""Brave: Search only; the API caps count at 20.

Contract: ``references/endpoints.md#brave`` (lines 128-138) and PLAN.md:88-93. Implemented
in T6. Two different keys exist in the wild: BRAVE_API_KEY (this adapter) and BRAVE_SEARCH_API_KEY (Hermes' free tier). They are not interchangeable, and 422 means a bad key while 402 means the allowance is spent.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="brave",
    label="Brave",
    tiers=("free", "paid"),
    search=True,
    extract=False,
    secret_env="BRAVE_API_KEY",
    summary="Search only; the API caps count at 20.",
    task="T6",
)


class BraveBackend(Backend):
    """Two different keys exist in the wild: BRAVE_API_KEY (this adapter) and BRAVE_SEARCH_API_KEY (Hermes' free tier"""

    spec = SPEC
