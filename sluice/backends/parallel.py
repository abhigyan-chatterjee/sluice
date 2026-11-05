"""Parallel: Keyless MCP search and fetch; keyed search REST. v1 does not call a keyed REST extract.

Contract: ``references/endpoints.md#parallel`` (lines 75-92) and PLAN.md:82-86. Implemented
in T5. Extract rides the keyless MCP fetch tool even when a key is set (references/endpoints.md:91-92).
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="parallel",
    label="Parallel",
    tiers=("keyless", "paid"),
    search=True,
    extract=True,
    secret_env="PARALLEL_API_KEY",
    summary="Keyless MCP search and fetch; keyed search REST. v1 does not call a keyed REST extract.",
    task="T5",
)


class ParallelBackend(Backend):
    """Extract rides the keyless MCP fetch tool even when a key is set (references/endpoints.md:91-92)."""

    spec = SPEC
