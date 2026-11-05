"""The single ``WebSearchProvider`` Sluice registers with Hermes.

Three contracts meet in this file:

* PLAN.md:53 — the provider class is the only thing ``register(ctx)`` wires besides the
  CLI command.
* DESIGN.md:126-134 — one provider named ``sluice``, advertising both search and extract;
  keys resolve through ``agent.secret_scope`` so profile multiplexing is respected, and no
  Hermes-internal provider module is imported.
* DESIGN.md:68-72 — search returns ``{"success": True, "data": {"web": [...]}}``, extract
  returns a per-URL list, failure is ``{"success": False, "error": str}``, and nothing
  raises across the boundary.

Scope at T1: the class, its capability flags and the failure envelopes. The router that
fills them is T3 (``sluice/router.py``), so ``is_available()`` answers ``False`` — a
provider that reported itself ready while every call failed would light the native
``web_search``/``web_extract`` tools for a backend with nothing behind them. T3 replaces
that method with the policy in DESIGN.md:83-88.
"""

from __future__ import annotations

import os
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


PROVIDER_NAME = "sluice"

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

_NOT_WIRED = (
    "{surface} is registered but not routed yet: {task} implements it. "
    "See REQUIREMENTS.md for the acceptance criteria."
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
        # The PluginContext is held for settings reads (ctx.get_config) by later tasks; it
        # is never used to write Hermes-level config, which goes through `hermes config set`
        # (DESIGN.md:59-64).
        self._ctx = ctx

    # ── capability flags ────────────────────────────────────────────────────────────

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def is_available(self) -> bool:
        """False until routing exists (T3). Cheap: no network (DESIGN.md:83-88, T3)."""
        return False

    def is_keyless_available(self) -> bool:
        """False until routing exists (T3). DESIGN.md:83-88 sets the real policy."""
        return False

    def get_setup_schema(self) -> Dict[str, Any]:
        """Row for the ``hermes tools`` picker: no key prompt, keys live in Settings."""
        return {
            "name": "Sluice",
            "badge": "free-first",
            "tag": "routes twelve backends; keys are set in Settings ▸ Plugins",
            "env_vars": [],
        }

    # ── tool surface ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Return a failure envelope instead of raising across the provider boundary."""
        if not isinstance(query, str) or not query.strip():
            return {"success": False, "error": "sluice: empty query"}
        return {"success": False, "error": _NOT_WIRED.format(surface="search", task="T3 (router)")}

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        """Per-URL failure items; one bad URL must never fail the batch (DESIGN.md:68-72)."""
        reason = _NOT_WIRED.format(surface="extract", task="T7 (fetch ladder)")
        return [{"url": url, "error": reason} for url in urls]
