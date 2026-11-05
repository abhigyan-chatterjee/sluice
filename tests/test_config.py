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
