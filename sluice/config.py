"""Settings, credentials and the plugin data directory, resolved in one place.

The manifest (``plugin.yaml config_schema``) is the user-facing schema; this module is the
runtime view of it. Defaults here must match the manifest exactly, and ``tests/test_config.py``
asserts that they do, so the two cannot drift.

Credentials are read, never written. A key set through Settings ▸ Plugins lands in Hermes'
``.env`` and is visible through ``agent.web_search_provider.get_provider_env`` (config-aware,
profile-scope aware). Outside Hermes the same names are read from ``os.environ`` so the
adapters and the CLI work in a bare virtualenv.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from .backends import BACKEND_MODULES

#: Vendors with a published free allowance default to the free tier; the rest to paid
#: (DESIGN.md:161-165). One key is used either way; the plan only changes routing order.
FREE_PLAN_VENDORS: frozenset[str] = frozenset({"brave", "tavily", "exa", "firecrawl"})


def default_plan(backend_id: str) -> str:
    return "free" if backend_id in FREE_PLAN_VENDORS else "paid"


def plan_key(backend_id: str) -> str:
    return f"plan_{backend_id}"


def plan_settings() -> dict[str, str]:
    """One ``plan_<vendor>`` per keyed vendor: the plan only orders a keyed backend."""
    return {
        plan_key(vendor): default_plan(vendor)
        for vendor in BACKEND_MODULES
        if vendor not in ("searxng", "ddgs")
    }


#: Every setting declared in ``plugin.yaml config_schema`` with its default. The plan
#: keys are per-vendor (`plan_exa`, `plan_brave`, ...) so the pane's toggle writes one key.
DEFAULTS: dict[str, Any] = {
    "tier_order": "local,keyless,free,paid",
    "keyless_enabled": True,
    "per_backend_budget_ms": 2500,
    "total_budget_ms": 8000,
    "search_ttl_general": 900,
    "search_ttl_news": 300,
    "extract_ttl": 3600,
    "negative_ttl": 60,
    "domain_memory_ttl": 86400,
    "circuit_failures": 3,
    "circuit_open_seconds": 900,
    "circuit_quota_seconds": 3600,
    "cache_max_mb": 200,
    "searxng_url": "",
    **plan_settings(),
    #: Not in the manifest: the local Camofox REST server address, overridable by
    #: ``CAMOFOX_URL`` like the official plugin does.
    "camofox_url": "http://localhost:9377",
}

#: Secrets declared by the manifest, in the same order (PLAN.md:49-52).
SECRET_ENVS: tuple[str, ...] = (
    "EXA_API_KEY",
    "PARALLEL_API_KEY",
    "FIRECRAWL_API_KEY",
    "TAVILY_API_KEY",
    "KEENABLE_API_KEY",
    "BRAVE_API_KEY",
    "PERPLEXITY_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "TINYFISH_API_KEY",
)

#: Not a Sluice backend: Browserbase is a browser-engine choice Hermes owns.
EXTRA_ENVS: tuple[str, ...] = ("BROWSERBASE_API_KEY", "CAMOFOX_URL")

#: The manifest name, also the plugin id Hermes stores settings under.
PLUGIN_ID = "sluice"


def saved_settings() -> dict[str, Any]:
    """Values written by Settings ▸ Plugins (``plugins.entries.sluice.settings``).

    Read-only and Hermes-optional: outside Hermes the CLI and tests run on defaults plus
    the environment, exactly as they did before this function existed.
    """
    try:
        from hermes_cli.config import load_config_readonly
    except ImportError:
        return {}
    try:
        config = load_config_readonly() or {}
        plugins = config.get("plugins")
        entries = plugins.get("entries") if isinstance(plugins, MutableMapping) else None
        entry = entries.get(PLUGIN_ID) if isinstance(entries, MutableMapping) else None
        values = entry.get("settings") if isinstance(entry, MutableMapping) else None
    except Exception:  # noqa: BLE001 - a config read must never break a search
        return {}
    return dict(values) if isinstance(values, MutableMapping) else {}


def secret(env: str) -> str:
    """Resolve one credential without ever raising. Empty string when it is not set."""
    try:
        from agent.web_search_provider import get_provider_env
    except ImportError:
        return (os.environ.get(env) or "").strip()
    try:
        return (get_provider_env(env) or "").strip()
    except Exception:  # noqa: BLE001 - existence checks must not raise
        return ""


def load_settings(ctx: Any = None) -> dict[str, Any]:
    """Defaults, then the user's plugin settings, then credentials and env overrides.

    ``ctx.get_config(key, default)`` returns the default when the user has not set the key,
    so a missing value keeps the manifest default instead of becoming ``None``. Without a
    context (the CLI, the dashboard API, the desktop pane's backend) the same values are
    read straight from Hermes' config file — ``plugins.entries.sluice.settings`` — so every
    surface sees one configuration.
    """
    settings: dict[str, Any] = dict(DEFAULTS)

    if ctx is not None:
        getter = getattr(ctx, "get_config", None)
        if callable(getter):
            for key, default in list(settings.items()):
                try:
                    value = getter(key, default)
                except Exception:  # noqa: BLE001 - a broken context must not kill a search
                    continue
                if value is not None:
                    settings[key] = value
    else:
        for key, value in saved_settings().items():
            if key in settings and value is not None:
                settings[key] = value

    for env in SECRET_ENVS:
        settings[env] = secret(env)
    for env in EXTRA_ENVS:
        value = secret(env)
        if value:
            settings[env] = value
    if not settings.get("camofox_url"):
        settings["camofox_url"] = os.environ.get("CAMOFOX_URL", DEFAULTS["camofox_url"])

    settings["tier_order"] = _normalize_tier_order(settings.get("tier_order"))
    settings["keyless_enabled"] = _as_bool(settings.get("keyless_enabled"), True)
    return settings


def _normalize_tier_order(raw: Any) -> str:
    """Keep only known tiers, in the user's order, and always append what is missing."""
    known = ("local", "keyless", "free", "paid")
    parts = [part.strip().lower() for part in str(raw or "").split(",")]
    ordered = [part for part in parts if part in known]
    ordered.extend(tier for tier in known if tier not in ordered)
    return ",".join(dict.fromkeys(ordered))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    return default


def tier_order(settings: Mapping[str, Any]) -> list[str]:
    return [tier for tier in _normalize_tier_order(settings.get("tier_order")).split(",") if tier]


def backend_plan(settings: Mapping[str, Any], backend_id: str) -> str:
    raw = str(settings.get(plan_key(backend_id)) or default_plan(backend_id)).strip().lower()
    return raw if raw in ("free", "paid") else default_plan(backend_id)


def configured_backends(
    settings: Mapping[str, Any], *, capability: str | None = None
) -> list[str]:
    """Backend ids that have what they need to run, without touching the network.

    ``capability`` filters to ``search`` or ``extract``; unknown ids are ignored.
    """
    from .backends import specs

    usable: list[str] = []
    for vendor, spec in specs().items():
        if capability == "search" and not spec.search:
            continue
        if capability == "extract" and not spec.extract:
            continue
        if spec.secret_env and not settings.get(spec.secret_env):
            if "keyless" not in spec.tiers or not settings.get("keyless_enabled", True):
                continue
        if spec.id == "searxng" and not str(settings.get("searxng_url") or "").strip():
            continue
        usable.append(vendor)
    return usable


def plugin_data_dir(ctx: Any = None) -> Path:
    """``ctx.plugin_data_dir()`` when the host offers it, else Hermes' plugin-data home."""
    getter = getattr(ctx, "plugin_data_dir", None) if ctx is not None else None
    if callable(getter):
        try:
            return Path(getter())
        except Exception:  # noqa: BLE001 - fall through to the convention
            pass
    from .store import default_data_dir

    return default_data_dir()


def apply_overrides(base: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Copy *base* and apply *overrides*; small helper for tests and the plugin API."""
    merged: MutableMapping[str, Any] = dict(base)
    merged.update(overrides)
    return dict(merged)
