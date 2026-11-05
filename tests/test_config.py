"""The runtime settings view and the manifest must not drift.

``sluice/config.py`` claims its defaults mirror ``plugin.yaml config_schema``. This file is
what makes that claim true: a default changed in one place and not the other fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from sluice.backends import BACKEND_MODULES
from sluice.config import (
    DEFAULTS,
    FREE_PLAN_VENDORS,
    SECRET_ENVS,
    backend_plan,
    configured_backends,
    default_plan,
    load_settings,
    plan_key,
    plan_settings,
    tier_order,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "plugin.yaml"

_LINE = re.compile(r"^  ([a-z0-9_]+):\s*\{(.*)\}\s*$")
_TYPE = re.compile(r"type:\s*(\w+)")
_QUOTED_DEFAULT = re.compile(r'default:\s*"([^"]*)"')
_PLAIN_DEFAULT = re.compile(r"default:\s*([^,]+)")
_SECRET = re.compile(r"env:\s*([A-Z0-9_]+)")


def manifest_fields() -> dict[str, tuple[str, str | None]]:
    text = MANIFEST.read_text(encoding="utf-8")
    fields: dict[str, tuple[str, str | None]] = {}
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        key, body = match.group(1), match.group(2)
        kind_match = _TYPE.search(body)
        if not kind_match:
            continue
        quoted = _QUOTED_DEFAULT.search(body)
        plain = _PLAIN_DEFAULT.search(body)
        default = quoted.group(1) if quoted else (plain.group(1).strip() if plain else None)
        fields[key] = (kind_match.group(1), default)
    return fields


def test_every_manifest_setting_has_a_runtime_default():
    fields = manifest_fields()
    manifest_settings = {key for key, (kind, _) in fields.items() if kind != "secret"}
    runtime_settings = set(DEFAULTS) - {"camofox_url"}
    assert manifest_settings == runtime_settings


def test_runtime_defaults_match_the_manifest():
    fields = manifest_fields()
    for key, value in DEFAULTS.items():
        if key == "camofox_url":
            continue
        kind, default = fields[key]
        if kind == "bool":
            assert str(value).lower() == str(default).lower(), key
        elif kind == "int":
            assert int(value) == int(default), key
        else:
            assert str(value) == str(default), key


def test_manifest_declares_exactly_the_ten_secrets():
    text = MANIFEST.read_text(encoding="utf-8")
    declared = set(_SECRET.findall(text))
    assert declared == set(SECRET_ENVS)
    assert len(SECRET_ENVS) == 10


def test_every_keyed_backend_has_a_plan_default():
    plans = plan_settings()
    keyed = {vendor for vendor in BACKEND_MODULES if vendor not in ("searxng", "ddgs")}
    assert set(plans) == {plan_key(vendor) for vendor in keyed}
    for vendor in keyed:
        expected = "free" if vendor in FREE_PLAN_VENDORS else "paid"
        assert plans[plan_key(vendor)] == expected, vendor
    # A local or keyless-only vendor has no plan: its tier is fixed by its access mode.
    assert plan_key("searxng") not in plans and plan_key("ddgs") not in plans


def test_free_plan_vendors_are_the_documented_four():
    assert FREE_PLAN_VENDORS == {"brave", "tavily", "exa", "firecrawl"}
    assert default_plan("perplexity") == "paid"
    assert default_plan("unknown-vendor") == "paid"


def test_backend_plan_reads_settings_and_falls_back():
    assert backend_plan({}, "exa") == "free"
    assert backend_plan({}, "xai") == "paid"
    assert backend_plan({"plan_exa": "paid"}, "exa") == "paid"
    assert backend_plan({"plan_exa": "nonsense"}, "exa") == "free"


def test_tier_order_is_normalized_to_the_four_tiers():
    assert tier_order({"tier_order": "paid,free"}) == ["paid", "free", "local", "keyless"]
    settings = load_settings()
    assert tier_order(settings) == ["local", "keyless", "free", "paid"]


def test_keyless_enabled_parses_strings():
    assert load_settings()["keyless_enabled"] is True


def test_configured_backends_skips_missing_keys_unless_keyless():
    settings = load_settings()
    settings["BRAVE_API_KEY"] = ""
    settings["DEEPSEEK_API_KEY"] = ""
    usable = configured_backends(settings)
    # Brave and DeepSeek are key-only: no key, no backend.
    assert "brave" not in usable and "deepseek" not in usable
    # Exa and Tavily have a keyless path, so they stay usable.
    assert "exa" in usable and "tavily" in usable
    assert "searxng" not in usable  # no SEARXNG_URL configured

    settings["keyless_enabled"] = False
    usable = configured_backends(settings)
    assert "exa" not in usable and "tavily" not in usable


def test_configured_backends_respects_capability():
    """Capability filtering is tested on injected settings, not on this machine's keys."""
    settings = {**DEFAULTS, **{env: "" for env in SECRET_ENVS}, "keyless_enabled": True}
    searchable = set(configured_backends(settings, capability="search"))
    extractable = set(configured_backends(settings, capability="extract"))
    # ddgs is keyless and search-only; exa is keyless and does both.
    assert "ddgs" in searchable and "ddgs" not in extractable
    assert "exa" in extractable
    # A key-only backend appears only when its key is present.
    assert "brave" not in searchable
    settings["BRAVE_API_KEY"] = "k"
    searchable = set(configured_backends(settings, capability="search"))
    extractable = set(configured_backends(settings, capability="extract"))
    assert "brave" in searchable and "brave" not in extractable


# ── a broken or hostile config file must never break a search ───────────────────


class FakeContext:
    """The ``get_config`` surface ``load_settings`` uses, with failure on demand."""

    def __init__(self, values: dict | None = None, *, broken: bool = False) -> None:
        self.values = dict(values or {})
        self.broken = broken
        self.calls: list[str] = []

    def get_config(self, key, default=None):
        self.calls.append(key)
        if self.broken:
            raise RuntimeError("config.yaml is half-written")
        return self.values.get(key, default)


def test_an_unknown_tier_order_is_repaired_to_the_four_known_tiers():
    settings = load_settings(FakeContext({"tier_order": "bogus,paid"}))
    assert tier_order(settings) == ["paid", "local", "keyless", "free"]


def test_boolean_setting_strings_are_parsed_and_garbage_keeps_the_default():
    for raw, expected in (("yes", True), ("ON", True), ("0", False), ("off", False), ("maybe", True)):
        settings = load_settings(FakeContext({"keyless_enabled": raw}))
        assert settings["keyless_enabled"] is expected, raw


def test_a_broken_context_falls_back_to_the_shipped_defaults():
    ctx = FakeContext(broken=True)
    settings = load_settings(ctx)
    for key, value in DEFAULTS.items():
        assert settings[key] == value, key
    assert ctx.calls, "the loader should have tried the context before falling back"


def test_missing_values_keep_the_manifest_defaults():
    settings = load_settings(FakeContext({}))
    for key, value in DEFAULTS.items():
        assert settings[key] == value, key


# ── SEARXNG_URL: the environment variable messages already name (F4) ────────────


def test_searxng_url_env_configures_searxng_when_the_setting_is_unset(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8888")
    settings = load_settings(FakeContext({"searxng_url": ""}))
    assert settings["searxng_url"] == "http://127.0.0.1:8888"
    assert "searxng" in configured_backends(settings)


def test_searxng_url_env_reaches_the_no_context_path_used_by_the_cli(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", " http://127.0.0.1:8888 ")
    assert load_settings()["searxng_url"] == "http://127.0.0.1:8888"


def test_an_explicit_searxng_setting_overrides_the_env(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://env.example:8080")
    settings = load_settings(FakeContext({"searxng_url": "http://plugin.example:8080"}))
    assert settings["searxng_url"] == "http://plugin.example:8080"
