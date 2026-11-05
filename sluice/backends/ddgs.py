"""ddgs: Local ddgs package, no HTTP; runs in a disposable child process with a hard kill and a 12 s wall clock.

Contract: ``references/endpoints.md#ddgs`` (lines 47-53) and PLAN.md:74-80. Implemented
in T4. Search only. Native code can hold the GIL, so a thread timeout may never fire (PLAN.md:78, DESIGN.md:191-193).
"""

from __future__ import annotations

from . import Backend, BackendSpec

SPEC = BackendSpec(
    id="ddgs",
    label="ddgs",
    tiers=("keyless",),
    search=True,
    extract=False,
    secret_env=None,
    summary="Local ddgs package, no HTTP; runs in a disposable child process with a hard kill and a 12 s wall clock.",
    task="T4",
)


class DdgsBackend(Backend):
    """Search only. Native code can hold the GIL, so a thread timeout may never fire (PLAN.md:78, DESIGN.md:191-193)."""

    spec = SPEC
