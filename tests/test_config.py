"""The runtime settings view and the manifest must not drift.

``sluice/config.py`` claims its defaults mirror ``plugin.yaml config_schema``. This file is
what makes that claim true: a default changed in one place and not the other fails here.
"""

from __future__ import annotations

import re
import socket
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
    normalize_searxng_url,
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


def test_a_trailing_slash_is_stripped_so_search_never_doubles_it(monkeypatch):
    for raw in ("http://127.0.0.1:8888/", "http://127.0.0.1:8888//"):
        monkeypatch.setenv("SEARXNG_URL", raw)
        assert load_settings(FakeContext({"searxng_url": ""}))["searxng_url"] == "http://127.0.0.1:8888"


def test_a_path_prefix_survives_normalization(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "https://search.example.com/searxng/")
    settings = load_settings(FakeContext({"searxng_url": ""}))
    assert settings["searxng_url"] == "https://search.example.com/searxng"
    assert settings["searxng_url_error"] == ""


def test_an_unset_url_is_empty_and_not_an_error():
    settings = load_settings(FakeContext({"searxng_url": ""}))
    assert settings["searxng_url"] == ""
    assert settings["searxng_url_error"] == ""
    assert "searxng" not in configured_backends(settings)


def test_a_malformed_url_is_dropped_with_the_reason_kept(monkeypatch):
    cases = {
        "localhost:8888": "including the scheme",
        "ftp://127.0.0.1:8888": "must use http or https",
        "http://": "has no host",
        "http://127.0.0.1:8888/?q=x": "without a query",
    }
    for raw, marker in cases.items():
        monkeypatch.setenv("SEARXNG_URL", raw)
        settings = load_settings(FakeContext({"searxng_url": ""}))
        assert settings["searxng_url"] == "", raw
        assert marker in settings["searxng_url_error"], raw
        # A rejected address must never be routed from.
        assert "searxng" not in configured_backends(settings), raw


def test_the_plugin_setting_is_validated_by_the_same_rules(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8888")
    settings = load_settings(FakeContext({"searxng_url": "not a url"}))
    assert settings["searxng_url"] == ""
    assert "including the scheme" in settings["searxng_url_error"]


def test_the_backend_reports_the_malformed_reason_not_a_missing_variable():
    from sluice.backends import make_backend

    url, error = normalize_searxng_url("127.0.0.1:8888")
    backend = make_backend("searxng", {"searxng_url": url, "searxng_url_error": error})
    message = backend.missing_message()
    assert "including the scheme" in message
    assert "is not set" not in message
    assert backend.mode() == "unavailable"

    result = backend.search("q")
    assert result["ok"] is False
    assert "including the scheme" in result["error"]


def test_a_port_urlsplit_defers_is_refused_before_httpx_sees_it(monkeypatch):
    """F4 residual: ``parts.port`` raises lazily, so the validator must read it.

    ``urlsplit`` accepts ``http://host:abc`` and ``http://::1:8888`` and only raises when
    ``parts.port`` is read; the validator never read it, so both reached httpx, whose
    ``InvalidURL`` is not an ``httpx.HTTPError`` and escaped the adapter boundary. A
    colon-only netloc (``http://:8888``) parses with no hostname at all.
    """
    from sluice.backends import make_backend

    cases = {
        "http://host:abc": "not a parseable URL",
        "http://host:99999": "not a parseable URL",
        "http://::1:8888": "not a parseable URL",
        "http://:8888": "has no host",
    }
    for raw, marker in cases.items():
        monkeypatch.setenv("SEARXNG_URL", raw)
        settings = load_settings(FakeContext({"searxng_url": ""}))
        assert settings["searxng_url"] == "", raw
        assert marker in settings["searxng_url_error"], (raw, settings["searxng_url_error"])
        # The refusal is what the adapter reports: no request, no httpx exception.
        result = make_backend("searxng", settings).search("q")
        assert result["ok"] is False, raw
        assert marker in result["error"], (raw, result["error"])


def test_a_non_printable_character_is_refused_before_it_reaches_httpx():
    """``urlsplit`` keeps a NUL or DEL and httpx rejects it with ``InvalidURL``.

    Same escape route as a bad port (``InvalidURL`` is not an ``httpx.HTTPError``), and a
    value like this can only arrive from an env var or a hand-edited config, so the reason
    is the only signal the user gets.
    """
    for raw in ("http://host\x00:8888", "http://host:8888/\x7f", "http://host:8888/\x01"):
        url, error = normalize_searxng_url(raw)
        assert url == "", raw
        assert "non-printable" in error, (raw, error)


def test_a_host_httpx_cannot_encode_is_refused_with_its_reason(monkeypatch):
    """``parts.hostname`` is not an IDNA check.

    A zero-width space, an NBSP, a non-breaking hyphen, an emoji or a combining mark all
    pass ``urlsplit`` and only fail inside httpx, whose ``InvalidURL`` is not an
    ``httpx.HTTPError`` and so escapes the adapter boundary as an exception instead of a
    failure dict.
    """
    from sluice.backends import make_backend

    cases = (
        "http://ho\u200bst:8888",
        "http://ho\u00a0st:8888",
        "http://ho\u2011st:8888",
        "http://\U0001F600.example:8888",
        "http://mu\u0308nchen.example:8888",
    )
    for raw in cases:
        monkeypatch.setenv("SEARXNG_URL", raw)
        settings = load_settings(FakeContext({"searxng_url": ""}))
        assert settings["searxng_url"] == "", raw
        assert "SEARXNG_URL" in settings["searxng_url_error"], raw
        assert "IDNA" in settings["searxng_url_error"], (raw, settings["searxng_url_error"])
        # The adapter contract: a refused address is a dict, never an exception.
        result = make_backend("searxng", settings).search("q", timeout=0.3)
        assert isinstance(result, dict), raw
        assert result["ok"] is False, raw
        assert "SEARXNG_URL" in result["error"], (raw, result["error"])


def test_an_overlong_url_is_refused_without_echoing_it():
    """httpx refuses a URL past 64 KiB with ``InvalidURL: URL too long``; refuse it first.

    The reason must stay short: copying a megabyte value into ``searxng_url_error`` would
    put it in front of the user through doctor, the pane and the CLI.
    """
    from sluice.backends import make_backend

    raw = "http://127.0.0.1:1/" + "a" * (1024 * 1024)
    url, error = normalize_searxng_url(raw)
    assert url == ""
    assert "SEARXNG_URL" in error
    assert "2048" in error, error
    assert len(error) < 512, len(error)
    result = make_backend("searxng", {"searxng_url": url, "searxng_url_error": error}).search(
        "q", timeout=0.3
    )
    assert isinstance(result, dict)
    assert result["ok"] is False
    assert "SEARXNG_URL" in result["error"]


def test_a_lone_surrogate_is_refused_before_it_reaches_httpx():
    """``os.environ`` decodes undecodable bytes with surrogateescape; httpx cannot encode one.

    ``httpx.URL`` raises ``UnicodeEncodeError`` for a surrogate in the path, not
    ``InvalidURL``, so the validator must catch both to keep its promise.
    """
    url, error = normalize_searxng_url("http://host:8888/\ud800")
    assert url == ""
    assert "SEARXNG_URL" in error, error


def test_a_valid_idna_host_is_still_accepted():
    """The IDNA gate must not refuse a hostname httpx can encode."""
    for raw in ("http://münchen.example:8888", "http://127.0.0.1:8888"):
        url, error = normalize_searxng_url(raw)
        assert error == "", (raw, error)
        assert url == raw, (raw, url)


def test_every_validator_accepted_address_survives_the_http_boundary():
    """No address the validator accepts may raise out of ``SearxngBackend.search()``.

    The port is bound and closed first, so a request fails at connect time: that is an
    ``httpx.HTTPError`` the adapter already maps to a failure dict. A value httpx cannot
    parse raises ``httpx.InvalidURL`` instead, which is not an ``HTTPError`` and is what
    this test watches for: the candidates below include every address the other tests
    expect to be refused, so re-accepting one fails here rather than in the router.
    """
    from sluice.backends import make_backend

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    closed_port = listener.getsockname()[1]
    listener.close()
    candidates = (
        f"http://127.0.0.1:{closed_port}",
        f"http://127.0.0.1:{closed_port}/searxng/",
        f"https://127.0.0.1:{closed_port}",
        "http://host:abc",
        "http://host:99999",
        "http://::1:8888",
        "http://:8888",
        "http://host\x00:8888",
        "http://host:8888/\x7f",
        # F4 tail: hosts urlsplit accepts and httpx's IDNA encoder refuses.
        "http://ho\u200bst:8888",
        "http://ho\u00a0st:8888",
        "http://ho\u2011st:8888",
        "http://\U0001F600.example:8888",
        "http://mu\u0308nchen.example:8888",
        # F4 tail: past httpx's 64 KiB URL cap.
        "http://127.0.0.1:1/" + "a" * (1024 * 1024),
    )
    accepted = 0
    for raw in candidates:
        url, error = normalize_searxng_url(raw)
        if error:
            continue
        accepted += 1
        backend = make_backend("searxng", {"searxng_url": url, "searxng_url_error": error})
        result = backend.search("q", timeout=1.0)
        assert isinstance(result, dict), raw
        assert result["ok"] is False, raw
        assert result["error"], raw
    assert accepted == 3, "the three loopback addresses must still be accepted"
