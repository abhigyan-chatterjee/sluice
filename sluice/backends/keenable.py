"""Keenable: Public /public endpoints without a key, keyed paths with one; the X-Keenable-Title header is mandatory.

Contract: ``references/endpoints.md#keenable`` (lines 118-126) and PLAN.md:82-86. Implemented
in T5. Fetch is a GET with the target URL as a query parameter.
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="keenable",
    label="Keenable",
    tiers=("keyless", "paid"),
    search=True,
    extract=True,
    secret_env="KEENABLE_API_KEY",
    summary="Public /public endpoints without a key, keyed paths with one; the X-Keenable-Title header is mandatory.",
    task="T5",
)


class KeenableBackend(Backend):
    """Fetch is a GET with the target URL as a query parameter."""

    spec = SPEC
