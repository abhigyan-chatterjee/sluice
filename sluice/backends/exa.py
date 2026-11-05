"""Exa: Keyless MCP (mcp.exa.ai) and keyed REST behind one entry.

Contract: ``references/endpoints.md#exa`` (lines 55-73) and PLAN.md:74-80. Implemented
in T4. One vendor entry, two access modes: the router picks the cheapest enabled one (DESIGN.md:136-141).
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="exa",
    label="Exa",
    tiers=("keyless", "free", "paid"),
    search=True,
    extract=True,
    secret_env="EXA_API_KEY",
    summary="Keyless MCP (mcp.exa.ai) and keyed REST behind one entry.",
    task="T4",
)


class ExaBackend(Backend):
    """One vendor entry, two access modes: the router picks the cheapest enabled one (DESIGN.md:136-141)."""

    spec = SPEC
