"""T4/T5/T6 adapter tests: recorded vendor payloads, no network.

Every vendor has at least one mocked call per mode, plus the failure mapping its
``references/endpoints.md`` section calls out. A live-call test per keyed backend is skipped
unless ``SLUICE_LIVE_TESTS=1`` — the plan's "one live call per mode behind an env flag"
(PLAN.md:80).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

from sluice.backends import (
    BACKEND_MODULES,
    all_backends,
    make_backend,
    spec,
    specs,
)
from sluice.backends._http import (
    failure,
    missing_key_message,
    parse_exa_search_text,
    parse_mcp_body,
    query_within_chars,
    retry_after_seconds,
    search_row,
)
from sluice.backends.brave import TWO_KEY_TRAP
from sluice.config import DEFAULTS, SECRET_ENVS, plan_settings


def settings(**overrides):
    base = dict(DEFAULTS)
    base.update(plan_settings())
    for env in SECRET_ENVS:
        base.setdefault(env, "")
    base.update(overrides)
    return base


KEYED = {env: f"test-{env.lower()}" for env in SECRET_ENVS}


# ── HTTP recorder ───────────────────────────────────────────────────────────────────


class HttpRecorder:
    """Replaces ``_http.new_client`` with an ``httpx.MockTransport`` client."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = None

    def respond(self, handler) -> None:
        self.handler = handler

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is None:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return self.handler(request)

    def client(self, timeout=None, headers=None) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def bodies(self) -> list[dict]:
        return [json.loads(request.read() or b"{}") for request in self.requests]


@pytest.fixture()
def http(monkeypatch):
    recorder = HttpRecorder()
    monkeypatch.setattr("sluice.backends._http.new_client", recorder.client)
    return recorder


def json_response(payload, status: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {})


def text_response(
    text: str, status: int = 200, headers: dict | None = None, content_type: str = "text/html"
) -> httpx.Response:
    merged = {"content-type": content_type}
    merged.update(headers or {})
    return httpx.Response(status, text=text, headers=merged)


def mcp_sse(text: str) -> httpx.Response:
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": text}]},
    }
    return text_response(
        f"event: message\ndata: {json.dumps(envelope)}\n\n",
        headers={"content-type": "text/event-stream"},
    )


# ── registry and configuration ─────────────────────────────────────────────────────


def test_every_vendor_has_an_adapter_class():
    for vendor in BACKEND_MODULES:
        backend = make_backend(vendor, settings())
        assert backend.spec.id == vendor


def test_all_twelve_instances_are_constructed_without_network():
    backends = all_backends(settings())
    assert len(backends) == 12
    assert all(backend.spec.id in BACKEND_MODULES for backend in backends.values())


def test_missing_key_message_names_the_env_var_and_signup_url():
    backend = make_backend("perplexity", settings())
    message = backend.missing_message()
    assert "PERPLEXITY_API_KEY is not set" in message
    assert "https://www.perplexity.ai/account/api" in message
    assert backend.mode() == "unavailable"


def test_a_keyless_vendor_has_no_missing_key_message_without_a_key():
    """Exa runs keyless, so an absent key is not an error: the mode is keyless."""
    backend = make_backend("exa", settings())
    assert backend.missing_message() == ""
    assert backend.mode() == "keyless"


def test_keyed_vendor_runs_keyed_and_reports_the_free_plan():
    backend = make_backend("exa", settings(EXA_API_KEY="k", keyless_enabled=True))
    assert backend.mode() == "keyed"
    assert backend.tier("free") == "free"
    assert backend.tier("paid") == "paid"


def test_keyless_vendor_runs_keyless_without_a_key():
    backend = make_backend("tavily", settings())
    assert backend.mode() == "keyless"
    assert backend.tier("paid") == "keyless"


def test_keyless_disabled_and_no_key_means_unavailable():
    backend = make_backend("tavily", settings(keyless_enabled=False))
    assert backend.mode() == "unavailable"


def test_searxng_needs_a_url_not_a_key():
    assert make_backend("searxng", settings()).mode() == "unavailable"
    configured = make_backend("searxng", settings(searxng_url="http://localhost:8080"))
    assert configured.mode() == "local"
    assert configured.tier() == "local"


def test_search_only_adapters_raise_not_implemented_on_extract():
    for vendor in ("searxng", "ddgs", "brave", "xai", "tinyfish"):
        with pytest.raises(NotImplementedError):
            make_backend(vendor, settings()).extract(["https://example.com"])


def test_probe_returns_the_configuration_error_without_a_request(http):
    result = make_backend("brave", settings()).probe()
    assert result["ok"] is False and result["skipped"] is True
    assert "BRAVE_API_KEY" in result["error"]
    assert http.requests == []


# ── searxng ────────────────────────────────────────────────────────────────────────


def test_searxng_search_sends_json_params_and_sorts_by_score(http):
    http.respond(
        lambda request: json_response(
            {
                "results": [
                    {"url": "https://low", "title": "Low", "content": "c", "score": 0.1},
                    {"url": "https://high", "title": "High", "content": "c", "score": 0.9},
                ]
            }
        )
    )
    backend = make_backend("searxng", settings(searxng_url="http://localhost:8080"))
    result = backend.search("zstd", limit=2)
    assert result["ok"] is True
    assert [row["url"] for row in result["results"]] == ["https://high", "https://low"]
    request = http.last
    assert request.method == "GET"
    assert request.url.path == "/search"
    assert request.url.params["q"] == "zstd"
    assert request.url.params["format"] == "json"
    assert request.url.params["pageno"] == "1"
    assert "application/json" in request.headers["accept"]


def test_searxng_explains_how_to_enable_json(http):
    http.respond(lambda request: text_response("<html>SearXNG</html>"))
    backend = make_backend("searxng", settings(searxng_url="http://localhost:8080"))
    result = backend.search("q", limit=1)
    assert result["ok"] is False
    assert "search.formats: [html, json]" in result["error"]


def test_searxng_without_url_is_a_configuration_error(http):
    result = make_backend("searxng", settings()).search("q")
    assert result["ok"] is False
    assert "SEARXNG_URL" in result["error"]
    assert http.requests == []


def test_searxng_url_env_configures_the_backend(http, monkeypatch):
    """F4: the variable the messages name is a real control, like ``CAMOFOX_URL``."""
    from sluice.config import load_settings

    monkeypatch.setenv("SEARXNG_URL", "http://env.example:8888")
    http.respond(lambda request: json_response({"results": []}))

    result = make_backend("searxng", load_settings()).search("q")

    assert result["ok"] is True, result["error"]
    assert http.last.url.host == "env.example"
    assert http.last.url.path == "/search"


# ── ddgs ──────────────────────────────────────────────────────────────────────────


def test_ddgs_maps_freshness_and_rows():
    seen = {}

    def runner(query, limit, timelimit):
        seen.update(query=query, limit=limit, timelimit=timelimit)
        return [search_row("https://a", "A", "body", 1)]

    backend = make_backend("ddgs", settings())
    backend._runner = runner
    result = backend.search("rust", limit=3, freshness="week")
    assert result["ok"] is True
    assert result["results"][0]["url"] == "https://a"
    assert seen == {"query": "rust", "limit": 3, "timelimit": "w"}


@pytest.mark.parametrize(
    "freshness,expected",
    [("day", "d"), ("week", "w"), ("month", "m"), ("year", "y"), (None, "")],
)
def test_ddgs_freshness_table(freshness, expected):
    from sluice.backends.ddgs import FRESHNESS_MAP

    if freshness is None:
        assert FRESHNESS_MAP.get("", "") == ""
    else:
        assert FRESHNESS_MAP[freshness] == expected


def test_ddgs_child_process_is_killed_at_the_wall_clock(monkeypatch):
    """A child that never answers must be killed, not waited on."""
    from sluice.backends import ddgs as ddgs_module

    monkeypatch.setattr(ddgs_module, "_CHILD_SOURCE", "import time; time.sleep(10)")
    started = time.monotonic()
    status, payload = ddgs_module.run_in_child("q", 1, "", timeout=0.4)
    elapsed = time.monotonic() - started
    assert status == "timeout"
    assert payload is None
    assert elapsed < 5.0


def test_ddgs_child_env_prepends_the_repo_root_to_pythonpath(monkeypatch):
    """The child imports ``sluice.backends._http`` from this checkout, not a stale install."""
    from sluice.backends import ddgs as ddgs_module

    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    entries = ddgs_module._child_env()["PYTHONPATH"].split(os.pathsep)
    assert entries[:2] == [repo_root, "/somewhere/else"]


def test_ddgs_search_works_under_the_hermes_plugins_loader(monkeypatch):
    """F1: the child must never have to import the loader's synthetic ``hermes_plugins``."""
    module = _load_ddgs_as_hermes_plugin()
    monkeypatch.setattr(module, "_CHILD_SOURCE", _CANNED_DDGS_CHILD)

    result = module.DdgsBackend().search("rust", limit=2, freshness="week")

    assert result["ok"] is True, result.get("error")
    assert result["results"] == [
        {
            "url": "https://example.test/row",
            "title": "rust",
            "description": "w",
            "position": 1,
        }
    ]


def _load_ddgs_as_hermes_plugin():
    """Import ``ddgs.py`` as ``hermes_plugins.sluice.sluice.backends.ddgs``.

    Mirrors ``hermes_cli/plugins_loader.py:_load_directory_module``: the plugin directory is
    the package path of ``hermes_plugins.sluice``, and ``hermes_plugins`` exists only as an
    empty namespace package in this process' ``sys.modules`` — exactly the name a spawned
    child could not import before F1 was fixed.
    """
    repo = Path(__file__).resolve().parents[1]
    name = "hermes_plugins.sluice"
    namespace = types.ModuleType("hermes_plugins")
    namespace.__path__ = []  # type: ignore[attr-defined]
    namespace.__package__ = "hermes_plugins"
    sys.modules["hermes_plugins"] = namespace

    spec = importlib.util.spec_from_file_location(
        name, repo / "__init__.py", submodule_search_locations=[str(repo)]
    )
    assert spec is not None and spec.loader is not None
    root = importlib.util.module_from_spec(spec)
    root.__package__ = name
    root.__path__ = [str(repo)]  # type: ignore[attr-defined]
    sys.modules[name] = root
    spec.loader.exec_module(root)
    return importlib.import_module(f"{name}.sluice.backends.ddgs")


_CANNED_DDGS_CHILD = """
import json
import sys

payload = json.loads(sys.stdin.read() or "{}")
row = {
    "url": "https://example.test/row",
    "title": str(payload.get("query") or ""),
    "description": str(payload.get("timelimit") or ""),
    "position": 1,
}
sys.stdout.write(json.dumps({"status": "ok", "payload": [row]}))
"""


def _capture_wall_clock(monkeypatch):
    seen: dict[str, float] = {}

    def fake_run(*args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return "ok", []

    monkeypatch.setattr("sluice.backends.ddgs.run_in_child", fake_run)
    return seen


def test_ddgs_honours_the_router_budget(monkeypatch):
    """PLAN.md:68: the 2.5 s per-backend budget must reach the child process."""
    seen = _capture_wall_clock(monkeypatch)
    result = make_backend("ddgs", settings()).search("q", timeout=1.25)
    assert result["ok"] is True
    assert seen["timeout"] == 1.25


def test_ddgs_budget_never_exceeds_the_hard_wall_clock(monkeypatch):
    seen = _capture_wall_clock(monkeypatch)
    make_backend("ddgs", settings()).search("q", timeout=30)
    assert seen["timeout"] == 12.0


def test_ddgs_direct_call_gets_the_full_wall_clock(monkeypatch):
    seen = _capture_wall_clock(monkeypatch)
    make_backend("ddgs", settings()).search("q")
    assert seen["timeout"] == 12.0


def test_ddgs_timeout_names_the_budget_it_used(monkeypatch):
    monkeypatch.setattr("sluice.backends.ddgs.run_in_child", lambda *args, **kwargs: ("timeout", None))
    result = make_backend("ddgs", settings()).search("q", timeout=2.5)
    assert result["ok"] is False
    assert result["kind"] == "timeout"
    assert "2.5s" in result["error"]


# ── exa ───────────────────────────────────────────────────────────────────────────


EXA_TEXT = (
    "Title: First result\r\nURL: https://first.example/a\r\nPublished: 2025-01-02\r\n"
    "Author: N/A\r\nHighlights:\r\n"
    "one line\r\ntwo line\r\n"
    "---\r\n"
    "Title: Second\r\nURL: https://second.example/b\r\nHighlights:\r\nthird line\r\n"
)


def test_exa_keyless_search_parses_sse_blocks(http):
    http.respond(lambda request: mcp_sse(EXA_TEXT))
    backend = make_backend("exa", settings())
    result = backend.search("q", limit=5)
    assert result["ok"] is True
    assert [row["url"] for row in result["results"]] == [
        "https://first.example/a",
        "https://second.example/b",
    ]
    assert result["results"][0]["description"] == "one line two line"
    assert result["results"][0]["date"] == "2025-01-02"
    assert "source" not in result["results"][0]  # Author: N/A is a sentinel
    body = http.bodies()[0]
    assert body["params"]["name"] == "web_search_exa"
    assert body["params"]["arguments"] == {"query": "q", "numResults": 5}


def test_exa_keyed_search_uses_x_api_key_and_rest(http):
    http.respond(
        lambda request: json_response(
            {
                "results": [
                    {
                        "url": "https://a",
                        "title": "A",
                        "highlights": ["h1", "h2"],
                        "publishedDate": "2024-05-05",
                    }
                ]
            }
        )
    )
    backend = make_backend("exa", settings(EXA_API_KEY="exa-key"))
    result = backend.search("q", limit=2)
    assert result["ok"] is True
    assert result["results"][0]["description"] == "h1 h2"
    assert result["results"][0]["date"] == "2024-05-05"
    request = http.last
    assert str(request.url) == "https://api.exa.ai/search"
    assert request.headers["x-api-key"] == "exa-key"
    assert http.bodies()[0]["contents"] == {"highlights": True}


def test_exa_keyed_extract_reports_missing_urls_per_item(http):
    http.respond(
        lambda request: json_response(
            {"results": [{"url": "https://a", "title": "A", "text": "hello"}]}
        )
    )
    backend = make_backend("exa", settings(EXA_API_KEY="exa-key"))
    items = backend.extract(["https://a", "https://b"])
    assert items[0] == {"url": "https://a", "title": "A", "content": "hello"}
    assert items[1]["url"] == "https://b" and items[1]["error"]


def test_exa_keyless_extract_fetches_one_url_at_a_time(http):
    http.respond(lambda request: mcp_sse("# Heading\n\nbody text"))
    backend = make_backend("exa", settings())
    items = backend.extract(["https://a"])
    assert items[0]["content"].startswith("# Heading")
    assert items[0]["title"] == "Heading"
    assert http.bodies()[0]["params"]["arguments"] == {"urls": ["https://a"]}


def test_exa_text_parser_normalizes_crlf_and_skips_na():
    rows = parse_exa_search_text("Title: T\r\nURL: https://x\r\nAuthor: N/A\r\nHighlights:\r\nhi")
    assert rows[0]["url"] == "https://x"
    assert "source" not in rows[0]


# ── parallel ──────────────────────────────────────────────────────────────────────


def test_parallel_keyless_search_uses_mcp_with_session_id_and_no_model_name(http):
    payload = {"results": [{"url": "https://a", "title": "A", "excerpts": ["e1", "e2"]}]}
    http.respond(lambda request: mcp_sse(json.dumps(payload)))
    backend = make_backend("parallel", settings())
    result = backend.search("q", limit=3)
    assert result["ok"] is True
    assert result["results"][0]["description"] == "e1 e2"
    arguments = http.bodies()[0]["params"]["arguments"]
    assert arguments["objective"] == "q"
    assert arguments["search_queries"] == ["q"]
    assert arguments["session_id"]
    assert "model_name" not in arguments


def test_parallel_keyed_search_uses_rest(http):
    http.respond(
        lambda request: json_response(
            {"results": [{"url": "https://a", "title": "A", "excerpts": ["e"], "publish_date": "2024-01-01"}]}
        )
    )
    backend = make_backend("parallel", settings(PARALLEL_API_KEY="p-key"))
    result = backend.search("q", limit=3)
    assert result["ok"] is True
    assert result["results"][0]["date"] == "2024-01-01"
    assert str(http.last.url) == "https://api.parallel.ai/v1/search"
    assert http.last.headers["x-api-key"] == "p-key"
    assert http.bodies()[0]["mode"] == "fast"


def test_parallel_extract_handles_results_errors_and_dropped_urls(http):
    payload = {
        "results": [{"url": "https://a", "title": "A", "full_content": "full"}],
        "errors": [{"url": "https://b", "error_type": "FETCH_FAILED"}],
    }
    http.respond(lambda request: mcp_sse(json.dumps(payload)))
    items = make_backend("parallel", settings()).extract(["https://a", "https://b", "https://c"])
    assert items[0]["content"] == "full"
    assert items[1]["error"] == "FETCH_FAILED"
    assert items[2]["url"] == "https://c" and items[2]["error"]


# ── firecrawl ─────────────────────────────────────────────────────────────────────


def test_firecrawl_keyless_never_sends_authorization(http):
    http.respond(
        lambda request: json_response(
            {"data": {"web": [{"url": "https://a", "title": "A", "description": "d"}]}}
        )
    )
    result = make_backend("firecrawl", settings()).search("q", limit=2)
    assert result["ok"] is True
    assert "authorization" not in {key.lower() for key in http.last.headers}
    assert http.bodies()[0] == {"query": "q", "limit": 2}


def test_firecrawl_keyed_uses_bearer_and_accepts_data_list(http):
    http.respond(
        lambda request: json_response(
            {"data": [{"url": "https://a", "title": "A"}]} if request.url.path.endswith("search") else {}
        )
    )
    backend = make_backend("firecrawl", settings(FIRECRAWL_API_KEY="fc"))
    result = backend.search("q", limit=2)
    assert result["results"][0]["url"] == "https://a"
    assert http.last.headers["authorization"] == "Bearer fc"


def test_firecrawl_scrape_reads_markdown_and_metadata(http):
    http.respond(
        lambda request: json_response(
            {"data": {"markdown": "# Body", "metadata": {"title": "T", "sourceURL": "https://final"}}}
        )
    )
    items = make_backend("firecrawl", settings()).extract(["https://a"])
    assert items == [{"url": "https://final", "title": "T", "content": "# Body"}]


# ── tavily ────────────────────────────────────────────────────────────────────────


def test_tavily_keyless_headers(http):
    http.respond(lambda request: json_response({"results": []}))
    make_backend("tavily", settings()).search("q")
    headers = {key.lower(): value for key, value in http.last.headers.items()}
    assert headers["x-client-name"] == "hermes-agent"
    assert headers["x-tavily-access-mode"] == "keyless"
    assert "authorization" not in headers


def test_tavily_keyed_headers_and_base_override(http):
    http.respond(lambda request: json_response({"results": []}))
    backend = make_backend(
        "tavily", settings(TAVILY_API_KEY="tv", TAVILY_BASE_URL="https://tv.internal")
    )
    backend.search("q")
    headers = {key.lower(): value for key, value in http.last.headers.items()}
    assert headers["authorization"] == "Bearer tv"
    assert headers["x-client-name"] == "hermes-agent"
    assert "x-tavily-access-mode" not in headers
    assert str(http.last.url).startswith("https://tv.internal/search")


def test_tavily_extract_surfaces_failed_results_and_urls(http):
    http.respond(
        lambda request: json_response(
            {
                "results": [{"url": "https://a", "title": "A", "raw_content": "text"}],
                "failed_results": [{"url": "https://b", "error": "blocked"}],
                "failed_urls": ["https://c"],
            }
        )
    )
    items = make_backend("tavily", settings()).extract(["https://a", "https://b", "https://c"])
    assert items[0]["content"] == "text"
    assert items[1]["error"] == "blocked"
    assert items[2]["error"] == "extraction failed"


def test_tavily_429_with_retry_after_and_no_credit_wording_retries_once(http):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return json_response({"detail": "slow down"}, status=429, headers={"Retry-After": "0"})
        return json_response({"results": [{"url": "https://a", "title": "A"}]})

    http.respond(handler)
    result = make_backend("tavily", settings()).search("q")
    assert result["ok"] is True
    assert calls["n"] == 2


def test_tavily_429_with_quota_wording_is_a_quota_failure_without_retry(http):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response(
            {"detail": "You have exceeded your monthly quota of credits"}, status=429
        )

    http.respond(handler)
    result = make_backend("tavily", settings()).search("q")
    assert result["ok"] is False
    assert result["quota"] is True and result["out_of_credit"] is True
    assert calls["n"] == 1
    assert "quota is exhausted" in result["error"]


# ── keenable ──────────────────────────────────────────────────────────────────────


def test_keenable_public_paths_and_title_header(http):
    http.respond(lambda request: json_response({"results": [{"url": "https://a", "title": "A"}]}))
    backend = make_backend("keenable", settings())
    backend.search("q", limit=2)
    assert http.last.url.path == "/v1/search/public"
    assert http.last.headers["x-keenable-title"] == "hermes-agent"
    assert "authorization" not in {key.lower() for key in http.last.headers}


def test_keenable_keyed_paths_and_fetch(http):
    def handler(request):
        if request.method == "POST":
            return json_response({"results": []})
        return json_response({"url": "https://a", "title": "A", "content": "body"})

    http.respond(handler)
    backend = make_backend("keenable", settings(KEENABLE_API_KEY="kk"))
    backend.search("q")
    assert http.last.url.path == "/v1/search"
    assert http.last.headers["authorization"] == "Bearer kk"
    items = backend.extract(["https://a"])
    assert http.last.url.path == "/v1/fetch"
    assert items[0]["content"] == "body"
    assert http.last.url.params["url"] == "https://a"


# ── brave ─────────────────────────────────────────────────────────────────────────


def test_brave_caps_count_at_20_and_sends_subscription_token(http):
    http.respond(lambda request: json_response({"web": {"results": [{"url": "https://a", "title": "A", "description": "d"}]}}))
    result = make_backend("brave", settings(BRAVE_API_KEY="bv")).search("q", limit=50)
    assert result["ok"] is True
    assert http.last.url.params["count"] == "20"
    assert http.last.headers["x-subscription-token"] == "bv"


def test_brave_two_key_trap_is_explained(http):
    backend = make_backend("brave", settings(BRAVE_SEARCH_API_KEY="free-key"))
    message = backend.missing_message()
    assert message == TWO_KEY_TRAP
    assert "BRAVE_SEARCH_API_KEY" in message and "BRAVE_API_KEY" in message
    assert http.requests == []


def test_brave_422_subscription_token_invalid_is_a_bad_key(http):
    http.respond(
        lambda request: json_response(
            {"error": {"code": "SUBSCRIPTION_TOKEN_INVALID", "detail": "invalid"}}, status=422
        )
    )
    result = make_backend("brave", settings(BRAVE_API_KEY="bad")).search("q")
    assert result["ok"] is False
    assert "SUBSCRIPTION_TOKEN_INVALID" in result["error"]
    assert result["kind"] == "auth"


def test_brave_402_usage_limit_is_a_spent_allowance(http):
    http.respond(
        lambda request: json_response(
            {"error": {"code": "USAGE_LIMIT_EXCEEDED", "detail": "spent"}}, status=402
        )
    )
    result = make_backend("brave", settings(BRAVE_API_KEY="b")).search("q")
    assert result["quota"] is True and result["out_of_credit"] is True
    assert "free allowance is spent" in result["error"]


# ── perplexity ────────────────────────────────────────────────────────────────────


def test_perplexity_search_body_uses_low_context(http):
    http.respond(lambda request: json_response({"results": [{"url": "https://a", "title": "A", "snippet": "s"}]}))
    result = make_backend("perplexity", settings(PERPLEXITY_API_KEY="pp")).search("q", limit=3)
    assert result["results"][0]["description"] == "s"
    body = http.bodies()[0]
    assert body == {"query": "q", "max_results": 3, "search_context_size": "low"}
    assert http.last.headers["authorization"] == "Bearer pp"


def test_perplexity_extract_marks_missing_text_per_url(http):
    http.respond(
        lambda request: json_response(
            {"results": [{"url": "https://a", "text": "snippet"}, {"url": "https://b", "error": "blocked"}]}
        )
    )
    items = make_backend("perplexity", settings(PERPLEXITY_API_KEY="pp")).extract(
        ["https://a", "https://b", "https://c"]
    )
    assert items[0]["content"] == "snippet"
    assert items[1]["error"] == "blocked"
    assert items[2]["error"] == "no content returned"
    assert http.bodies()[0]["max_tokens"] == 16384
    assert http.bodies()[0]["max_tokens_per_page"] == 4096


def test_perplexity_query_is_derived_from_url_words():
    from sluice.backends.perplexity import query_for_urls

    assert query_for_urls(["https://example.com/bloom-filter"]) == "bloom filter"
    assert query_for_urls(["https://example.com"]) == "https://example.com"


# ── deepseek ──────────────────────────────────────────────────────────────────────


def test_deepseek_clips_the_query_on_a_word_boundary_and_reports_it(http):
    http.respond(
        lambda request: json_response(
            {"results": [{"url": "https://a", "title": "A", "summary": "short summary"}]}
        )
    )
    query = "alpha beta gamma " * 20
    result = make_backend("deepseek", settings(DEEPSEEK_API_KEY="ds")).search(query, limit=2)
    assert result["ok"] is True
    assert result["query_truncated"] is True
    sent = http.bodies()[0]["query"]
    assert len(sent) <= 128
    assert not sent.endswith(" ")
    assert sent.split(" ")[-1] in ("alpha", "beta", "gamma")
    assert result["results"][0]["description"] == "short summary"
    assert result["cost_usd"] == 0.00075


def test_deepseek_short_query_is_not_truncated(http):
    http.respond(lambda request: json_response({"results": []}))
    result = make_backend("deepseek", settings(DEEPSEEK_API_KEY="ds")).search("short query")
    assert result["query_truncated"] is False
    body = http.bodies()[0]
    assert body["ori_question"] == "short query"
    assert body["need_content"] is False
    assert body["localization"] == ""


def test_deepseek_402_is_insufficient_balance(http):
    http.respond(lambda request: json_response({"error": "insufficient balance"}, status=402))
    result = make_backend("deepseek", settings(DEEPSEEK_API_KEY="ds")).search("q")
    assert result["ok"] is False and result["quota"] is True


def test_deepseek_404_and_403_are_named(http):
    http.respond(lambda request: json_response({"error": "no content"}, status=404))
    result = make_backend("deepseek", settings(DEEPSEEK_API_KEY="ds")).search("q")
    assert "no usable results" in result["error"]

    http.respond(lambda request: json_response({"error": "policy"}, status=403))
    result = make_backend("deepseek", settings(DEEPSEEK_API_KEY="ds")).search("q")
    assert "policy grounds" in result["error"]


def test_deepseek_extract_is_one_url_per_request(http):
    http.respond(lambda request: json_response({"title": "T", "content": "body"}))
    items = make_backend("deepseek", settings(DEEPSEEK_API_KEY="ds")).extract(
        ["https://a", "https://b"]
    )
    assert len(http.requests) == 2
    assert [item["content"] for item in items] == ["body", "body"]
    assert [json.loads(request.read())["url"] for request in http.requests] == [
        "https://a",
        "https://b",
    ]


# ── tinyfish ──────────────────────────────────────────────────────────────────────


def test_tinyfish_search_params_and_source_fields(http):
    http.respond(
        lambda request: json_response(
            {
                "results": [
                    {
                        "url": "https://a",
                        "title": "A",
                        "snippet": "s",
                        "date": "2024-02-02",
                        "site_name": "Example",
                        "position": 1,
                    }
                ],
                "total_results": 1,
                "page": 1,
            }
        )
    )
    result = make_backend("tinyfish", settings(TINYFISH_API_KEY="tf")).search("q", limit=5)
    assert result["results"][0]["source"] == "Example"
    assert result["total_results"] == 1
    assert http.last.url.params["domain_type"] == "web"
    assert http.last.headers["x-api-key"] == "tf"


def test_tinyfish_401_invalid_api_key_is_actionable(http):
    http.respond(lambda request: json_response({"error_code": "INVALID_API_KEY"}, status=401))
    result = make_backend("tinyfish", settings(TINYFISH_API_KEY="bad")).search("q")
    assert result["ok"] is False
    assert "INVALID_API_KEY" in result["error"]
    assert "TINYFISH_API_KEY" in result["error"]


# ── xai ───────────────────────────────────────────────────────────────────────────


def test_xai_parses_the_json_block_first(http):
    text = 'Here you go:\n{"results": [{"title": "A", "url": "https://a", "description": "d"}]}'
    http.respond(
        lambda request: json_response(
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
        )
    )
    result = make_backend("xai", settings(XAI_API_KEY="xk")).search("q", limit=3)
    assert result["parse_mode"] == "json"
    assert result["results"][0] == {
        "url": "https://a",
        "title": "A",
        "description": "d",
        "position": 1,
    }
    body = http.bodies()[0]
    assert body["model"] == "grok-build-0.1"
    assert body["tools"] == [{"type": "web_search"}]
    assert body["include"] == ["no_inline_citations"]


def test_xai_falls_back_to_url_citation_annotations(http):
    http.respond(
        lambda request: json_response(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "A sentence about the topic.",
                                "annotations": [
                                    {"type": "url_citation", "url": "https://a", "start_index": 0, "end_index": 5}
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )
    result = make_backend("xai", settings(XAI_API_KEY="xk")).search("q")
    assert result["parse_mode"] == "annotations"
    assert result["results"][0]["url"] == "https://a"


def test_xai_falls_back_to_the_citations_list(http):
    http.respond(lambda request: json_response({"output": [], "citations": ["https://a", "https://b"]}))
    result = make_backend("xai", settings(XAI_API_KEY="xk")).search("q")
    assert result["parse_mode"] == "citations"
    assert [row["url"] for row in result["results"]] == ["https://a", "https://b"]


def test_xai_error_envelope_is_not_reported_as_success(http):
    http.respond(lambda request: json_response({"error": {"message": "overloaded"}}))
    result = make_backend("xai", settings(XAI_API_KEY="xk")).search("q")
    assert result["ok"] is False
    assert "overloaded" in result["error"]


# ── shared helpers ────────────────────────────────────────────────────────────────


def test_missing_key_message_shape():
    message = missing_key_message("TAVILY_API_KEY", "tavily")
    assert message.startswith("TAVILY_API_KEY is not set. Get a key at ")


def test_retry_after_parses_seconds_and_dates():
    assert retry_after_seconds({"Retry-After": "2"}) == 2.0
    assert retry_after_seconds({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}) is not None
    assert retry_after_seconds({}) is None


def test_query_within_chars_hard_slices_a_single_long_token():
    clipped, truncated = query_within_chars("x" * 200, 128)
    assert truncated is True
    assert len(clipped) == 128


def test_parse_mcp_body_rejects_an_error_envelope():
    body = json.dumps({"jsonrpc": "2.0", "error": {"message": "rate limited"}})
    text, error = parse_mcp_body(body)
    assert text is None
    assert "rate limited" in error["error"]


def test_parse_mcp_body_reads_plain_json_and_sse():
    plain = json.dumps({"result": {"content": [{"type": "text", "text": "plain"}]}})
    assert parse_mcp_body(plain)[0] == "plain"
    sse = "event: message\ndata: " + plain + "\n\n"
    assert parse_mcp_body(sse)[0] == "plain"


def test_transport_errors_never_escape_the_adapter(monkeypatch):
    def explode(request):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(
        "sluice.backends._http.new_client",
        lambda timeout=None, headers=None: httpx.Client(transport=httpx.MockTransport(explode)),
    )
    result = make_backend("exa", settings(EXA_API_KEY="k")).search("q")
    assert result["ok"] is False
    assert "request failed" in result["error"]


def test_every_vendor_spec_has_a_summary_and_a_task():
    for vendor in specs().values():
        assert vendor.summary
        assert vendor.task in ("T4", "T5", "T6")


@pytest.mark.skipif(
    os.environ.get("SLUICE_LIVE_TESTS") != "1",
    reason="live vendor calls: set SLUICE_LIVE_TESTS=1 (PLAN.md:80)",
)
@pytest.mark.parametrize("vendor", ["exa", "parallel", "firecrawl", "tavily", "keenable", "brave", "deepseek", "tinyfish", "xai", "perplexity"])
def test_live_search_against_the_real_vendor(vendor):
    live_settings = settings()
    for env in SECRET_ENVS:
        live_settings[env] = os.environ.get(env, "")
    backend = make_backend(vendor, live_settings)
    if backend.mode() == "unavailable":
        pytest.skip(f"{vendor} has no credentials in this environment")
    result = backend.search("hermes agent plugin", limit=2)
    assert isinstance(result, dict)
    assert result["ok"] is True, result.get("error")
