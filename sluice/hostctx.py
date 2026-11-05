"""The one Hermes handle Sluice keeps: the ``PluginContext`` passed to ``register()``.

F5. Settings writes go through ``ctx.set_config()``, the documented plugin API. Hermes' own
per-field plugin-settings writer does the same thing behind a *private* core module:
importing it couples Sluice to an internal path that can move on any release, and
``hermes plugins validate`` warns about it.

The dashboard's ``plugin_api.py`` has no context of its own, so ``register()`` parks the
context here and the API module reads it back. The parking is per process, not per module
object: Hermes loads a directory plugin as ``hermes_plugins.<slug>`` over the plugin
directory, while the dashboard wrapper is mounted by file path and reaches ``sluice.api``
by another name, so there can be two module objects for this one file. See :func:`_state`.
When no context is parked (the plugin is loaded by something that never called ``register``,
or a test), the caller gets ``None`` and reports that saving needs the plugin loaded by
Hermes, rather than guessing at a writer.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Callable

__all__ = ["clear_host_ctx", "host_ctx", "host_set_config", "set_host_ctx"]

#: ``sys.modules`` key of the one state holder in this process. Not a real module: it exists
#: so the copies of this file share a context (see the module docstring).
_STATE_MODULE = "_sluice_hostctx_state"


def _state() -> types.ModuleType:
    """The process-wide holder, created by whichever copy of this file imports first.

    Hermes loads a directory plugin as ``hermes_plugins.<slug>`` with the plugin directory as
    the package ``__path__`` (``plugins_loader._load_directory_module``,
    ``plugin_host_child._import_plugin``), while ``dashboard/plugin_api.py`` is mounted by
    file path and reaches ``sluice.api`` under whatever name it can import. ``register()``
    therefore lands on ``hermes_plugins.sluice.sluice.hostctx``, and a process where the
    wrapper bound another copy reaches ``sluice.hostctx``: two module objects for this one
    file. A module global would be per copy, and the context parked by one copy would stay
    invisible to the other (every ``POST /settings`` answered "no Hermes plugin context"
    while the plugin was loaded and registered). The holder is keyed by name in
    ``sys.modules`` so both copies read and write the same context.
    """
    holder = sys.modules.get(_STATE_MODULE)
    if holder is None:
        holder = types.ModuleType(_STATE_MODULE)
        setattr(holder, "ctx", None)
        sys.modules[_STATE_MODULE] = holder
    return holder


def set_host_ctx(ctx: Any) -> None:
    """Remember the context Hermes handed to ``register()``."""
    setattr(_state(), "ctx", ctx)


def host_ctx() -> Any:
    """The parked context, or ``None`` when nothing registered this process."""
    return getattr(_state(), "ctx", None)


def host_set_config() -> Callable[[str, Any], Any] | None:
    """``ctx.set_config`` as a plain callable, or ``None`` when no context offers one.

    ``PluginContext.set_config(key, value)`` writes one plugin-relative key under
    ``plugins.entries.<id>.settings``, enforces managed-install and administrator-managed
    refusals, and takes the cross-process lock — everything Sluice needs and nothing it
    should reimplement (DESIGN.md:63-64: never write ``config.yaml`` ourselves).
    """
    setter = getattr(host_ctx(), "set_config", None)
    return setter if callable(setter) else None


def clear_host_ctx() -> None:
    """Drop the parked context. Tests use it; runtime never calls it."""
    setattr(_state(), "ctx", None)
