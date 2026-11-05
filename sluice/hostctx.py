"""The one Hermes handle Sluice keeps: the ``PluginContext`` passed to ``register()``.

F5. Settings writes go through ``ctx.set_config()``, the documented plugin API. Hermes' own
per-field plugin-settings writer does the same thing behind a *private* core module:
importing it couples Sluice to an internal path that can move on any release, and
``hermes plugins validate`` warns about it.

The dashboard's ``plugin_api.py`` has no context of its own, so ``register()`` parks the
context here and the API module reads it back. When no context is parked (the plugin is
loaded by something that never called ``register``, or a test), the caller gets ``None``
and reports that saving needs the plugin loaded by Hermes, rather than guessing at a writer.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["clear_host_ctx", "host_ctx", "host_set_config", "set_host_ctx"]

_CTX: Any = None


def set_host_ctx(ctx: Any) -> None:
    """Remember the context Hermes handed to ``register()``."""
    global _CTX
    _CTX = ctx


def host_ctx() -> Any:
    """The parked context, or ``None`` when nothing registered this process."""
    return _CTX


def host_set_config() -> Callable[[str, Any], Any] | None:
    """``ctx.set_config`` as a plain callable, or ``None`` when no context offers one.

    ``PluginContext.set_config(key, value)`` writes one plugin-relative key under
    ``plugins.entries.<id>.settings``, enforces managed-install and administrator-managed
    refusals, and takes the cross-process lock — everything Sluice needs and nothing it
    should reimplement (DESIGN.md:63-64: never write ``config.yaml`` ourselves).
    """
    setter = getattr(_CTX, "set_config", None)
    return setter if callable(setter) else None


def clear_host_ctx() -> None:
    """Drop the parked context. Tests use it; runtime never calls it."""
    global _CTX
    _CTX = None
