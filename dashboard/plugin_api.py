"""Sluice dashboard API — the FastAPI wrapper, mounted at ``/api/plugins/sluice/``.

PLAN.md:127-134 fixes the route list: ``GET /status``, ``POST /probe``, ``POST /test``,
``POST /install``, ``GET /jobs/{id}``, ``POST /cache/clear``. Two support routes are added
because the desktop pane needs them to work without shell access: ``GET/POST /settings``
(the pane's Settings page writes through Hermes' own plugin-settings writer) and
``POST /setup`` (the two ``hermes config set`` routes, run only with ``apply: true``).

Every handler is a thin wrapper over ``sluice.api`` — the same functions the tests exercise
without FastAPI. A handler never raises out to the client: an operational failure is a
JSON body with ``ok: false``, which is the plugin contract the dashboard documents.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body

log = logging.getLogger(__name__)

#: The plugin directory this wrapper belongs to (``<plugin root>/dashboard/plugin_api.py``).
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _bind_api() -> Any:
    """The ``sluice.api`` module this wrapper belongs to.

    Hermes imports a directory plugin as ``hermes_plugins.<slug>`` with the plugin directory
    as the package ``__path__`` (``plugins_loader._load_directory_module``,
    ``plugin_host_child._import_plugin``) and does not put that directory on ``sys.path``. A
    bare ``import sluice`` then either fails outright (the normal directory install) or
    resolves to a pip-installed copy — a second module object for the same files, whose
    ``hostctx`` is not the one ``register()`` parked (F5: every ``POST /settings`` answered
    "no Hermes plugin context"). So bind the copy the host already loaded, and only then
    fall back to an installed package, then to the tree next to this file.
    """
    entry_init = _PLUGIN_ROOT / "__init__.py"
    candidates: list[str] = []
    for name, module in list(sys.modules.items()):
        if "." not in name:
            # Hermes loads a directory plugin under its ``hermes_plugins.<slug>`` namespace;
            # a bare top-level module named after the plugin directory is not that copy (a
            # test runner or an ``import sluice`` from the repo directory leaves one behind).
            continue
        source = getattr(module, "__file__", None)
        if not source:
            continue
        try:
            if Path(source).resolve() != entry_init:
                continue
        except OSError:  # a stale ``__file__`` for a removed path
            continue
        candidates.append(name)
    for name in candidates:
        try:
            return importlib.import_module(f"{name}.sluice.api")
        except ModuleNotFoundError as exc:
            if (exc.name or "") != f"{name}.sluice":
                raise
            continue  # that module is not the plugin package
    try:
        return importlib.import_module("sluice.api")
    except ModuleNotFoundError as exc:
        if (exc.name or "") != "sluice":
            raise
    sys.path.insert(0, str(_PLUGIN_ROOT))
    try:
        return importlib.import_module("sluice.api")
    finally:
        sys.path.remove(str(_PLUGIN_ROOT))


api = _bind_api()

router = APIRouter()


def _safe(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run one payload function, folding any failure into the no-data contract."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a plugin handler must never raise to the client
        log.warning("sluice plugin api: %s failed: %s", getattr(fn, "__name__", fn), exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.get("/status")
def status() -> dict[str, Any]:
    """Backend table, cache state, per-domain block report and browser engines."""
    return _safe(api.status_payload)


@router.post("/probe")
def probe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Probe one backend (``{backend: id}``) or every configured one."""
    backend = payload.get("backend") if isinstance(payload, dict) else None
    return _safe(api.probe_payload, backend)


@router.post("/test")
def test(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Run one query, optionally pinned to one backend.

    The body fields go in raw: coercion and validation live in ``sluice.api``, so a
    malformed field becomes a JSON envelope instead of raising before ``_safe`` runs.
    """
    body = payload if isinstance(payload, dict) else {}
    return _safe(
        api.test_payload,
        body.get("query"),
        backend_id=body.get("backend") or None,
        limit=body.get("limit", 5),
    )


@router.post("/install")
def install(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Start an install job (``{target: playwright|camofox|browserbase}``) or stop camofox."""
    body = payload if isinstance(payload, dict) else {}
    return _safe(
        api.install_payload,
        str(body.get("target") or ""),
        action=str(body.get("action") or "install"),
    )


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    """Job status; the pane polls this instead of holding a request open (PLAN.md:131)."""
    return _safe(api.job_payload, job_id)


@router.post("/cache/clear")
def cache_clear(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Clear the Sluice cache and Hermes' outer ``cache/web`` directory."""
    return _safe(api.cache_clear_payload)


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """Editable settings, current values and per-backend plans."""
    return _safe(api.settings_payload)


@router.post("/settings")
def post_settings(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Write settings through ``ctx.set_config`` (F5: public plugin API; secrets refused)."""
    body = payload if isinstance(payload, dict) else {}
    values = body.get("values") if isinstance(body.get("values"), dict) else body
    return _safe(api.save_settings_payload, values)


@router.post("/setup")
def setup(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Print, or apply, the three ``hermes config set`` routes."""
    body = payload if isinstance(payload, dict) else {}
    return _safe(api.setup_payload, apply=bool(body.get("apply")))
