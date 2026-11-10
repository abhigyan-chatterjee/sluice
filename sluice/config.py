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
from urllib.parse import urlsplit

import httpx

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


#: Schemes a SearXNG instance can be reached on. Anything else is refused before a request.
SEARXNG_SCHEMES: tuple[str, ...] = ("http", "https")

#: A SearXNG instance address is a scheme, a host, an optional port and at most a short path
#: prefix. httpx refuses a request URL past its 64 KiB cap with ``InvalidURL``, which is not
#: an ``httpx.HTTPError`` and would escape the adapter boundary; the bound also leaves room
#: for the query the adapter appends.
MAX_SEARXNG_URL_LENGTH = 2048


def _clip(text: str, limit: int = 120) -> str:
    """Shorten a value echoed in a refusal, so a hostile length cannot flood the reason."""
    return text if len(text) <= limit else f"{text[:limit]}..."


def normalize_searxng_url(raw: Any) -> tuple[str, str]:
    """Return ``(base_url, error)`` for one SearXNG instance address.

    An empty value returns ``("", "")``: not configured is not a mistake. A value that is not
    an absolute http(s) URL with a host is dropped and the second element says why, so the
    router never builds a request from it and the user gets the reason instead of httpx's own
    failure long after they typed the address. Trailing slashes are removed so
    ``base + "/search"`` can never double one.

    The port is read here on purpose: ``urlsplit`` parses it lazily, so ``http://host:abc``
    and a port past 65535 pass the split and only raise when ``parts.port`` is touched.
    Left unread they reach ``httpx``, whose ``InvalidURL`` is not an ``httpx.HTTPError`` and
    so escapes the adapter boundary instead of being refused with a reason (F4 residual).

    The address is then handed to ``httpx.URL`` itself. ``parts.hostname`` only proves the
    host is non-empty; httpx IDNA-encodes it and refuses what it cannot encode (a zero-width
    space, an NBSP, a non-breaking hyphen, an emoji, a combining mark) with the same
    ``InvalidURL``. Asking httpx here keeps the validator and the request in agreement.
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""
    if len(text) > MAX_SEARXNG_URL_LENGTH:
        return "", (
            f"SEARXNG_URL is {len(text)} characters long; the limit is "
            f"{MAX_SEARXNG_URL_LENGTH}. A SearXNG address is a scheme, a host, an optional "
            f"port and at most a short path prefix (got {_clip(text)!r})."
        )
    if "://" not in text:
        return "", (
            f"SEARXNG_URL must be a full URL including the scheme, for example "
            f"http://127.0.0.1:8888 (got {text!r}): a bare host and port cannot be routed."
        )
    try:
        parts = urlsplit(text)
        _ = parts.port
    except ValueError as exc:
        return "", f"SEARXNG_URL is not a parseable URL: {text!r} ({exc})"
    scheme = parts.scheme.lower()
    if scheme not in SEARXNG_SCHEMES:
        return "", (
            f"SEARXNG_URL must use http or https (got scheme {parts.scheme!r} in {text!r})."
        )
    # ``http://:8888`` has a netloc but no host, and an unbracketed IPv6 literal
    # (``http://::1:8888``) gives urlsplit no hostname either: both would reach httpx as a
    # request to nowhere.
    if not parts.netloc or not parts.hostname:
        return "", f"SEARXNG_URL has no host: {text!r}."
    # httpx refuses an ASCII control character anywhere in the URL with ``InvalidURL``, which
    # the adapter boundary does not catch; ``urlsplit`` keeps them (it only deletes CR/LF/TAB).
    bad = next((char for char in f"{parts.netloc}{parts.path}" if char.isascii() and not char.isprintable()), "")
    if bad:
        return "", f"SEARXNG_URL contains a non-printable ASCII character {bad!r}: {text!r}."
    if parts.query or parts.fragment:
        return "", (
            f"SEARXNG_URL must point at the instance root or a path prefix, without a query "
            f"or fragment: {text!r}."
        )
    base = f"{scheme}://{parts.netloc}{parts.path}".rstrip("/")
    try:
        # The exact string ``SearxngBackend.search`` builds, not a reconstruction of it.
        httpx.URL(f"{base}/search")
    except (httpx.InvalidURL, UnicodeError) as exc:
        return "", (
            f"SEARXNG_URL is not an address httpx can request ({exc}): {_clip(text)!r}."
        )
    return base, ""


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
    # ``SEARXNG_URL`` is how the CLI, the adapter messages and docker/searxng name the
    # control; the plugin setting wins when both are set, so an explicit value is never
    # shadowed by a stale shell export. Either way the value is validated once, here, and a
    # rejected address is dropped with its reason kept beside it (``searxng_url_error``) so
    # every surface can explain what is wrong instead of reporting "not set" (F4).
    searxng_raw = str(settings.get("searxng_url") or "").strip() or os.environ.get("SEARXNG_URL", "").strip()
    settings["searxng_url"], settings["searxng_url_error"] = normalize_searxng_url(searxng_raw)

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


def manifest_schema() -> dict[str, dict[str, Any]]:
    """``config_schema`` from the shipped ``plugin.yaml``, keyed by setting name.

    The manifest is the one place a setting's human label and explanation are written, so
    the desktop pane reads them from here instead of hardcoding a second copy that drifts.
    Returns ``{}`` when the manifest cannot be read: a missing label is a cosmetic loss and
    must never stop the pane from rendering.
    """
    try:
        import yaml

        path = Path(__file__).resolve().parents[1] / "plugin.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a cosmetic lookup never breaks the pane
        return {}
    schema = data.get("config_schema")
    if not isinstance(schema, dict):
        return {}
    return {str(key): value for key, value in schema.items() if isinstance(value, dict)}
