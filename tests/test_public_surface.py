"""F5: the plugin boundary. No private Hermes core import, and settings write through ctx.

``hermes plugins validate`` warns on any import of a Hermes module that is not a documented
plugin surface. Sluice used to import ``hermes_cli.plugins_settings.save_plugin_settings``
directly; the fix is to write settings through the public ``PluginContext.set_config``
(``sluice/hostctx.py``). These tests make that structural: a re-introduced private import
fails the suite even on a machine where Hermes is not installed to run the validator.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sluice import api
from sluice.hostctx import clear_host_ctx, host_set_config, set_host_ctx

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIRS = (ROOT / "sluice", ROOT / "dashboard")
ENTRY = ROOT / "__init__.py"

#: Hermes modules a plugin may import: documented plugin surfaces only. ``hermes_cli.config``
#: is the public config module (``load_config_readonly``); ``agent.web_search_provider`` is the
#: documented provider base plus ``get_provider_env``; ``tools.url_safety`` is the published
#: URL-safety surface (``is_safe_url``, ``redirect_target_from_response``) the redirect-hop
#: SSRF guard uses. Nothing else is needed.
ALLOWED_HERMES_MODULES = frozenset(
    {"hermes_cli.config", "agent.web_search_provider", "tools.url_safety"}
)

#: The private modules this finding was about. Named so the failure message says what broke.
FORBIDDEN_MODULES = ("hermes_cli.plugins_settings", "hermes_cli.plugins_state",
                     "hermes_cli.plugins_loader", "hermes_cli.plugin_validate")


def _modules() -> list[Path]:
    files = [ENTRY]
    for directory in PACKAGE_DIRS:
        files.extend(sorted(directory.rglob("*.py")))
    return [path for path in files if "__pycache__" not in path.parts]


def _imported_modules(tree: ast.AST) -> list[str]:
    """Every dotted module a file imports, including function-local imports."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            target = ast.unparse(node.func)
            if target in ("importlib.import_module", "import_module") and isinstance(node.args[0].value, str):
                names.append(node.args[0].value)
    return names


def test_the_package_imports_only_public_hermes_modules():
    offenders: dict[str, list[str]] = {}
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_modules(tree):
            top = module.split(".")[0]
            if top not in ("hermes_cli", "agent", "tools", "gateway", "hermes_state", "utils"):
                continue
            if module in ALLOWED_HERMES_MODULES:
                continue
            offenders.setdefault(path.relative_to(ROOT).as_posix(), []).append(module)
    assert not offenders, f"private Hermes imports found: {offenders}"


def test_no_module_names_the_private_settings_writer_any_more():
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert forbidden not in text, f"{path.relative_to(ROOT)} still names {forbidden}"


class FakeContext:
    """The bit of ``PluginContext`` the settings route uses."""

    def __init__(self, *, refuse: dict[str, Exception] | None = None) -> None:
        self.written: list[tuple[str, object]] = []
        self.refuse = refuse or {}

    def set_config(self, key: str, value: object) -> None:
        if key in self.refuse:
            raise self.refuse[key]
        self.written.append((key, value))


@pytest.fixture(autouse=True)
def _forget_context():
    clear_host_ctx()
    yield
    clear_host_ctx()


def test_a_parked_context_is_what_host_set_config_returns():
    ctx = FakeContext()
    assert host_set_config() is None
    set_host_ctx(ctx)
    assert host_set_config() is not None


def test_settings_are_written_through_ctx_set_config():
    ctx = FakeContext()
    set_host_ctx(ctx)
    result = api.save_settings_payload({"keyless_enabled": False, "plan_brave": "paid"})
    assert result["ok"] is True
    assert ctx.written == [("keyless_enabled", False), ("plan_brave", "paid")]
    assert sorted(result["written"]) == ["keyless_enabled", "plan_brave"]


def test_a_wrong_type_is_refused_before_anything_is_written():
    ctx = FakeContext()
    set_host_ctx(ctx)
    result = api.save_settings_payload({"cache_max_mb": "300", "keyless_enabled": False})
    assert result["ok"] is False
    assert "should be int" in result["error"]
    assert ctx.written == [], "a refused batch must not write the keys before it"


def test_an_integer_is_not_accepted_for_a_boolean_setting():
    set_host_ctx(FakeContext())
    result = api.save_settings_payload({"keyless_enabled": 1})
    assert result["ok"] is False
    assert "should be bool" in result["error"]


def test_a_permission_refusal_from_the_host_is_surfaced():
    ctx = FakeContext(refuse={"keyless_enabled": PermissionError("administrator-managed")})
    set_host_ctx(ctx)
    result = api.save_settings_payload({"keyless_enabled": False})
    assert result["ok"] is False
    assert "administrator-managed" in result["error"]


def test_no_context_means_an_actionable_error_not_a_guess():
    result = api.save_settings_payload({"keyless_enabled": False})
    assert result["ok"] is False
    assert "enable Sluice" in result["error"]
