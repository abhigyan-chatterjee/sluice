"""T3 router and provider tests: tier order, budgets, fallback, cache, availability.

Backends here are doubles, not vendors: the point is the routing policy, and a double can
be made slow, failing or quota-blocked on demand. The vendor payloads themselves are covered
in ``tests/test_backends.py``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sluice.backends import BackendSpec
from sluice.config import DEFAULTS
from sluice.provider import SECRET_ENVS, SluiceWebSearchProvider
from sluice.router import (
    PER_BACKEND_BUDGET_MS,
    TOTAL_BUDGET_MS,
    Candidate,
    Router,
    classify_query,
    extract_cache_key,
    search_cache_key,
)
from sluice.store import (
    NS_EXTRACT,
    NS_EXTRACT_NEGATIVE,
    NS_SEARCH_GENERAL,
    Store,
    open_store,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend:
    """Duck-typed backend: only what the router reads."""

    def __init__(
        self,
        vendor: str,
        tier: str,
        *,
        ok: bool = True,
        error: str = "boom",
        quota: bool = False,
        delay: float = 0.0,
        missing: str = "",
        clock: FakeClock | None = None,
        cost: float = 0.0,
        results: list[dict] | None = None,
    ) -> None:
        self.spec = BackendSpec(
            id=vendor, label=vendor.title(), tiers=(tier,), search=True, extract=True
        )
        self._tier = tier
        self._ok = ok
        self._error = error
        self._quota = quota
        self._delay = delay
        self._missing = missing
        self._clock = clock
        self._cost = cost
        self._results = results
        self.calls: list[dict] = []

    def missing_message(self) -> str:
        return self._missing

    def mode(self) -> str:
        return "unavailable" if self._missing else ("local" if self._tier == "local" else "keyed")

    def tier(self, plan: str = "paid") -> str:
        return self._tier

    def cost_per_call(self) -> float:
        return self._cost

    def search(self, query: str, limit: int = 5, **kwargs):
        self.calls.append({"query": query, "limit": limit, **kwargs})
        if self._clock is not None:
            self._clock.advance(self._delay)
        if self._ok:
            rows = self._results
            if rows is None:
                rows = [
                    {"url": f"https://{self.spec.id}/1", "title": self.spec.id, "description": "d", "position": 1}
                ]
            return {"ok": True, "results": rows}
        return {"ok": False, "error": self._error, "quota": self._quota}


def settings(**overrides) -> dict:
    base = dict(DEFAULTS)
    base.update(overrides)
    return base


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "sluice.db")
    try:
        yield s
    finally:
        s.close()


def router_with(*backends: FakeBackend, store=None, clock=None, **overrides) -> Router:
    return Router(
        store=store,
        settings=settings(**overrides),
        backends={backend.spec.id: backend for backend in backends},
        clock=clock or FakeClock(),
    )


# ── tier order and candidate selection ───────────────────────────────────────────


def test_a_free_backend_beats_a_paid_one():
    free = FakeBackend("freeone", "free")
    paid = FakeBackend("paidone", "paid")
    router = router_with(paid, free)
    assert [c.id for c in router.candidates("search")] == ["freeone", "paidone"]
    result = router.search("anything")
    assert result["success"] is True
    assert result["data"]["web"][0]["backend"] == "freeone"
    assert paid.calls == []


def test_local_backend_is_tried_first():
    local = FakeBackend("searxng", "local")
    keyless = FakeBackend("ddgs", "keyless")
    router = router_with(keyless, local)
    assert [c.id for c in router.candidates("search")] == ["searxng", "ddgs"]


def test_tier_order_setting_is_honoured():
    paid = FakeBackend("paidone", "paid")
    free = FakeBackend("freeone", "free")
    router = router_with(paid, free, tier_order="paid,free")
    assert [c.id for c in router.candidates("search")] == ["paidone", "freeone"]


def test_query_classification_prefers_a_matching_vendor_inside_the_tier():
    news = FakeBackend("brave", "free")
    docs = FakeBackend("exa", "free")
    router = router_with(news, docs)
    assert [c.id for c in router.candidates("search", "latest news on the election")] == [
        "brave",
        "exa",
    ]
    assert [c.id for c in router.candidates("search", "api reference for httpx")] == [
        "exa",
        "brave",
    ]


def test_query_classification_never_promotes_paid_above_free():
    paid_news = FakeBackend("brave", "paid")
    free_general = FakeBackend("ddgs", "free")
    router = router_with(paid_news, free_general)
    order = [c.id for c in router.candidates("search", "breaking news")]
    assert order == ["ddgs", "brave"]


def test_latency_ema_orders_inside_a_tier(store):
    fast = FakeBackend("fast", "free")
    slow = FakeBackend("slow", "free")
    store.record_success("slow", latency_ms=900, now=0)
    store.record_success("fast", latency_ms=50, now=0)
    router = router_with(slow, fast, store=store)
    assert [c.id for c in router.candidates("search")] == ["fast", "slow"]


def test_missing_key_is_skipped_and_explained():
    ready = FakeBackend("ready", "free")
    unconfigured = FakeBackend("unconfigured", "paid", missing="X_API_KEY is not set.")
    router = router_with(ready, unconfigured)
    assert [c.id for c in router.candidates("search")] == ["ready"]
    reasons = router.skip_reasons("search")
    assert any("unconfigured" in reason and "X_API_KEY" in reason for reason in reasons)


def test_capability_filter_uses_the_spec_flags():
    """FakeBackend advertises both capabilities, so this checks the real registry filter."""
    real = Router(store=None, settings=settings())
    assert all(c.backend.spec.extract for c in real.candidates("extract"))
    assert all(c.backend.spec.search for c in real.candidates("search"))
    assert "brave" not in [c.id for c in real.candidates("extract")]
    assert "searxng" not in [c.id for c in real.candidates("extract")]


def test_open_circuit_is_skipped(store):
    healthy = FakeBackend("healthy", "free")
    blocked = FakeBackend("blocked", "free")
    store.record_failure("blocked", error="quota", quota=True, now=time.time())
    router = router_with(healthy, blocked, store=store)
    assert [c.id for c in router.candidates("search")] == ["healthy"]
    assert any("blocked" in reason and "quota" in reason for reason in router.skip_reasons("search"))


# ── fallback and metadata ────────────────────────────────────────────────────────


def test_a_failed_backend_falls_through_and_marks_fallback_used():
    failing = FakeBackend("a_failing", "free", ok=False, error="HTTP 500")
    backup = FakeBackend("z_backup", "free")
    router = router_with(failing, backup)
    result = router.search("q", limit=3)
    assert result["success"] is True
    row = result["data"]["web"][0]
    assert row["backend"] == "z_backup"
    assert row["tier"] == "free"
    assert row["fallback_used"] is True
    assert row["cached"] is False
    assert isinstance(row["elapsed_ms"], float)
    assert failing.calls and backup.calls


def test_a_quota_failure_falls_through_and_opens_the_circuit_for_an_hour(store):
    quota = FakeBackend("a_quota", "free", ok=False, error="quota is exhausted", quota=True)
    backup = FakeBackend("z_backup", "free")
    router = router_with(quota, backup, store=store)
    result = router.search("q")
    assert result["success"] is True
    assert result["data"]["web"][0]["backend"] == "z_backup"
    assert len(quota.calls) == 1
    state = store.backend_state("a_quota")
    assert state.quota_exhausted_until > state.last_error_at


def test_adapter_exception_is_a_failure_not_a_crash():
    class Exploding(FakeBackend):
        def search(self, query, limit=5, **kwargs):
            raise RuntimeError("kaboom")

    router = router_with(Exploding("boom", "free"), FakeBackend("ok", "free"))
    result = router.search("q")
    assert result["success"] is True
    assert result["data"]["web"][0]["backend"] == "ok"


def test_every_row_carries_the_five_metadata_keys():
    router = router_with(FakeBackend("only", "free"))
    row = router.search("q")["data"]["web"][0]
    for key in ("backend", "tier", "cached", "fallback_used", "elapsed_ms"):
        assert key in row


# ── budgets ─────────────────────────────────────────────────────────────────────


def test_per_backend_budget_is_passed_to_the_adapter():
    backend = FakeBackend("only", "free")
    router_with(backend).search("q")
    assert backend.calls[0]["timeout"] == pytest.approx(PER_BACKEND_BUDGET_MS / 1000.0)


def test_total_budget_expiry_returns_each_backends_reason():
    clock = FakeClock()
    slow = [
        FakeBackend(f"slow{index}", "free", ok=False, error=f"timeout {index}", delay=3.0, clock=clock)
        for index in range(4)
    ]
    router = router_with(*slow, clock=clock)
    result = router.search("q")
    assert result["success"] is False
    assert "no backend answered" in result["error"]
    assert "slow0: timeout 0" in result["error"]
    assert "total budget" in result["error"]


def test_an_empty_query_is_rejected_without_calling_anything():
    backend = FakeBackend("only", "free")
    result = router_with(backend).search("   ")
    assert result["success"] is False
    assert backend.calls == []


def test_a_malformed_limit_never_raises_and_falls_back_to_the_default():
    """PLAN.md:64-72 / router.search's "Never raises": a bad limit is a request, not a crash.

    The tool layer hands whatever the caller wrote straight through, so ``limit`` can be a
    string, ``None`` or a container. None of those may escape as a ValueError/TypeError.
    """
    backend = FakeBackend("only", "free")
    router = router_with(backend)
    for bad in ("not-a-number", None, {"five": 5}, [5]):
        result = router.search("zstd", limit=bad)  # type: ignore[arg-type]
        assert result["success"] is True, bad
    assert [call["limit"] for call in backend.calls] == [5, 5, 5, 5]



# ── cache ────────────────────────────────────────────────────────────────────────


def test_a_cache_hit_returns_cached_true_and_skips_the_backend(store):
    backend = FakeBackend("only", "free")
    router = router_with(backend, store=store)
    first = router.search("zstd compression", limit=4)
    assert first["data"]["web"][0]["cached"] is False
    second = router.search("zstd compression", limit=4)
    assert second["data"]["web"][0]["cached"] is True
    assert second["data"]["web"][0]["backend"] == "only"
    assert len(backend.calls) == 1


def test_news_queries_use_the_shorter_ttl_namespace(store):
    backend = FakeBackend("only", "free")
    router = router_with(backend, store=store)
    router.search("breaking news today")
    assert store.cache_count(NS_SEARCH_GENERAL) == 0
    from sluice.store import NS_SEARCH_NEWS

    assert store.cache_count(NS_SEARCH_NEWS) == 1


def test_cache_key_normalizes_case_and_whitespace():
    assert search_cache_key("  Zstd   vs gzip ", 5) == search_cache_key("zstd vs gzip", 5)
    assert extract_cache_key("https://a", "markdown") != extract_cache_key("https://a", "text")


def test_extract_is_cached_and_a_failure_uses_the_negative_namespace(store, monkeypatch):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        if url.endswith("bad"):
            return {"url": url, "title": "", "content": "", "error": "blocked",
                    "block_class": "forbidden", "stage": "http", "stages": [], "blocked_by_policy": False}
        return {"url": url, "title": "T", "content": "[sluice: stage=http status=ok]\n\nbody",
                "raw_content": "body", "block_class": "ok", "stage": "http", "stages": [],
                "blocked_by_policy": False}

    monkeypatch.setattr("sluice.ladder.fetch", fake_fetch)
    router = router_with(store=store)
    first = router.extract(["https://a/good", "https://a/bad"])
    assert first[0]["backend"] == "ladder" and first[0]["tier"] == "local"
    assert first[0]["cached"] is False and first[1]["error"] == "blocked"
    assert store.cache_count(NS_EXTRACT) == 1
    assert store.cache_count(NS_EXTRACT_NEGATIVE) == 1

    second = router.extract(["https://a/good", "https://a/bad"])
    assert second[0]["cached"] is True
    assert second[1]["cached"] is True
    assert len(calls) == 2  # the second batch was served from cache


# ── query classifier ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query,expected",
    [
        ("latest news on the election", "news"),
        ("breaking headlines today", "news"),
        ("how to install httpx", "docs"),
        ("api reference for the client", "docs"),
        ("capital of France", "general"),
        ("zstd vs gzip throughput", "general"),
    ],
)
def test_classify_query(query, expected):
    assert classify_query(query) == expected


# ── provider availability ───────────────────────────────────────────────────────


def _provider(settings_dict) -> SluiceWebSearchProvider:
    """A provider with an in-memory router: never the real plugin data dir or network."""
    provider = SluiceWebSearchProvider()
    provider._settings = dict(settings_dict)
    provider._router = Router(store=None, settings=provider._settings)
    return provider


def test_with_no_keys_and_keyless_disabled_both_are_false():
    provider = _provider({**DEFAULTS, "keyless_enabled": False, **{env: "" for env in SECRET_ENVS}})
    assert provider.is_available() is False
    assert provider.is_keyless_available() is False


def test_a_keyed_backend_makes_the_provider_available():
    base = {**DEFAULTS, **{env: "" for env in SECRET_ENVS}}
    base["EXA_API_KEY"] = "k"
    provider = _provider(base)
    assert provider.is_available() is True
    assert provider.is_keyless_available() is True


def test_keyless_alone_never_makes_is_available_true():
    provider = _provider({**DEFAULTS, "keyless_enabled": True, **{env: "" for env in SECRET_ENVS}})
    assert provider.is_available() is False
    assert provider.is_keyless_available() is True


def test_a_configured_searxng_makes_the_provider_available():
    provider = _provider(
        {**DEFAULTS, "searxng_url": "http://localhost:8080", **{env: "" for env in SECRET_ENVS}}
    )
    assert provider.is_available() is True


def test_provider_search_never_raises_when_the_router_explodes(monkeypatch):
    provider = SluiceWebSearchProvider()
    provider._settings = {**DEFAULTS, **{env: "" for env in SECRET_ENVS}}

    class Broken:
        def search(self, query, limit=5):
            raise RuntimeError("no database")

        def extract(self, urls, **kwargs):
            raise RuntimeError("no database")

    monkeypatch.setattr(provider, "router", lambda: Broken())
    result = provider.search("q")
    assert result["success"] is False and "no database" in result["error"]
    items = provider.extract(["https://a", "https://b"])
    assert [item["url"] for item in items] == ["https://a", "https://b"]
    assert all(item["error"] for item in items)


def test_provider_surfaces_a_clean_failure_when_nothing_is_routable(monkeypatch):
    provider = SluiceWebSearchProvider()
    provider._settings = {**DEFAULTS, "keyless_enabled": False, **{env: "" for env in SECRET_ENVS}}
    result = provider.search("q")
    assert result["success"] is False
    assert "no backend answered" in result["error"]


# ── malformed input: a request, never a crash ───────────────────────────────────


@pytest.mark.parametrize("query", [None, 123, ["q"], "", "   \t\n  "])
def test_an_invalid_query_is_rejected_without_calling_a_backend(query):
    backend = FakeBackend("only", "free")
    result = router_with(backend).search(query)  # type: ignore[arg-type]
    assert result == {"success": False, "error": "sluice: empty query"}
    assert backend.calls == []


def test_a_limit_below_one_is_clamped_to_one():
    backend = FakeBackend("only", "free")
    result = router_with(backend).search("zstd", limit=-5)
    assert result["success"] is True
    assert backend.calls[0]["limit"] == 1


def test_a_search_with_zero_matches_is_a_success_and_is_cached(store):
    """An empty answer is still an answer: cache it so a dead query is not re-run."""
    backend = FakeBackend("only", "free", results=[])
    router = router_with(backend, store=store)
    assert store.cache_count() == 0

    first = router.search("a query with no matches")
    assert first == {"success": True, "data": {"web": []}}
    assert store.cache_count() == 1

    second = router.search("a query with no matches")
    assert second == {"success": True, "data": {"web": []}}
    assert len(backend.calls) == 1


def test_extract_of_an_empty_list_is_an_empty_batch(store):
    assert router_with(store=store).extract([]) == []


def test_extract_turns_a_none_entry_into_one_failed_item(store, monkeypatch):
    """One bad URL never fails the batch; the item carries the error."""

    def refuses(url, **kwargs):
        raise ValueError(f"not a URL: {url!r}")

    monkeypatch.setattr("sluice.ladder.fetch", refuses)
    items = router_with(store=store).extract([None])  # type: ignore[list-item]
    assert len(items) == 1
    assert items[0]["url"] == "None"
    assert items[0]["error"] and items[0]["block_class"] == "error"


# ── budgets from malformed settings ────────────────────────────────────────────


def test_a_non_numeric_per_backend_budget_falls_back_to_the_default():
    backend = FakeBackend("only", "free")
    router = router_with(backend, per_backend_budget_ms="abc")
    assert router.search("q")["success"] is True
    assert backend.calls[0]["timeout"] == pytest.approx(PER_BACKEND_BUDGET_MS / 1000.0)


def test_a_negative_total_budget_is_clamped_and_never_raises():
    backend = FakeBackend("only", "free")
    router = router_with(backend, total_budget_ms=-10)
    result = router.search("q")
    assert result["success"] is True
    assert backend.calls[0]["timeout"] == pytest.approx(0.001)


# ── a cached answer is only valid while its backend stays routable ─────────────


class KeyedBackend(FakeBackend):
    """A keyed backend whose availability follows the settings dict, like a real adapter."""

    def __init__(self, vendor: str, tier: str, env: str, live: dict) -> None:
        super().__init__(vendor, tier)
        self.env = env
        self.live = live

    def missing_message(self) -> str:
        return "" if self.live.get(self.env) else f"{self.env} is not set."

    def mode(self) -> str:
        return "unavailable" if self.missing_message() else "keyed"


class KeylessBackend(FakeBackend):
    """A keyless-only backend whose availability follows ``keyless_enabled``."""

    def __init__(self, vendor: str, live: dict) -> None:
        super().__init__(vendor, "keyless")
        self.live = live

    def missing_message(self) -> str:
        return "" if self.live.get("keyless_enabled") else "the keyless tier is disabled."

    def mode(self) -> str:
        return "unavailable" if self.missing_message() else "keyless"


def test_a_cached_hit_is_dropped_when_the_winning_backend_loses_its_key(store):
    live = {"BRAVE_API_KEY": "k"}
    backend = KeyedBackend("brave", "free", "BRAVE_API_KEY", live)
    router = Router(store=store, settings=settings(BRAVE_API_KEY="k"), backends={"brave": backend})
    assert router.search("zstd", limit=2)["success"] is True

    live["BRAVE_API_KEY"] = ""
    second = router.search("zstd", limit=2)
    assert second["success"] is False
    assert "no backend answered" in second["error"]
    assert len(backend.calls) == 1


def test_a_cached_hit_is_dropped_when_the_keyless_tier_is_switched_off(store):
    live = {"keyless_enabled": True}
    backend = KeylessBackend("ddgs", live)
    router = Router(store=store, settings=settings(), backends={"ddgs": backend})
    assert router.search("zstd")["success"] is True

    live["keyless_enabled"] = False
    second = router.search("zstd")
    assert second["success"] is False
    assert "keyless" in second["error"]
    assert len(backend.calls) == 1


def test_a_cached_hit_is_dropped_when_the_winning_backends_circuit_opens(store):
    backend = FakeBackend("only", "free")
    router = router_with(backend, store=store)
    assert router.search("zstd")["success"] is True

    for _ in range(3):
        store.record_failure("only", error="HTTP 503")
    second = router.search("zstd")
    assert second["success"] is False
    assert "circuit" in second["error"]
    assert len(backend.calls) == 1


# ── the provider end to end, against a hermetic data directory ─────────────────


class HermeticContext:
    """A PluginContext whose only job is to hand back a temporary data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def plugin_data_dir(self) -> Path:
        return self._data_dir


def test_provider_search_and_extract_persist_in_the_context_data_dir(tmp_path, monkeypatch):
    """PLAN.md:53-72 — the provider owns a SQLite store under ``ctx.plugin_data_dir()``.

    The vendor, the ladder fetch and the settings are all injected: this proves the provider
    wiring (store, cache, metadata), not vendor behaviour, and it never reads the real
    Hermes state directory.
    """
    data_dir = tmp_path / "plugin-data"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("SLUICE_DATA_DIR", str(tmp_path / "must-not-be-used"))

    def never_the_default() -> Path:
        raise AssertionError("the provider must use ctx.plugin_data_dir(), not the fallback")

    monkeypatch.setattr("sluice.store.default_data_dir", never_the_default)

    backend = FakeBackend("exa", "keyless")

    def make_fake(vendor, settings_dict):
        assert vendor == "exa"
        return backend

    monkeypatch.setattr("sluice.router.BACKEND_MODULES", ("exa",))
    monkeypatch.setattr("sluice.router.make_backend", make_fake)

    fetches: list[str] = []

    def fake_fetch(url, **kwargs):
        fetches.append(url)
        return {
            "url": url,
            "title": "Example",
            "content": "[sluice: stage=http status=ok]\n\nbody",
            "raw_content": "body",
            "block_class": "ok",
            "stage": "http",
            "stages": [],
            "blocked_by_policy": False,
        }

    monkeypatch.setattr("sluice.ladder.fetch", fake_fetch)

    provider = SluiceWebSearchProvider(HermeticContext(data_dir))
    provider._settings = {**DEFAULTS, **{env: "" for env in SECRET_ENVS}, "keyless_enabled": True}
    try:
        result = provider.search("zstd vs gzip", limit=3)
        assert result["success"] is True
        row = result["data"]["web"][0]
        assert row["backend"] == "exa" and row["cached"] is False

        database = data_dir / "sluice.db"
        assert database.is_file(), "the provider did not open its store in the data dir"
        with open_store(data_dir, settings=provider._settings) as reopened:
            assert reopened.cache_count() >= 1
            assert reopened.backend_state("exa").calls == 1

        cached = provider.search("zstd vs gzip", limit=3)
        assert cached["data"]["web"][0]["cached"] is True
        assert len(backend.calls) == 1

        items = provider.extract(["https://example.com/a"])
        assert items[0]["title"] == "Example" and items[0]["cached"] is False
        assert provider.extract(["https://example.com/a"])[0]["cached"] is True
        assert fetches == ["https://example.com/a"]
    finally:
        provider.close()
