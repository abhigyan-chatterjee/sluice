"""Dashboard/desktop API logic, with no web framework in it.

``dashboard/plugin_api.py`` is the FastAPI wrapper (six routes from PLAN.md:130-131 plus
the settings and setup routes the pane needs); this module is the behaviour, so the same
calls can be unit-tested without FastAPI and reused by a CLI or a test harness.

Rules that hold for every function here:

* nothing raises out — a failure is ``{"ok": False, "error": str}``;
* no secret value is ever returned, only the presence of a key and the env var name;
* one shared SQLite store per data directory, so a 5 s status poll is a handful of indexed
  reads instead of a fresh connection each time (PLAN.md:133-134).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import __version__
from .backends import BACKEND_MODULES, make_backend, specs
from .config import DEFAULTS, backend_plan, configured_backends, load_settings, plugin_data_dir
from .manage import (
    clear_outer_web_cache,
    detect,
    install_plan,
    missing_steps,
    setup_routes,
    start_install_job,
    stop_camofox,
)
from .router import Router
from .store import Store, open_store

#: Settings the pane may edit. Secrets are not here on purpose: keys go through Hermes'
#: credential route and never through a plugin write (REQUIREMENTS.md §4.2).
EDITABLE_KEYS: tuple[str, ...] = (
    "tier_order",
    "keyless_enabled",
    "per_backend_budget_ms",
    "total_budget_ms",
    "search_ttl_general",
    "search_ttl_news",
    "extract_ttl",
    "negative_ttl",
    "domain_memory_ttl",
    "circuit_failures",
    "circuit_open_seconds",
    "circuit_quota_seconds",
    "cache_max_mb",
    "searxng_url",
)

#: How long a browser-engine probe may be reused by ``/status`` (it is an HTTP health call).
DETECTION_TTL_SECONDS = 30.0
#: The health probe inside ``/status`` must fit the 300 ms budget, so it gets a short leash.
STATUS_HEALTH_TIMEOUT = 0.3

_LOCK = threading.RLock()
_STORE: Store | None = None
_STORE_PATH: Path | None = None
_DETECTION: dict[str, Any] = {"at": 0.0, "value": None}
_DETECTION_LOCK = threading.Lock()


# ── shared state ────────────────────────────────────────────────────────────────


def store(settings: Mapping[str, Any] | None = None) -> Store:
    """One ``Store`` per data directory, shared by every route."""
    global _STORE, _STORE_PATH
    path = Path(plugin_data_dir()) / "sluice.db"
    with _LOCK:
        if _STORE is None or _STORE_PATH != path or not path.exists():
            if _STORE is not None:
                try:
                    _STORE.close()
                except Exception:  # noqa: BLE001 - reopening is more useful than raising
                    pass
            _STORE = open_store(path.parent, settings=settings or load_settings())
            _STORE_PATH = path
        return _STORE


def reset() -> None:
    """Close the shared store and drop caches. Used by tests and by ``cache clear``."""
    global _STORE, _STORE_PATH
    with _LOCK:
        if _STORE is not None:
            try:
                _STORE.close()
            except Exception:  # noqa: BLE001
                pass
        _STORE = None
        _STORE_PATH = None
    with _DETECTION_LOCK:
        _DETECTION["at"] = 0.0
        _DETECTION["value"] = None


def _detection(settings: Mapping[str, Any], *, refresh: bool = False) -> Mapping[str, Any]:
    """Cached browser-engine detection, so a 5 s poll is not an HTTP call each time."""
    now = time.time()
    with _DETECTION_LOCK:
        cached = _DETECTION["value"]
        if not refresh and cached is not None and now - float(_DETECTION["at"]) < DETECTION_TTL_SECONDS:
            return cached
        value = detect(settings=settings, health_timeout=STATUS_HEALTH_TIMEOUT).as_dict()
        _DETECTION["value"] = value
        _DETECTION["at"] = now
        return value


def _health(state: Any, missing: str, *, now: float) -> str:
    if missing:
        return "unconfigured"
    if state.quota_exhausted_until > now:
        return "quota"
    if state.circuit_open_until > now:
        return "circuit"
    if state.last_ok_at and (not state.last_error_at or state.last_ok_at >= state.last_error_at):
        return "ok"
    if state.last_error:
        return "error"
    return "idle"


def _plan_values(settings: Mapping[str, Any]) -> dict[str, str]:
    return {
        vendor: backend_plan(settings, vendor)
        for vendor in BACKEND_MODULES
        if vendor not in ("searxng", "ddgs")
    }


# ── GET /status ────────────────────────────────────────────────────────────────


def status_payload(
    *,
    settings: Mapping[str, Any] | None = None,
    state: Store | None = None,
    detection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Backend table, cache state, per-domain block report and browser engines."""
    settings = settings or load_settings()
    state = state or store(settings)
    now = time.time()
    registry = specs()
    backends: list[dict[str, Any]] = []
    for vendor in BACKEND_MODULES:
        spec = registry[vendor]
        backend = make_backend(vendor, settings)
        plan = backend_plan(settings, vendor)
        persisted = state.backend_state(vendor)
        missing = backend.missing_message()
        backends.append(
            {
                "id": vendor,
                "label": spec.label,
                "summary": spec.summary,
                "task": spec.task,
                "declared_tier": spec.cheapest_tier,
                "tiers": list(spec.tiers),
                "plan": plan,
                "tier": backend.tier(plan),
                "mode": backend.mode(),
                "key_env": spec.secret_env,
                "key_present": bool(backend.key),
                "keyless_capable": backend.keyless_capable,
                "search": spec.search,
                "extract": spec.extract,
                "health": _health(persisted, missing, now=now),
                "missing": missing,
                "circuit_open_until": persisted.circuit_open_until,
                "quota_exhausted_until": persisted.quota_exhausted_until,
                "consecutive_failures": persisted.consecutive_failures,
                "latency_p50_ms": persisted.latency_p50_ms,
                "latency_ema_ms": persisted.latency_ema_ms,
                "calls": persisted.calls,
                "failures": persisted.failures,
                "spend_usd": persisted.spend_usd,
                "last_error": persisted.last_error,
                "last_error_at": persisted.last_error_at,
                "last_ok_at": persisted.last_ok_at,
            }
        )
    return {
        "ok": True,
        "plugin": "sluice",
        "version": __version__,
        "generated_at": now,
        "settings": {
            key: settings.get(key, DEFAULTS.get(key))
            for key in (*EDITABLE_KEYS, "camofox_url")
        },
        "backends": backends,
        "totals": {
            "backends": len(backends),
            "configured": sum(1 for item in backends if not item["missing"]),
            "calls": sum(item["calls"] for item in backends),
            "spend_usd": state.total_spend(),
        },
        "cache": state.cache_stats(),
        "domains": state.domain_memory_all(),
        "browsers": detection if detection is not None else _detection(settings),
        "browser_gaps": missing_steps(
            _DetectionView(detection if detection is not None else _detection(settings)), settings=settings
        ),
    }


class _DetectionView:
    """Read a detection payload dict with the ``Detection`` attribute names."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._payload = payload

    def __getattr__(self, name: str) -> Any:
        return self._payload.get(name)


# ── POST /probe ────────────────────────────────────────────────────────────────


def probe_payload(
    backend_id: str | None = None,
    *,
    settings: Mapping[str, Any] | None = None,
    state: Store | None = None,
) -> dict[str, Any]:
    """Probe one backend, or every configured search backend when none is named."""
    settings = settings or load_settings()
    state = state or store(settings)
    if backend_id:
        if backend_id not in BACKEND_MODULES:
            return {"ok": False, "error": f"unknown backend {backend_id!r}"}
        targets = [backend_id]
    else:
        targets = configured_backends(settings, capability="search")

    results: list[dict[str, Any]] = []
    for vendor in targets:
        backend = make_backend(vendor, settings)
        started = time.monotonic()
        result = backend.probe()
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if result.get("ok"):
            state.record_success(vendor, latency_ms=elapsed_ms, kind="probe")
        elif not result.get("skipped"):
            state.record_failure(
                vendor,
                error=str(result.get("error") or "probe failed"),
                quota=bool(result.get("quota")),
                latency_ms=elapsed_ms,
                kind="probe",
            )
        results.append({"backend": vendor, "elapsed_ms": elapsed_ms, **result})
    return {
        "ok": all(item.get("ok") or item.get("skipped") for item in results),
        "probed": len(results),
        "results": results,
    }


# ── POST /test ─────────────────────────────────────────────────────────────────


def test_payload(
    query: str,
    *,
    backend_id: str | None = None,
    limit: int = 5,
    settings: Mapping[str, Any] | None = None,
    state: Store | None = None,
) -> dict[str, Any]:
    """One query, one answer: which backend served it, its tier and the latency."""
    settings = settings or load_settings()
    state = state or store(settings)
    if not isinstance(query, str) or not query.strip():
        return {"ok": False, "error": "query must be a non-empty string"}
    safe_limit = max(1, min(int(limit or 5), 20))

    if backend_id:
        if backend_id not in BACKEND_MODULES:
            return {"ok": False, "error": f"unknown backend {backend_id!r}"}
        backend = make_backend(backend_id, settings)
        missing = backend.missing_message()
        if missing:
            return {"ok": False, "error": missing, "backend": backend_id}
        started = time.monotonic()
        result = backend.search(query, limit=safe_limit)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        plan = backend_plan(settings, backend_id)
        tier = backend.tier(plan)
        if result.get("ok"):
            state.record_success(backend_id, latency_ms=elapsed_ms, kind="test")
        else:
            state.record_failure(
                backend_id,
                error=str(result.get("error") or "search failed"),
                quota=bool(result.get("quota")),
                latency_ms=elapsed_ms,
                kind="test",
            )
        rows = result.get("results") or []
        return {
            "ok": bool(result.get("ok")),
            "backend": backend_id,
            "tier": tier,
            "mode": backend.mode(),
            "latency_ms": elapsed_ms,
            "rows": rows,
            "error": None if result.get("ok") else (result.get("error") or "search failed"),
        }

    started = time.monotonic()
    result = Router(store=state, settings=settings).search(query, safe_limit)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    rows = ((result.get("data") or {}).get("web") or []) if result.get("success") else []
    first = rows[0] if rows else {}
    return {
        "ok": bool(result.get("success")),
        "backend": first.get("backend"),
        "tier": first.get("tier"),
        "mode": first.get("backend") and "routed" or None,
        "latency_ms": first.get("elapsed_ms") or elapsed_ms,
        "rows": rows,
        "error": None if result.get("success") else result.get("error"),
    }


# ── install / jobs / cache / settings / setup ─────────────────────────────────


def install_payload(
    target: str,
    *,
    action: str = "install",
    wait: bool = False,
    settings: Mapping[str, Any] | None = None,
    state: Store | None = None,
) -> dict[str, Any]:
    """Start one install job, or stop the camofox container. Never runs implicitly."""
    settings = settings or load_settings()
    if action not in ("install", "stop"):
        return {"ok": False, "error": f"unknown action {action!r}; expected install or stop"}
    if action == "stop":
        if target != "camofox":
            return {"ok": False, "error": "only the camofox server can be stopped"}
        result = stop_camofox()
        return {"ok": bool(result.get("stopped")), "action": "stop", "target": target, **result}
    try:
        plan = install_plan(target)
    except KeyError as exc:
        return {"ok": False, "error": str(exc.args[0])}
    if not plan["supported"]:
        return {
            "ok": True,
            "started": False,
            "target": target,
            "job": None,
            "note": plan["note"],
            "next": plan["next"],
        }
    state = state or store(settings)
    result = start_install_job(state, target, wait=wait)
    return {"ok": True, "action": "install", **result}


def job_payload(job_id: str, *, settings: Mapping[str, Any] | None = None, state: Store | None = None) -> dict[str, Any]:
    state = state or store(settings or load_settings())
    job = state.get_job(str(job_id))
    if job is None:
        return {"ok": False, "error": f"no job {job_id!r}", "status": 404}
    return {"ok": True, "job": job}


def cache_clear_payload(
    *, settings: Mapping[str, Any] | None = None, state: Store | None = None
) -> dict[str, Any]:
    settings = settings or load_settings()
    state = state or store(settings)
    rows = state.cache_clear()
    outer = clear_outer_web_cache()
    return {"ok": True, "sluice_rows": rows, "outer": outer}


def settings_payload(*, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    return {
        "ok": True,
        "values": {key: settings.get(key, DEFAULTS.get(key)) for key in EDITABLE_KEYS},
        "plans": _plan_values(settings),
        "editable": list(EDITABLE_KEYS),
        "plan_key_pattern": "plan_<vendor>",
        "tier_choices": ["local", "keyless", "free", "paid"],
    }


def save_settings_payload(
    values: Mapping[str, Any],
    *,
    writer: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Validate, then write through Hermes' own plugin-settings writer.

    The writer is the only Hermes code in this module, imported at the point of use. It
    refuses secrets, managed installs and unknown keys — Sluice adds no second writer.
    """
    if not isinstance(values, Mapping) or not values:
        return {"ok": False, "error": "no settings supplied"}
    unknown = [key for key in values if key not in EDITABLE_KEYS and not str(key).startswith("plan_")]
    if unknown:
        return {"ok": False, "error": f"not editable through Sluice: {', '.join(sorted(unknown))}"}
    if writer is None:
        try:
            from hermes_cli.plugins_settings import save_plugin_settings
        except ImportError:
            return {
                "ok": False,
                "error": "Hermes' plugin-settings writer is not importable here; start the dashboard",
            }
        plugin_dir = Path(__file__).resolve().parents[1]

        def writer(payload: Mapping[str, Any]) -> Any:  # type: ignore[misc]
            return save_plugin_settings("sluice", plugin_dir, payload)

    try:
        written = writer(dict(values))
    except (ValueError, PermissionError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "written": list(written or values.keys())}


def setup_payload(*, apply: bool = False) -> dict[str, Any]:
    """The three ``hermes config set`` routes; run only with ``apply``."""
    results = setup_routes(apply=bool(apply))
    return {
        "ok": all(item["ok"] for item in results if item["applied"]) if apply else True,
        "applied": bool(apply),
        "routes": results,
    }


# ``Store`` is referenced in annotations above; re-exported for callers that inject one.
__all__ = [
    "DETECTION_TTL_SECONDS",
    "EDITABLE_KEYS",
    "cache_clear_payload",
    "install_payload",
    "job_payload",
    "probe_payload",
    "reset",
    "save_settings_payload",
    "settings_payload",
    "setup_payload",
    "status_payload",
    "store",
    "test_payload",
]
