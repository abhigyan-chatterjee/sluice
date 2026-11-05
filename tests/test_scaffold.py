"""Scaffold smoke tests (PLAN.md:43-54).

Three things must hold at T1 and stay true as the tasks land: the package imports without
Hermes, ``register(ctx)`` wires exactly one provider named ``sluice`` plus one CLI command,
and the CLI parser builds every subcommand the plan names.

``register()`` is exercised through the same loader shape Hermes uses — the repository-root
``__init__.py`` imported as a package whose ``__path__`` is the repository root — because a
bare ``import __init__`` would not reproduce it.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin.yaml"

#: PLAN.md:49-52.
EXPECTED_SECRET_ENVS = [
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
]


class RecordingContext:
    """The subset of ``PluginContext`` that ``register()`` touches."""

    def __init__(self) -> None:
        self.providers = []
        self.cli_commands = []
        self.settings: dict = {}

    def register_web_search_provider(self, provider):
        self.providers.append(provider)

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli_commands.append(
            {
                "name": name,
                "help": help,
                "setup_fn": setup_fn,
                "handler_fn": handler_fn,
                "description": description,
            }
        )

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


@pytest.fixture(scope="module")
def plugin_module():
    """Import the repository-root ``__init__.py`` the way the Hermes loader does."""
    name = "sluice_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(ROOT)]
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        for loaded in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
            del sys.modules[loaded]


# ── package ─────────────────────────────────────────────────────────────────────


def test_package_imports():
    import sluice

    assert sluice.__version__ == "1.0.0"


def test_provider_is_constructible_without_hermes():
    from sluice.provider import PROVIDER_NAME, SluiceWebSearchProvider

    provider = SluiceWebSearchProvider()
    assert provider.name == PROVIDER_NAME == "sluice"
    assert provider.supports_search() is True
    assert provider.supports_extract() is True


def test_provider_never_raises_across_the_boundary():
    """DESIGN.md:68-72: failures are envelopes, and one bad URL never fails the batch."""
    from sluice.provider import SluiceWebSearchProvider

    provider = SluiceWebSearchProvider()
    result = provider.search("anything", limit=3)
    assert result["success"] is False
    assert isinstance(result["error"], str) and result["error"]

    items = provider.extract(["https://example.com/a", "https://example.com/b"])
    assert [item["url"] for item in items] == ["https://example.com/a", "https://example.com/b"]
    assert all(item["error"] for item in items)


def test_availability_is_false_before_the_router_exists():
    """A provider that cannot route must not advertise itself (T1 decision, see REQUIREMENTS.md)."""
    from sluice.provider import SluiceWebSearchProvider

    provider = SluiceWebSearchProvider()
    assert provider.is_available() is False
    assert provider.is_keyless_available() is False


# ── registration ────────────────────────────────────────────────────────────────


def test_register_adds_one_web_provider(plugin_module):
    ctx = RecordingContext()
    plugin_module.register(ctx)

    assert len(ctx.providers) == 1
    provider = ctx.providers[0]
    assert provider.name == "sluice"
    assert provider.supports_search() and provider.supports_extract()


def test_register_adds_the_sluice_cli_command(plugin_module):
    ctx = RecordingContext()
    plugin_module.register(ctx)

    assert [cmd["name"] for cmd in ctx.cli_commands] == ["sluice"]
    command = ctx.cli_commands[0]
    assert callable(command["setup_fn"])
    assert callable(command["handler_fn"])


def test_register_fails_loudly_without_the_provider_surface(plugin_module):
    """An older Hermes must raise a named error, not register a dead plugin."""

    class OldContext:
        """A pre-0.22.5 context: no register_web_search_provider at all."""

        def register_cli_command(self, **kwargs):
            raise AssertionError("register() must not reach the CLI registration")

    with pytest.raises(RuntimeError, match="register_web_search_provider"):
        plugin_module.register(OldContext())


# ── CLI ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["doctor"],
        ["status"],
        ["setup"],
        ["setup", "--apply"],
        ["use", "search"],
        ["use", "extract"],
        ["test", "--backend", "exa", "zstd vs gzip"],
        ["cache", "clear"],
        ["install", "playwright"],
        ["install", "camofox"],
        ["install", "browserbase"],
    ],
)
def test_cli_parser_accepts_every_planned_subcommand(argv):
    from sluice.cli import build_parser

    args = build_parser().parse_args(argv)
    assert args.sluice_command == argv[0]


def test_cli_rejects_unknown_subcommand_and_bad_install_target():
    from sluice.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["nope"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["install", "chromium"])


def test_cli_handler_fails_unattended_until_implemented(capsys):
    """T1 ships the parser only: every command must exit non-zero and say why."""
    from sluice.cli import build_parser, handle_command

    args = build_parser().parse_args(["doctor"])
    assert handle_command(args) == 1
    assert "T9" in capsys.readouterr().err


def test_cli_setup_parser_attaches_to_a_foreign_parser():
    """Hermes builds the ``hermes sluice`` parser itself and hands it to setup_fn."""
    import argparse

    from sluice.cli import handle_command, setup_parser

    parser = argparse.ArgumentParser(prog="hermes sluice")
    setup_parser(parser)
    args = parser.parse_args(["status"])
    assert args.sluice_command == "status"
    assert args.func is handle_command


# ── backends ────────────────────────────────────────────────────────────────────


def test_all_twelve_vendors_are_registered():
    from sluice.backends import BACKEND_MODULES, specs

    registry = specs()
    assert len(registry) == 12
    assert list(registry) == list(BACKEND_MODULES)


def test_tier_coverage_matches_the_design_table():
    """DESIGN.md:143-150: local 1, keyless 6, free 4, paid 10, one entry per vendor."""
    from sluice.backends import specs

    counts: dict[str, int] = {}
    for vendor in specs().values():
        for tier in vendor.tiers:
            counts[tier] = counts.get(tier, 0) + 1
    assert counts == {"local": 1, "keyless": 6, "free": 4, "paid": 10}


def test_every_vendor_that_needs_a_key_names_one():
    from sluice.backends import specs

    for vendor in specs().values():
        if vendor.tiers[0] in ("local",) or vendor.id == "ddgs":
            assert vendor.secret_env is None, vendor.id
        else:
            assert vendor.secret_env, vendor.id
            assert vendor.secret_env.endswith("_API_KEY")


# ── manifest ────────────────────────────────────────────────────────────────────


def _manifest_scalar(text: str, key: str) -> str | None:
    """A top-level scalar from ``plugin.yaml`` without needing a YAML parser."""
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def test_manifest_required_fields():
    text = MANIFEST.read_text(encoding="utf-8")
    assert _manifest_scalar(text, "name") == "sluice"
    assert _manifest_scalar(text, "version") == '"1.0.0"'
    assert _manifest_scalar(text, "kind") == "backend"
    assert _manifest_scalar(text, "manifest_version") == "2"
    assert _manifest_scalar(text, "requires_hermes") == '">=0.22.5"'
    assert _manifest_scalar(text, "license") == "MIT"


def test_manifest_declares_all_ten_secrets():
    text = MANIFEST.read_text(encoding="utf-8")
    for env in EXPECTED_SECRET_ENVS:
        assert f"env: {env}" in text, f"{env} missing from config_schema"
    assert text.count("type: secret") == len(EXPECTED_SECRET_ENVS)


def test_manifest_declares_the_provider_and_python_dependencies():
    text = MANIFEST.read_text(encoding="utf-8")
    assert "provides_web_providers:" in text
    assert "- sluice" in text
    assert _manifest_scalar(text, "capabilities") == "[]"
    for dep in ("httpx>=0.27,<1", "trafilatura>=1.8,<3", "playwright>=1.45,<2", "ddgs>=9,<10"):
        assert f'"{dep}"' in text, dep


def test_pyproject_and_manifest_agree_on_versions():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.0.0"' in pyproject
    assert 'requires-python = ">=3.11"' in pyproject
    assert 'sluice = "sluice.cli:main"' in pyproject
