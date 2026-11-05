"""Tier policy, candidate selection, budgets, fallback and winner metadata.

Contract: PLAN.md:64-72.

* Tier order is ``local, keyless, free, paid`` (configurable through ``tier_order``). A
  backend is skipped when its key is missing, its circuit is open, its quota window is
  exhausted, or it cannot serve the requested capability.
* Candidates inside a tier sort by latency EMA. A light query classifier biases that order
  (news versus docs) and can never move a paid backend above a free one: the tier index is
  the first sort key.
* Each search gets a 2.5 s per-attempt budget and an 8 s total budget, run sequentially. A
  backend may declare a wider per-attempt window through ``BackendSpec.budget_ms`` (``ddgs``
  does, for its local multi-engine scrape); the total budget still bounds every attempt. The
  window is passed to the adapter as its request timeout, so it is enforced rather than
  measured after the fact.
* Every search row and extract item carries ``backend``, ``tier``, ``cached``,
  ``fallback_used`` and ``elapsed_ms``.

Search results are cached by normalized query plus filters and limit (900 s general, 300 s
for news); extraction is cached per URL (3600 s), with a 60 s negative cache for a failed
URL so a dead link is not re-fetched on every call.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .backends import BACKEND_MODULES, Backend, make_backend
from .config import backend_plan, load_settings, tier_order
from .store import (
    NS_EXTRACT,
    NS_EXTRACT_NEGATIVE,
    NS_SEARCH_GENERAL,
    NS_SEARCH_NEWS,
    Store,
)

#: Cheapest first (PLAN.md:67, DESIGN.md:136-141). The runtime order comes from settings.
TIER_ORDER: tuple[str, ...] = ("local", "keyless", "free", "paid")

PER_BACKEND_BUDGET_MS = 2500
TOTAL_BUDGET_MS = 8000

#: Keys attached to every search row / extract item (PLAN.md:69).
RESULT_META_KEYS: tuple[str, ...] = ("backend", "tier", "cached", "fallback_used", "elapsed_ms")

#: Which query class each vendor is good at. A bias inside a tier only; it never reorders
#: tiers (DESIGN.md:170-171).
PREFERRED_FOR: dict[str, tuple[str, ...]] = {
    "searxng": ("general",),
    "ddgs": ("general",),
    "exa": ("docs", "general"),
    "parallel": ("docs",),
    "firecrawl": ("general", "docs"),
    "tavily": ("news", "general"),
    "keenable": ("general",),
    "brave": ("news",),
    "perplexity": ("news", "docs"),
    "deepseek": ("docs",),
    "tinyfish": ("general",),
    "xai": ("news",),
}

_NEWS_WORDS = (
    "news",
    "latest",
    "today",
    "tonight",
    "breaking",
    "headline",
    "headlines",
    "yesterday",
    "this week",
    "this month",
    "election",
    "score",
    "match",
    "weather",
    "stock",
    "market",
    "announced",
)
_DOCS_WORDS = (
    "docs",
    "documentation",
    "api",
    "api reference",
    "reference",
    "how to",
    "tutorial",
    "guide",
    "install",
    "configure",
    "configuration",
    "syntax",
    "example",
    "changelog",
    "release notes",
    "man page",
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Candidate:
    """One routable backend with the tier and plan resolved for this request."""

    backend: Backend
    tier: str
    plan: str

    @property
    def id(self) -> str:
        return self.backend.spec.id


def classify_query(query: str) -> str:
    """``news``, ``docs`` or ``general`` — a cheap, deterministic bias, not a model call."""
    lowered = _normalize(query)
    if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in _NEWS_WORDS):
        return "news"
    if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in _DOCS_WORDS):
        return "docs"
    return "general"


def _normalize(query: str) -> str:
    return _WHITESPACE.sub(" ", (query or "").strip().lower())


def search_cache_key(query: str, limit: int, freshness: str = "", qclass: str = "general") -> str:
    """Normalized query plus every input that changes the answer."""
    return json.dumps(
        [_normalize(query), int(limit), freshness or "", qclass],
        separators=(",", ":"),
    )


def extract_cache_key(url: str, fmt: str = "") -> str:
    return json.dumps([str(url), fmt or ""], separators=(",", ":"))


def coerce_limit(raw: Any, default: int = 5, *, maximum: int | None = None) -> int:
    """A usable result limit from an untrusted value.

    :meth:`Router.search` is documented as never raising, and the dashboard routes hand it
    the raw JSON field, so a non-numeric string, ``None`` or a container falls back to
    *default* instead of escaping as ``ValueError``/``TypeError``. The result is always at
    least 1 and, when *maximum* is given, capped there.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        value = default
    value = max(1, value)
    return value if maximum is None else min(value, maximum)


class Router:
    """Sequential free-first router over the backend registry."""

    def __init__(
        self,
        store: Store | None = None,
        settings: Mapping[str, Any] | None = None,
        *,
        backends: Mapping[str, Backend] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._settings = dict(settings) if settings is not None else load_settings()
        self._backends = dict(backends or {})
        # An injected registry replaces the vendor list entirely, so a test can describe
        # routing with doubles instead of instantiating twelve real adapters.
        self._vendors = list(backends) if backends is not None else list(BACKEND_MODULES)
        self._clock = clock

    # ── configuration ───────────────────────────────────────────────────────────

    @property
    def settings(self) -> Mapping[str, Any]:
        return self._settings

    def _int_setting(self, key: str, default: int) -> int:
        raw = self._settings.get(key, default)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return default

    def backend(self, vendor: str) -> Backend:
        if vendor not in self._backends:
            self._backends[vendor] = make_backend(vendor, self._settings)
        return self._backends[vendor]

    def candidates(self, capability: str = "search", query: str = "") -> list[Candidate]:
        """Routable backends in tier order, then preference, then latency EMA."""
        order = tier_order(self._settings)
        qclass = classify_query(query) if query else "general"
        out: list[Candidate] = []
        for vendor in self._vendors:
            spec_ok = True
            try:
                backend = self.backend(vendor)
            except Exception:  # noqa: BLE001 - one broken adapter must not break routing
                continue
            spec = backend.spec
            if capability == "search" and not spec.search:
                spec_ok = False
            if capability == "extract" and not spec.extract:
                spec_ok = False
            if not spec_ok:
                continue
            if backend.mode() == "unavailable":
                continue
            plan = backend_plan(self._settings, vendor)
            tier = backend.tier(plan)
            if tier not in order:
                continue
            if self._store is not None and not self._store.is_available(vendor):
                continue
            out.append(Candidate(backend=backend, tier=tier, plan=plan))

        def sort_key(candidate: Candidate) -> tuple:
            ema = None
            if self._store is not None:
                ema = self._store.backend_state(candidate.id).latency_ema_ms
            preferred = 0 if qclass in PREFERRED_FOR.get(candidate.id, ()) else 1
            return (
                order.index(candidate.tier),
                preferred,
                ema if ema is not None else float("inf"),
                candidate.id,
            )

        return sorted(out, key=sort_key)

    def skip_reasons(self, capability: str = "search") -> list[str]:
        """Why each unroutable backend is out, for the total-budget error and the pane."""
        reasons: list[str] = []
        order = tier_order(self._settings)
        for vendor in self._vendors:
            try:
                backend = self.backend(vendor)
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"{vendor}: adapter failed to load ({exc})")
                continue
            spec = backend.spec
            if (capability == "search" and not spec.search) or (
                capability == "extract" and not spec.extract
            ):
                reasons.append(f"{vendor}: no {capability} capability")
                continue
            missing = backend.missing_message()
            if missing:
                reasons.append(f"{vendor}: {missing}")
                continue
            tier = backend.tier(backend_plan(self._settings, vendor))
            if tier not in order:
                reasons.append(f"{vendor}: tier {tier!r} is disabled by tier_order")
                continue
            if self._store is not None:
                blocked = self._store.circuit_reason(vendor)
                if blocked:
                    reasons.append(f"{vendor}: {blocked}")
        return reasons

    # ── search ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
    ) -> dict[str, Any]:
        """Run one query through the ladder of backends. Never raises."""
        if not isinstance(query, str) or not query.strip():
            return {"success": False, "error": "sluice: empty query"}
        safe_limit = coerce_limit(limit)
        qclass = classify_query(query)
        namespace = NS_SEARCH_NEWS if qclass == "news" else NS_SEARCH_GENERAL
        ttl = self._int_setting(
            "search_ttl_news" if qclass == "news" else "search_ttl_general",
            300 if qclass == "news" else 900,
        )
        key = search_cache_key(query, safe_limit, freshness or "", qclass)

        if self._store is not None:
            hit = self._store.cache_get(namespace, key)
            # A cached answer from a backend that has since been disabled (key removed,
            # keyless turned off, circuit open) is stale policy, not a result.
            if hit is not None and not self._routable(hit.get("backend")): 
                hit = None
            if hit is not None:
                rows = []
                for row in hit.get("rows") or []:
                    cached_row = dict(row)
                    cached_row.update(
                        backend=hit.get("backend"),
                        tier=hit.get("tier"),
                        cached=True,
                        fallback_used=False,
                        elapsed_ms=0,
                    )
                    rows.append(cached_row)
                return {"success": True, "data": {"web": rows}}

        try:
            candidates = self.candidates("search", query)
        except Exception as exc:  # noqa: BLE001 - routing never raises to the tool layer
            return {"success": False, "error": f"sluice: could not build the backend list ({exc})"}

        per_backend = self._int_setting("per_backend_budget_ms", PER_BACKEND_BUDGET_MS) / 1000.0
        total = self._int_setting("total_budget_ms", TOTAL_BUDGET_MS) / 1000.0
        started = self._clock()
        reasons: list[str] = []
        attempted = 0

        for candidate in candidates:
            if self._store is not None:
                blocked = self._store.circuit_reason(candidate.id)
                if blocked:
                    reasons.append(f"{candidate.id}: {blocked}")
                    continue
            remaining = total - (self._clock() - started)
            if remaining <= 0:
                reasons.append(f"{candidate.id}: total budget of {int(total * 1000)} ms exhausted")
                break
            # A backend whose transport is inherently slower than the global window asks for
            # its own (``spec.budget_ms``); that only ever widens this attempt, and the total
            # budget below still bounds it.
            window = max(per_backend, int(getattr(candidate.backend.spec, "budget_ms", 0) or 0) / 1000.0)
            timeout = min(window, remaining)
            attempt_started = self._clock()
            result = self._call_search(candidate, query, safe_limit, freshness, timeout)
            elapsed_ms = round(max(0.0, self._clock() - attempt_started) * 1000, 1)

            if result.get("ok"):
                rows = self._stamp_rows(
                    result.get("results") or [],
                    candidate,
                    cached=False,
                    fallback_used=attempted > 0,
                    elapsed_ms=elapsed_ms,
                )
                if self._store is not None:
                    self._store.record_success(
                        candidate.id,
                        latency_ms=elapsed_ms,
                        kind="search",
                        cost_usd=float(result.get("cost_usd") or candidate.backend.cost_per_call()),
                    )
                    self._store.cache_put(
                        namespace,
                        key,
                        {
                            "rows": [_strip_meta(row) for row in rows],
                            "backend": candidate.id,
                            "tier": candidate.tier,
                        },
                        ttl,
                    )
                return {"success": True, "data": {"web": rows}}

            attempted += 1
            reasons.append(f"{candidate.id}: {result.get('error') or 'failed'}")
            if self._store is not None:
                self._store.record_failure(
                    candidate.id,
                    error=str(result.get("error") or "search failed"),
                    quota=bool(result.get("quota")),
                    latency_ms=elapsed_ms,
                    kind="search",
                )

        reasons.extend(f"not routable — {reason}" for reason in self.skip_reasons("search"))
        detail = "; ".join(reasons) if reasons else "no backend is configured or routable"
        return {
            "success": False,
            "error": f"sluice: no backend answered for {query!r}. {detail}",
        }

    def _routable(self, vendor: str | None) -> bool:
        """Whether *vendor* could answer right now; used to validate a cached answer."""
        if not vendor:
            return False
        try:
            backend = self.backend(vendor)
        except Exception:  # noqa: BLE001
            return False
        if backend.mode() == "unavailable":
            return False
        if backend.tier(backend_plan(self._settings, vendor)) not in tier_order(self._settings):
            return False
        return self._store is None or self._store.is_available(vendor)

    def _call_search(
        self,
        candidate: Candidate,
        query: str,
        limit: int,
        freshness: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        try:
            result = candidate.backend.search(
                query, limit, freshness=freshness, timeout=timeout
            )
        except NotImplementedError:
            return {"ok": False, "error": f"{candidate.id} has no search adapter"}
        except Exception as exc:  # noqa: BLE001 - adapter contract guard
            return {"ok": False, "error": f"{candidate.id} raised: {exc}"}
        if not isinstance(result, dict):
            return {"ok": False, "error": f"{candidate.id} returned {type(result).__name__}"}
        return result

    def _stamp_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        candidate: Candidate,
        *,
        cached: bool,
        fallback_used: bool,
        elapsed_ms: float,
    ) -> list[dict[str, Any]]:
        stamped: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            stamped.append(
                {
                    **dict(row),
                    "position": row.get("position") or index + 1,
                    "backend": candidate.id,
                    "tier": candidate.tier,
                    "cached": cached,
                    "fallback_used": fallback_used,
                    "elapsed_ms": elapsed_ms,
                }
            )
        return stamped

    # ── extract ─────────────────────────────────────────────────────────────────

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        """Per-URL extraction through the local ladder, cached and isolated per URL.

        DESIGN.md:175-183: extraction is local only, so no paid extract API is called here.
        Vendor extract adapters exist for ``hermes sluice test --backend <id> --extract`` and
        for the probe surface; the router itself never spends money on a page fetch.
        """
        fmt = str(kwargs.get("format") or "")
        items: list[dict[str, Any]] = []
        for url in [str(item) for item in urls]:
            items.append(self._extract_one(url, fmt))
        return items

    def _extract_one(self, url: str, fmt: str) -> dict[str, Any]:
        key = extract_cache_key(url, fmt)
        if self._store is not None:
            for namespace in (NS_EXTRACT, NS_EXTRACT_NEGATIVE):
                hit = self._store.cache_get(namespace, key)
                if hit is not None and isinstance(hit.get("item"), dict):
                    cached = dict(hit["item"])
                    cached.update(cached=True, fallback_used=False, elapsed_ms=0)
                    return cached

        started = self._clock()
        try:
            from .ladder import fetch as ladder_fetch

            item = ladder_fetch(url, store=self._store, settings=self._settings)
        except Exception as exc:  # noqa: BLE001 - one URL never fails the batch
            item = {
                "url": url,
                "title": "",
                "content": "",
                "error": f"sluice: extract failed: {exc}",
                "block_class": "error",
                "stage": "router",
                "stages": [],
                "blocked_by_policy": False,
            }
        elapsed_ms = round(max(0.0, self._clock() - started) * 1000, 1)
        item = {
            **item,
            "backend": "ladder",
            "tier": "local",
            "cached": False,
            "fallback_used": False,
            "elapsed_ms": elapsed_ms,
        }
        if self._store is not None:
            failed = bool(item.get("error"))
            namespace = NS_EXTRACT_NEGATIVE if failed else NS_EXTRACT
            ttl = self._int_setting("negative_ttl", 60) if failed else self._int_setting("extract_ttl", 3600)
            self._store.cache_put(
                namespace, key, {"item": _strip_meta(item)}, ttl
            )
        return item


def _strip_meta(row: Mapping[str, Any]) -> dict[str, Any]:
    """Row/item as cached: everything except the per-request metadata keys."""
    return {key: value for key, value in row.items() if key not in ("cached", "elapsed_ms", "fallback_used")}
