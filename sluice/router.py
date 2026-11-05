"""Tier policy, candidate selection, budgets, fallback and winner metadata.

Contract: PLAN.md:64-72. Tier order is local, keyless, free, paid; a backend is skipped
when its key is missing, its circuit is open, its quota window is exhausted, or it cannot
serve the requested capability; candidates inside a tier sort by latency EMA; each search
gets a 2.5 s per-backend budget and an 8 s total budget, run sequentially; every result
carries ``backend``, ``tier``, ``cached``, ``fallback_used`` and ``elapsed_ms``.

Implemented in T3. The constants below are the plan's numbers, kept here so the manifest
defaults, this module and the tests read from one place.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Cheapest first (PLAN.md:67, DESIGN.md:136-141).
TIER_ORDER: tuple[str, ...] = ("local", "keyless", "free", "paid")

PER_BACKEND_BUDGET_MS = 2500
TOTAL_BUDGET_MS = 8000

#: Keys attached to every search row / extract item (PLAN.md:69).
RESULT_META_KEYS: tuple[str, ...] = ("backend", "tier", "cached", "fallback_used", "elapsed_ms")


class Router:
    """Sequential free-first router over the backend registry. Implemented in T3."""

    def __init__(self, store: Any = None, settings: Mapping[str, Any] | None = None) -> None:
        self._store = store
        self._settings = dict(settings or {})

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        raise NotImplementedError(
            "sluice.router.Router.search lands in T3 (PLAN.md:64-72): free-first tier "
            "order, per-backend 2.5 s and total 8 s budgets, winner metadata on every row."
        )

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "sluice.router.Router.extract lands in T3 (PLAN.md:64-72) on top of the T7 ladder."
        )
