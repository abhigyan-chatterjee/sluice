"""F5 regression: the ctx parked by ``register()`` must reach ``dashboard/plugin_api.py``.

Hermes loads a directory plugin as ``hermes_plugins.<slug>`` with the plugin directory as
the package ``__path__`` (``hermes_cli/plugins_loader._load_directory_module`` and
``hermes_cli/plugin_host_child._import_plugin``), and mounts the dashboard wrapper by file
path (``web_server_dashboard._mount_one_plugin_api`` / ``plugin_host_child.op_asgi``). The
two import ``sluice/hostctx.py`` under different module names — ``register()``'s relative
import lands on ``hermes_plugins.sluice_search.sluice.hostctx``, a bare ``from sluice import api``
on ``sluice.hostctx`` — so a context held in module globals is parked in one copy and read
from the other. That shipped once: the plugin was loaded and registered while every
``POST /settings`` answered "no Hermes plugin context".

``dashboard/plugin_api.py`` now binds the copy the host already loaded, and ``hostctx``
parks the context once per process, so either one alone keeps the handoff alive. These
tests perform that exact mount and assert the write lands, plus the negative control for a
process where ``register()`` never ran. They fail if the context becomes per-module again or
if the wrapper goes back to a bare ``import sluice``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The names both Hermes loaders assign (``_directory_module_name`` / ``op_asgi``).
ENTRY_MODULE = "hermes_plugins.sluice_search"
DASHBOARD_MODULE = "hermes_dashboard_plugin_sluice-search"


class RecordingContext:
    """The ``PluginContext`` surface ``register()`` touches, with a recording ``set_config``."""

    def __init__(self) -> None:
        self.written: list[tuple[str, object]] = []
        self.provider: object | None = None
        self.commands: list[dict[str, object]] = []
        self.skills: list[tuple[object, ...]] = []

    def set_config(self, key: str, value: object) -> None:
        self.written.append((str(key), value))

    def register_web_search_provider(self, provider: object) -> None:
        self.provider = provider

    def register_cli_command(self, **kwargs: object) -> None:
        self.commands.append(kwargs)

    def register_skill(self, *args: object) -> None:
        self.skills.append(args)


def _load_entry() -> types.ModuleType:
    """Load the plugin entry the way ``plugins_loader._load_directory_module`` does."""
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []  # type: ignore[attr-defined]
        namespace.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = namespace
    spec = importlib.util.spec_from_file_location(
        ENTRY_MODULE,
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = ENTRY_MODULE
    module.__path__ = [str(REPO_ROOT)]  # type: ignore[attr-defined]
    sys.modules[ENTRY_MODULE] = module
    spec.loader.exec_module(module)
    return module


def _load_dashboard_api() -> types.ModuleType:
    """Load ``dashboard/plugin_api.py`` the way the mount does."""
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location(
        DASHBOARD_MODULE, REPO_ROOT / "dashboard" / "plugin_api.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[DASHBOARD_MODULE] = module
    spec.loader.exec_module(module)
    return module


def _is_mount_module(name: str) -> bool:
    return name == "hermes_plugins" or name == ENTRY_MODULE or name.startswith(f"{ENTRY_MODULE}.") \
        or name == DASHBOARD_MODULE


@pytest.fixture(autouse=True)
def _fresh_mount() -> Iterator[None]:
    """Every test mounts from scratch; the interpreter is left as it was found.

    ``tests/test_backends.py`` imports ``hermes_plugins.sluice_search.sluice.backends.ddgs`` and
    leaves that namespace behind, so the host copy has to be evicted up front too — a test
    about which module the wrapper binds must not inherit another test's mount.
    """
    from sluice.hostctx import clear_host_ctx

    saved = [(name, module) for name, module in list(sys.modules.items()) if _is_mount_module(name)]
    for name in [name for name in sys.modules if _is_mount_module(name)]:
        del sys.modules[name]
    clear_host_ctx()
    try:
        yield
    finally:
        clear_host_ctx()
        for name in [name for name in sys.modules if _is_mount_module(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_a_settings_write_from_the_dashboard_reaches_the_context_register_parked():
    """The whole handoff: entry loaded as ``hermes_plugins.sluice_search``, wrapper mounted by path."""
    entry = _load_entry()
    ctx = RecordingContext()
    entry.register(ctx)

    plugin_api = _load_dashboard_api()
    result = plugin_api.post_settings({"values": {"cache_max_mb": 300}})

    assert result == {"ok": True, "written": ["cache_max_mb"]}
    assert ctx.written == [("cache_max_mb", 300)]


def test_the_wrapper_binds_the_plugin_copy_the_host_already_loaded():
    """No second ``sluice`` package: the wrapper uses the entry's own module namespace.

    Hermes does not put the plugin directory on ``sys.path``, so a bare ``import sluice``
    would either fail (the normal directory install) or load a pip-installed duplicate whose
    ``hostctx`` is a different module object. Binding the loaded copy makes both problems
    structurally impossible.
    """
    entry = _load_entry()
    entry.register(RecordingContext())

    plugin_api = _load_dashboard_api()

    assert plugin_api.api.__name__ == f"{ENTRY_MODULE}.sluice.api"
    assert plugin_api.api is sys.modules[f"{ENTRY_MODULE}.sluice.api"]


def test_a_wrapper_loaded_before_the_plugin_still_sees_the_later_context():
    """Two copies is still survivable: the context is parked per process, not per module."""
    from sluice import api as standalone_api

    plugin_api = _load_dashboard_api()
    assert plugin_api.api is standalone_api, (
        "setup: no host copy is loaded, so the plain import must win"
    )

    entry = _load_entry()
    ctx = RecordingContext()
    entry.register(ctx)

    result = plugin_api.post_settings({"values": {"cache_max_mb": 300}})

    assert result["ok"] is True
    assert ctx.written == [("cache_max_mb", 300)]


def test_the_same_mount_without_register_reports_the_actionable_error():
    """Negative control: nothing parked means the error, not a guess at a writer."""
    plugin_api = _load_dashboard_api()

    result = plugin_api.post_settings({"values": {"cache_max_mb": 300}})

    assert result["ok"] is False
    assert "enable Sluice" in result["error"]
