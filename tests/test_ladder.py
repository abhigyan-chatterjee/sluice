"""T7 ladder tests: every class, every stop rule, and the robots path.

The classifier cases are files under ``tests/fixtures/blocks/``; the ladder cases run
against ``demo/fixture_server.py`` over real localhost HTTP, so the HTTP rung, the cookie
jar and the retry path are exercised for real rather than mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from demo.fixture_server import running_server
from sluice.ladder import (
    BLOCK_CLASSES,
    RETRY_AFTER_CAP_SECONDS,
    STAGES,
    StageOutcome,
    classify,
    clean_html,
    extract_text,
    fetch,
    fetch_many,
    host_locale,
    host_timezone,
    parse_robots,
    response_policy_block,
    robots_allowed,
)
from sluice.store import NS_ROBOTS, Store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "blocks"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))


def skipped_chromium(url, **kwargs):
    return StageOutcome(
        stage="chromium",
        block_class="error",
        url=url,
        skipped=True,
        reason="test: no browser",
    )


def ok_chromium(url, **kwargs):
    return StageOutcome(
        stage="chromium",
        block_class="ok",
        status=200,
        url=url,
        title="from chromium",
        content="rendered text",
        cookies=kwargs.get("cookies", []),
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "sluice.db")
    try:
        yield s
    finally:
        s.close()


# ── classifier ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_classifier_cases(case):
    body = (FIXTURES / case["body"]).read_bytes()
    assert (
        classify(case["status"], case["headers"], body, case["content_type"])
        == case["expected"]
    )


def test_every_class_the_classifier_can_produce_is_covered():
    """``policy_blocked`` comes from stage 0, not from the classifier; all others are here."""
    expected = {case["expected"] for case in CASES}
    assert expected == set(BLOCK_CLASSES) - {"policy_blocked"}


def test_cookie_markers_only_count_on_a_block_status():
    body = (
        b"<html><body><article><h1>A real page</h1>"
        + b"<p>This page has substantial prose in it, so it is not a shell.</p>" * 6
        + b"</article></body></html>"
    )
    # __cf_bm is set on successful Cloudflare responses too: diagnostic, not decisive.
    assert classify(200, {"set-cookie": "__cf_bm=1; Path=/"}, body, "text/html") == "ok"
    assert classify(403, {"set-cookie": "__cf_bm=1; Path=/"}, body, "text/html") == "challenge"


def test_plain_403_without_a_marker_is_forbidden():
    assert classify(403, {"server": "nginx"}, "<html>no</html>", "text/html") == "forbidden"


def test_imperva_fixture_is_exactly_83_bytes():
    assert len((FIXTURES / "imperva.html").read_bytes()) == 83


# ── robots parsing ────────────────────────────────────────────────────────────────


ROBOTS = """
User-agent: *
Disallow: /private
Allow: /private/ok
Disallow: /*.pdf$
Crawl-delay: 2
"""


def test_robots_longest_match_wins():
    rules = parse_robots(ROBOTS)
    assert rules.allowed("/private")[0] is False
    assert rules.allowed("/private/ok")[0] is True
    assert rules.crawl_delay == 2


def test_robots_dollar_anchor():
    rules = parse_robots(ROBOTS)
    assert rules.allowed("/docs/file.pdf")[0] is False
    assert rules.allowed("/docs/file.pdf?x=1")[0] is True


def test_robots_allow_wins_a_tie():
    rules = parse_robots("User-agent: *\nDisallow: /a\nAllow: /a\n")
    assert rules.allowed("/a")[0] is True


def test_robots_token_group_shadows_the_wildcard():
    text = (
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: sluice\nDisallow: /only-this\n"
    )
    rules = parse_robots(text)
    assert rules.allowed("/anything")[0] is True
    assert rules.allowed("/only-this")[0] is False


def test_robots_empty_agent_group_means_no_restriction():
    """A named group with no rules must not inherit the wildcard's disallow."""
    rules = parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: Sluice\n")
    assert rules.allowed("/anything")[0] is True


def test_robots_without_our_group_or_wildcard_allows_everything():
    rules = parse_robots("User-agent: googlebot\nDisallow: /\n")
    assert rules.allowed("/x")[0] is True


# ── robots fetch, cache and policy headers ───────────────────────────────────────


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_robots_404_means_crawlable(store):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404, text="")

    allowed, rule = robots_allowed(
        "https://example.com/page", store=store, client=_client(handler), now=0
    )
    assert allowed is True and rule == ""
    assert len(calls) == 1


def test_robots_500_means_disallowed(store):
    def handler(request):
        return httpx.Response(503, text="down")

    allowed, rule = robots_allowed(
        "https://example.com/page", store=store, client=_client(handler), now=0
    )
    assert allowed is False
    assert "503" in rule


def test_robots_file_disallow_quotes_the_rule(store):
    def handler(request):
        return httpx.Response(200, text="User-agent: *\nDisallow: /admin\n")

    allowed, rule = robots_allowed(
        "https://example.com/admin/panel", store=store, client=_client(handler), now=0
    )
    assert allowed is False and rule == "/admin"


def test_robots_result_is_cached_for_24_hours(store):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /x\n")

    client = _client(handler)
    assert robots_allowed("https://example.com/x", store=store, client=client, now=0)[0] is False
    assert robots_allowed("https://example.com/x", store=store, client=client, now=86_399)[0] is False
    assert len(calls) == 1
    # At exactly 24 h the cached row is expired and the file is fetched again.
    robots_allowed("https://example.com/x", store=store, client=client, now=86_400)
    assert len(calls) == 2
    assert store.cache_get(NS_ROBOTS, "https://example.com", now=86_400) is not None


def test_transport_error_on_robots_is_not_a_policy_verdict(store):
    def handler(request):
        raise httpx.ConnectError("down")

    allowed, rule = robots_allowed(
        "https://example.com/x", store=store, client=_client(handler), now=0
    )
    assert allowed is True and rule == ""


def test_x_robots_tag_noindex_is_a_policy_block():
    rule = response_policy_block({"X-Robots-Tag": "noindex, nofollow"})
    assert rule is not None and "noindex" in rule


def test_content_signal_search_no_is_a_policy_block():
    rule = response_policy_block({"Content-Signal": "search=no, ai-input=no"})
    assert rule is not None and "search=no" in rule


def test_ai_train_no_alone_is_not_a_fetch_block():
    """ai-train governs training; it must not stop a user's extraction request."""
    assert response_policy_block({"Content-Signal": "ai-train=no, search=yes"}) is None


# ── ladder over real localhost HTTP ──────────────────────────────────────────────


def test_ok_page_is_extracted_at_the_http_rung(store):
    with running_server() as base:
        item = fetch(f"{base}/ok", store=store, chromium=skipped_chromium)
    assert item["block_class"] == "ok"
    assert item["stage"] == "http"
    assert item["title"] == "Sluice fixture: OK"
    assert "plain HTML" in item["raw_content"]
    assert item["content"].startswith("[sluice: stage=http status=ok]")
    assert item["blocked_by_policy"] is False


def test_js_shell_climbs_to_chromium(store):
    with running_server() as base:
        item = fetch(f"{base}/js", store=store, chromium=ok_chromium)
    assert item["block_class"] == "ok"
    assert item["stage"] == "chromium"
    assert [stage["stage"] for stage in item["stages"]] == ["http", "chromium"]
    assert item["stages"][0]["block_class"] == "js_required"
    assert item["content"].startswith("[sluice: stage=chromium status=ok]")


def test_cloudflare_challenge_climbs_and_names_the_vendor(store):
    with running_server() as base:
        item = fetch(f"{base}/cf", store=store, chromium=ok_chromium)
    assert item["block_class"] == "ok"
    assert item["stages"][0]["block_class"] == "challenge"
    assert any("cf-mitigated" in signal for signal in item["stages"][0]["signals"])


def test_imperva_200_with_83_bytes_is_a_challenge(store):
    with running_server() as base:
        item = fetch(f"{base}/imperva", store=store, chromium=skipped_chromium)
    assert item["stages"][0]["block_class"] == "challenge"
    assert item["block_class"] == "challenge"
    assert "x-iinfo" in " ".join(item["stages"][0]["signals"])


def test_robots_disallow_returns_blocked_by_policy_with_the_rule(store):
    with running_server() as base:
        item = fetch(f"{base}/private", store=store, chromium=ok_chromium)
    assert item["block_class"] == "policy_blocked"
    assert item["blocked_by_policy"] is True
    assert item["error"] == "robots.txt disallows /private for Sluice (/private)"
    assert item["content"].startswith("[sluice: stage=policy status=policy_blocked]")
    assert item["error"] in item["content"]


def test_a_longer_allow_rule_reopens_the_path(store):
    with running_server() as base:
        item = fetch(f"{base}/private/ok", store=store, chromium=skipped_chromium)
    assert item["block_class"] == "ok"
    assert "allowed private path" in item["raw_content"]


def test_429_is_retried_once_then_succeeds(store):
    with running_server() as base:
        item = fetch(f"{base}/ratelimit-once", store=store, chromium=ok_chromium, sleep=lambda _s: None)
    assert item["block_class"] == "ok"
    assert item["stage"] == "http"


def test_persistent_429_stops_the_ladder_without_climbing(store):
    climbed = []

    def chromium(url, **kwargs):
        climbed.append(url)
        return ok_chromium(url, **kwargs)

    with running_server() as base:
        item = fetch(f"{base}/ratelimit", store=store, chromium=chromium, sleep=lambda _s: None)
    assert item["block_class"] == "rate_limited"
    assert climbed == []
    assert "rate_limited" in item["error"]


def _static_client(routes: dict[str, tuple[int, dict, str]]) -> httpx.Client:
    """A client whose every path is a fixed (status, headers, body) response."""

    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n")
        status, headers, body = routes.get(path, (404, {}, "missing"))
        return httpx.Response(status, headers=headers, text=body)

    return _client(handler)


@pytest.mark.parametrize(
    "status,expected",
    [(401, "auth_required"), (402, "payment_required"), (403, "forbidden")],
)
def test_401_402_403_stop_without_climbing(status, expected):
    climbed = []

    def chromium(url, **kwargs):
        climbed.append(url)
        return ok_chromium(url, **kwargs)

    client = _static_client({"/x": (status, {}, "<html><body>refused</body></html>")})
    item = fetch("https://example.com/x", http_client=client, chromium=chromium)
    assert item["block_class"] == expected
    assert climbed == []
    assert item["error"]


def test_500_stops_after_one_retry():
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        calls.append(request.url.path)
        return httpx.Response(500, text="boom")

    item = fetch("https://example.com/x", http_client=_client(handler))
    assert item["block_class"] == "error"
    assert len(calls) == 2  # the first attempt and the single retry


def test_x_robots_tag_on_the_response_stops_with_blocked_by_policy(store):
    client = _static_client(
        {"/x": (200, {"x-robots-tag": "noindex"}, "<html><body>" + "text " * 200 + "</body></html>")}
    )
    item = fetch("https://example.com/x", store=store, http_client=client)
    assert item["block_class"] == "policy_blocked"
    assert item["blocked_by_policy"] is True
    assert "X-Robots-Tag" in item["error"]


def test_a_skipped_engine_does_not_end_the_ladder(store):
    """Playwright missing must not stop the climb before Camofox."""
    seen = []

    def camofox(url, base_url):
        seen.append(base_url)
        return StageOutcome(stage="camofox", block_class="ok", status=200, url=url, content="cf text")

    with running_server() as base:
        item = fetch(f"{base}/cf", store=store, chromium=skipped_chromium, camofox=camofox)
    assert seen  # camofox was reached
    assert item["stage"] == "camofox" and item["block_class"] == "ok"
    assert item["stages"][1]["skipped"] is True


def test_domain_memory_starts_a_known_challenge_at_the_browser_rung(store):
    with running_server() as base:
        fetch(f"{base}/cf", store=store, chromium=ok_chromium)
        memory = store.domain_memory_get(f"127.0.0.1:{base.rsplit(':', 1)[1]}")
        assert memory is not None
        assert memory["stage_that_worked"] == "chromium"
        assert memory["block_class"] == "challenge"

        second = fetch(f"{base}/cf", store=store, chromium=ok_chromium)
    assert second["stages"][0]["stage"] == "chromium"
    assert "http" not in [stage["stage"] for stage in second["stages"]]


def test_domain_memory_expires_after_24_hours(store):
    store.domain_memory_put("example.com", "challenge", "chromium", now=0)
    assert store.domain_memory_get("example.com", now=86_399) is not None
    assert store.domain_memory_get("example.com", now=86_400) is None


def test_fetch_many_isolates_a_bad_url(store):
    with running_server() as base:
        items = fetch_many(
            ["not-a-url", f"{base}/ok"], store=store, chromium=skipped_chromium
        )
    assert items[0]["block_class"] == "error" and items[0]["error"]
    assert items[1]["block_class"] == "ok"


def test_provenance_line_survives_the_trim_contract(store):
    """_trim_results keeps url/title/content/error/blocked_by_policy — nothing else."""
    with running_server() as base:
        item = fetch(f"{base}/ok", store=store, chromium=skipped_chromium)
    trimmed = {
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "content": item.get("content", ""),
        "error": item.get("error"),
        "blocked_by_policy": item.get("blocked_by_policy", False),
    }
    assert trimmed["content"].splitlines()[0].startswith("[sluice: stage=")
    assert "status=" in trimmed["content"].splitlines()[0]


# ── extraction helpers ───────────────────────────────────────────────────────────


def test_clean_html_fallback_keeps_text_and_drops_markup():
    text = clean_html(
        "<html><head><style>x{}</style><script>bad()</script></head>"
        "<body><h1>Title</h1><p>First   paragraph.</p><div>Second</div></body></html>"
    )
    assert "Title" in text and "First paragraph." in text
    assert "bad()" not in text and "<" not in text


def test_trafilatura_empty_result_falls_back_to_clean_html():
    text = extract_text(b"<html><body><p>tiny</p></body></html>", "https://example.com")
    assert "tiny" in text


def test_host_locale_and_timezone_are_usable_strings():
    assert isinstance(host_locale(), str) and host_locale()
    assert isinstance(host_timezone(), str) and host_timezone()


def test_retry_after_cap_is_below_the_router_budget():
    assert RETRY_AFTER_CAP_SECONDS <= 5.0
