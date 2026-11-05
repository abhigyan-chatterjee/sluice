"""F2: the pure locale resolver, pinned against the tag shape Chromium accepts.

Chromium accepts ``locale='C'`` when Playwright creates the context and only fails
later, at ``new_page()``, with ``Protocol error (Emulation.setLocaleOverride):
Invalid locale name``. The same error was measured for ``C.UTF-8`` and ``POSIX``,
while ``en-US`` and ``de-DE`` were accepted. ``chromium_stage`` resolves the locale
before it reaches Playwright, so these tests cover that resolver directly.

Nothing here starts a browser: ``normalize_locale()`` and ``host_locale(env)`` are
pure, so the whole matrix runs in CI on every platform. The contract is that every
value the resolver returns is a tag Chromium accepts; when the host expresses no
preference the resolver still returns ``en-US`` rather than ``None``, so no caller
ever has to drop the ``locale=`` keyword argument to stay safe.
"""

from __future__ import annotations

import re

import pytest

from sluice.ladder import FALLBACK_LOCALE, host_locale, normalize_locale

#: Chromium's ``Emulation.setLocaleOverride`` takes a BCP-47 language, optionally
#: with an uppercase region or a three-digit UN M.49 area code. Anything with an
#: encoding (``.UTF-8``), a modifier (``@euro``) or POSIX underscores is not a tag.
PLAYWRIGHT_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2}|-[0-9]{3})?$")

#: Pseudo-locales that were measured to raise "Invalid locale name".
REJECTED_PSEUDO_LOCALES = {"c", "c.utf-8", "c.utf8", "posix", "posix.utf-8"}

#: Raw host values exercised across the property-style tests below.
RAW_SAMPLES = (
    "C",
    "c",
    "C.UTF-8",
    "C.utf8",
    "C.ISO-8859-1",
    "POSIX",
    "POSIX.UTF-8",
    "",
    "   ",
    "de_DE.UTF-8",
    "de_DE.UTF-8@euro",
    "de_DE@euro",
    "de_DE",
    "de_DE.",
    "de-DE",
    "de",
    "DE_de",
    "en_US",
    "en_US.UTF-8",
    "en_US_POSIX",
    "en",
    "en-US-x-private",
    "fr_FR",
    "pt_BR.UTF-8",
    "en_IN.UTF-8",
    "zh_CN.GBK",
    "zh_Hant_TW",
    "sr_RS.UTF-8@latin",
    ".UTF-8",
    "@euro",
    "de__DE",
    "123_456",
    "x",
    None,
)


def assert_playwright_acceptable(tag: str) -> None:
    """A returned tag is usable by Chromium and carries no raw host syntax."""
    assert isinstance(tag, str) and tag, f"{tag!r} is not a usable locale tag"
    assert PLAYWRIGHT_TAG.match(tag), f"{tag!r} is not a BCP-47-ish ll[-CC] tag"
    assert tag.lower() not in REJECTED_PSEUDO_LOCALES, f"{tag!r} is a pseudo-locale"
    for marker in ("_", ".", "@"):
        assert marker not in tag, f"{tag!r} still carries host locale syntax {marker!r}"


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"LANG": "C"}, "en-US"),
        ({"LC_ALL": "C.UTF-8"}, "en-US"),
        ({"LANG": "de_DE.UTF-8"}, "de-DE"),
        ({"LANG": "de_DE.UTF-8@euro"}, "de-DE"),
        ({}, "en-US"),
    ],
    ids=["LANG=C", "LC_ALL=C.UTF-8", "LANG=de_DE.UTF-8", "LANG=de_DE.UTF-8@euro", "unset"],
)
def test_f2_required_locale_matrix(env, expected):
    """F2: the five host values the audit matrix requires all resolve safely."""
    tag = host_locale(env)
    assert tag == expected
    assert_playwright_acceptable(tag)


@pytest.mark.parametrize(
    "raw",
    ["C", "c", "C.UTF-8", "C.utf8", "C.ISO-8859-1", "POSIX", "POSIX.UTF-8"],
)
def test_f2_c_and_posix_spellings_fall_back(raw):
    """F2: every spelling of the C/POSIX pseudo-locale becomes ``en-US``."""
    tag = normalize_locale(raw)
    assert tag == FALLBACK_LOCALE
    assert_playwright_acceptable(tag)


@pytest.mark.parametrize(
    "env",
    [{"LANG": ""}, {"LC_ALL": ""}, {"LANG": "   "}, {"LC_ALL": " ", "LANG": "  "}],
    ids=["empty-LANG", "empty-LC_ALL", "blank-LANG", "blank-both"],
)
def test_f2_empty_values_fall_back(env):
    """F2: an empty or blank variable is no preference, so the fallback is used."""
    tag = host_locale(env)
    assert tag == FALLBACK_LOCALE
    assert_playwright_acceptable(tag)


def test_f2_empty_variable_does_not_shadow_a_usable_language():
    """F2: an ``LC_ALL`` set to the empty string falls through to the next variable."""
    assert host_locale({"LC_ALL": "", "LC_MESSAGES": "", "LANG": "de_DE.UTF-8"}) == "de-DE"
    # A blank-but-set LC_ALL still wins precedence (POSIX ignores only the empty
    # string), is stripped, and normalizes to the fallback instead of reading LANG.
    tag = host_locale({"LC_ALL": "  ", "LANG": "fr_FR.UTF-8"})
    assert tag == FALLBACK_LOCALE
    assert_playwright_acceptable(tag)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("de_DE.UTF-8", "de-DE"),
        ("de_DE.UTF-8@euro", "de-DE"),
        ("de_DE@euro", "de-DE"),
        ("de-DE", "de-DE"),
        ("de_DE.", "de-DE"),
        ("DE_de", "de-DE"),
        ("fr_FR", "fr-FR"),
        ("pt_BR.UTF-8", "pt-BR"),
        ("en_IN.UTF-8", "en-IN"),
        ("en_US.UTF-8", "en-US"),
        ("zh_CN.GBK", "zh-CN"),
        ("sr_RS.UTF-8@latin", "sr-RS"),
        ("de", "de"),
    ],
)
def test_f2_usable_preferences_normalize_to_ll_cc(raw, expected):
    """F2: a usable host preference keeps its language and region in ``ll-CC`` form."""
    tag = normalize_locale(raw)
    assert tag == expected
    assert_playwright_acceptable(tag)


@pytest.mark.parametrize(
    "raw",
    [".UTF-8", "@euro", "de__DE", "123_456", "é_FR", "x", "en-US-x-private", "zh_Hant_TW", None],
    ids=[
        "encoding-only",
        "modifier-only",
        "double-underscore",
        "all-digits",
        "non-ascii",
        "one-char",
        "private-subtag",
        "script-subtag",
        "none",
    ],
)
def test_f2_malformed_values_never_escape(raw):
    """F2: a malformed value yields a usable tag, never the raw string."""
    tag = normalize_locale(raw)
    assert_playwright_acceptable(tag)


@pytest.mark.parametrize("raw", RAW_SAMPLES, ids=lambda value: repr(value))
def test_f2_result_is_idempotent(raw):
    """F2: normalizing a resolved tag again changes nothing."""
    once = normalize_locale(raw)
    assert normalize_locale(once) == once


@pytest.mark.parametrize("raw", RAW_SAMPLES, ids=lambda value: repr(value))
def test_f2_every_environment_channel_agrees_with_the_resolver(raw):
    """F2: ``LC_ALL``, ``LC_MESSAGES`` and ``LANG`` all feed the same resolver."""
    expected = normalize_locale(raw)
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        tag = host_locale({variable: raw})
        assert tag == expected, f"{variable}={raw!r} resolved to {tag!r}"
        assert_playwright_acceptable(tag)


def test_f2_locale_variables_follow_the_standard_precedence():
    """F2: ``LC_ALL`` beats ``LC_MESSAGES`` beats ``LANG``."""
    assert (
        host_locale({"LC_ALL": "de_DE.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8", "LANG": "C"})
        == "de-DE"
    )
    assert host_locale({"LC_MESSAGES": "fr_FR.UTF-8", "LANG": "C"}) == "fr-FR"
    assert host_locale({"LANG": "de_DE.UTF-8"}) == "de-DE"
    assert host_locale({"LC_ALL": "C", "LANG": "de_DE.UTF-8"}) == "en-US"


def test_f2_unset_environment_yields_the_safe_fallback(monkeypatch):
    """F2: with no preference anywhere the tag is still usable, so the kwarg is safe."""
    import locale as locale_module

    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(locale_module, "getlocale", lambda: (None, None))

    tag = host_locale()
    assert tag == FALLBACK_LOCALE == "en-US"
    assert_playwright_acceptable(tag)


def test_f2_getlocale_path_normalizes_the_host_preference(monkeypatch):
    """F2: the production path (``locale.getlocale()``) is normalized too."""
    import locale as locale_module

    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setattr(locale_module, "getlocale", lambda: ("de_DE", "UTF-8"))
    assert host_locale() == "de-DE"

    monkeypatch.setattr(locale_module, "getlocale", lambda: ("C", "UTF-8"))
    assert host_locale() == "en-US"

    # getlocale() reports nothing, so the process environment decides.
    monkeypatch.setattr(locale_module, "getlocale", lambda: (None, None))
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert host_locale() == "de-DE"


def test_f2_live_verified_tags_stay_canonical():
    """F2: the two tags the live Chromium check accepted are produced unchanged."""
    assert normalize_locale("en_US.UTF-8") == "en-US"
    assert normalize_locale("de_DE.UTF-8") == "de-DE"
