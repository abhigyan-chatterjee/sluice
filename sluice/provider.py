"""The single ``WebSearchProvider`` Sluice registers with Hermes.

Three contracts meet in this file:

* PLAN.md:53 — the provider class is the only thing ``register(ctx)`` wires besides the CLI
  command and the bundled skill.
* DESIGN.md:126-134 — one provider named ``sluice-search``, advertising both search and extract;
  keys resolve through ``agent.secret_scope`` so profile multiplexing is respected, and no
  Hermes-internal provider module is imported.
* DESIGN.md:68-72 — search returns ``{"success": True, "data": {"web": [...]}}``, extract
  returns a per-URL list, failure is ``{"success": False, "error": str}``, and nothing
  raises across the boundary.

Availability (DESIGN.md:83-88): ``is_available()`` is true when any keyed backend has
credentials or a local SearXNG URL is configured. ``is_keyless_available()`` is true while
the keyless tier is enabled, even with zero keys, and never makes ``is_available()`` true.
With no keys and keyless disabled both are false, which is honest and lets ``hermes tools``
say so.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict

try:  # Hermes runtime: the real ABC, so isinstance() and the registry agree.
    from agent.web_search_provider import WebSearchProvider as _WebSearchProviderBase
except ModuleNotFoundError as exc:  # standalone install / plain pytest
    # Only a genuinely absent ``agent`` package falls back. A missing transitive
    # dependency inside ``agent`` must surface, not turn into a look-alike provider that
    # the real registry would reject.
    if not str(exc.name or "").startswith("agent"):
        raise

    class _WebSearchProviderBase:  # type: ignore[no-redef]
        """Fallback base used only when Hermes is not importable.

        Mirrors the surface ``agent.web_search_provider.WebSearchProvider`` requires, so
        the provider class can be imported and unit-tested in a plain virtualenv. The
        real ``ProviderBase.name`` is an abstract property; a plain class attribute
        satisfies it the same way.
        """

        name: str = ""
        display_name: str = ""

        def supports_search(self) -> bool:  # pragma: no cover - overridden below
            return True

        def is_available(self) -> bool:  # pragma: no cover - overridden below
            return False

        def is_keyless_available(self) -> bool:  # pragma: no cover - overridden below
            return False

        def supports_extract(self) -> bool:  # pragma: no cover - overridden below
            return False

        def get_setup_schema(self) -> Dict[str, Any]:
            return {"name": self.display_name or self.name, "badge": "", "tag": "", "env_vars": []}


PROVIDER_NAME = "sluice-search"

#: The ten credentials the manifest declares (PLAN.md:49-52). Env-var names, never values.
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


def _secret(name: str) -> str:
    """Resolve one credential, profile-scope aware, without ever raising.

    Uses Hermes' own config-aware lookup (``get_provider_env``) when available so a key set
    through Settings ▸ Plugins is visible in gateway sessions and delegate children. Under
    profile multiplexing that lookup can raise ``UnscopedSecretError`` when no scope is
    bound; an availability check must not raise, and an unreadable key is treated as absent.
    """
    try:
        from agent.web_search_provider import get_provider_env
    except ImportError:
        return (os.environ.get(name) or "").strip()
    try:
        return get_provider_env(name) or ""
    except Exception:  # noqa: BLE001 - never raise from an availability path
        return ""


def configured_secrets() -> Dict[str, bool]:
    """Presence (never the value) of each declared credential. No network, no side effects."""
    return {env: bool(_secret(env)) for env in SECRET_ENVS}


class SluiceWebSearchProvider(_WebSearchProviderBase):
    """One provider owning both native web tools, routing across twelve backends."""

    name = PROVIDER_NAME
    display_name = "Sluice"

    def __init__(self, ctx: Any = None) -> None:
        # The PluginContext is held for settings reads (ctx.get_config). It is never used to
        # write Hermes-level config, which goes through `hermes config set` (DESIGN.md:59-64).
        self._ctx = ctx
        self._router: Any = None
        self._settings: Dict[str, Any] | None = None
        self._store: Any = None
        self._lock = threading.Lock()

    # ── capability flags ────────────────────────────────────────────────────────────

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def settings(self) -> Dict[str, Any]:
        """Resolved settings; cheap enough for the ``hermes tools`` paint, and cached."""
        if self._settings is None:
            from .config import load_settings

            self._settings = load_settings(self._ctx)
        return self._settings

    def is_available(self) -> bool:
        """True when a keyed backend has credentials or a local SearXNG is configured.

        Cheap and offline: it reads settings and environment only (DESIGN.md:83-88).
        """
        try:
            settings = self.settings()
        except Exception:  # noqa: BLE001 - never raise from an availability check
            return False
        if str(settings.get("searxng_url") or "").strip():
            return True
        return any(bool(str(settings.get(env) or "").strip()) for env in SECRET_ENVS)

    def is_keyless_available(self) -> bool:
        """True while the keyless tier is enabled, even with zero keys.

        This must never make :meth:`is_available` true: a keyless provider is not a
        configured one (DESIGN.md:86-88, REQUIREMENTS.md §4.1).
        """
        try:
            return bool(self.settings().get("keyless_enabled", True))
        except Exception:  # noqa: BLE001
            return True

    def get_setup_schema(self) -> Dict[str, Any]:
        """Row for the ``hermes tools`` picker: no key prompt, keys live in Settings."""
        return {
            "name": "Sluice",
            "badge": "free-first",
            "tag": "routes twelve backends; keys are set in Settings ▸ Plugins",
            "env_vars": [],
        }

    # ── routing ─────────────────────────────────────────────────────────────────────

    def router(self):
        """Build the router (and its store) on first use, once per provider instance."""
        if self._router is not None:
            return self._router
        with self._lock:
            if self._router is not None:
                return self._router
            from .config import plugin_data_dir
            from .router import Router
            from .store import open_store

            settings = self.settings()
            try:
                self._store = open_store(plugin_data_dir(self._ctx), settings=settings)
            except Exception:  # noqa: BLE001 - a read-only data dir must not kill search
                self._store = None
            self._router = Router(store=self._store, settings=settings)
            return self._router

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                try:
                    self._store.close()
                except Exception:  # noqa: BLE001
                    pass
            self._store = None
            self._router = None

    # ── tool surface ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Return a failure envelope instead of raising across the provider boundary."""
        if not isinstance(query, str) or not query.strip():
            return {"success": False, "error": "sluice-search: empty query"}
        try:
            return self.router().search(query, limit)
        except Exception as exc:  # noqa: BLE001 - the tool layer never sees a traceback
            return {"success": False, "error": f"sluice-search: search failed: {exc}"}

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        """Per-URL failure items; one bad URL must never fail the batch (DESIGN.md:68-72)."""
        targets = [str(url) for url in urls] if urls else []
        try:
            return self.router().extract(targets, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return [{"url": url, "error": f"sluice-search: extract failed: {exc}"} for url in targets]
