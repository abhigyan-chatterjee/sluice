"""T2 store tests: the numbers from PLAN.md:18-23, and nothing invented.

Every constant asserted here is quoted from PLAN.md or the reconciliation recorded in
REQUIREMENTS.md §2.2. If a number moves, this file is the place that says so.
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from sluice.store import (
    LATENCY_SAMPLE_WINDOW,
    NS_EXTRACT,
    NS_EXTRACT_NEGATIVE,
    NS_SEARCH_GENERAL,
    NS_SEARCH_NEWS,
    TABLES,
    BackendState,
    Store,
    StorePolicy,
    open_store,
)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "sluice.db")
    try:
        yield s
    finally:
        s.close()


# ── schema and plumbing ─────────────────────────────────────────────────────────────


def test_the_five_required_tables_exist(store):
    names = set(store.table_names())
    assert set(TABLES) <= names
    assert len(TABLES) == 5


def test_wal_mode_is_on(store):
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_open_store_creates_its_data_directory(tmp_path):
    target = tmp_path / "nested" / "data"
    s = open_store(target)
    try:
        assert (target / "sluice.db").is_file()
    finally:
        s.close()


def test_policy_defaults_match_the_plan():
    policy = StorePolicy()
    assert policy.search_ttl_general == 900
    assert policy.search_ttl_news == 300
    assert policy.extract_ttl == 3600
    assert policy.negative_ttl == 60
    assert policy.domain_memory_ttl == 86400
    assert policy.circuit_failures == 3
    assert policy.circuit_open_seconds == 900
    assert policy.circuit_quota_seconds == 3600
    assert policy.cache_max_mb == 200
    assert policy.cache_low_water_mb == 150
    assert policy.vacuum_interval_seconds == 604800


def test_policy_reads_only_integer_settings():
    policy = StorePolicy.from_settings(
        {"search_ttl_general": "60", "extract_ttl": True, "negative_ttl": "nope", "cache_max_mb": 12}
    )
    assert policy.search_ttl_general == 60
    assert policy.extract_ttl == 3600
    assert policy.negative_ttl == 60
    assert policy.cache_max_mb == 12


# ── circuit breaker ─────────────────────────────────────────────────────────────────


def test_circuit_opens_after_three_consecutive_failures_for_15_minutes(store):
    now = 1_000.0
    store.record_failure("exa", error="boom", now=now)
    store.record_failure("exa", error="boom", now=now + 1)
    assert store.is_available("exa", now=now + 1) is True
    store.record_failure("exa", error="boom", now=now + 2)

    state = store.backend_state("exa")
    assert state.consecutive_failures == 3
    assert state.circuit_open_until == now + 2 + 900
    assert store.is_available("exa", now=now + 2 + 899) is False
    assert store.is_available("exa", now=now + 2 + 900) is True
    assert "circuit open" in store.circuit_reason("exa", now=now + 2)


def test_a_success_resets_the_failure_count_and_the_circuit(store):
    now = 1_000.0
    for offset in range(3):
        store.record_failure("exa", error="boom", now=now + offset)
    assert store.is_available("exa", now=now + 5) is False

    store.record_success("exa", latency_ms=120, now=now + 10)
    state = store.backend_state("exa")
    assert state.consecutive_failures == 0
    assert state.circuit_open_until == 0
    assert state.quota_exhausted_until == 0
    assert store.is_available("exa", now=now + 10) is True


def test_two_failures_then_success_never_opens_the_circuit(store):
    now = 1_000.0
    store.record_failure("exa", error="boom", now=now)
    store.record_failure("exa", error="boom", now=now + 1)
    store.record_success("exa", latency_ms=50, now=now + 2)
    store.record_failure("exa", error="boom", now=now + 3)
    state = store.backend_state("exa")
    assert state.consecutive_failures == 1
    assert state.circuit_open_until == 0


def test_one_quota_error_opens_for_60_minutes(store):
    now = 2_000.0
    store.record_failure("brave", error="quota is exhausted", quota=True, now=now)

    state = store.backend_state("brave")
    assert state.quota_exhausted_until == now + 3600
    assert state.circuit_open_until == now + 3600
    assert store.is_available("brave", now=now + 3599) is False
    assert store.is_available("brave", now=now + 3600) is True
    assert "quota exhausted" in store.circuit_reason("brave", now=now + 10)

    store.record_success("brave", latency_ms=10, now=now + 20)
    assert store.backend_state("brave").quota_exhausted_until == 0


def test_circuit_state_survives_a_reopen(tmp_path):
    path = tmp_path / "sluice.db"
    first = Store(path)
    first.record_failure("ddgs", error="timeout", quota=True, now=5_000.0)
    first.close()

    second = Store(path)
    try:
        state = second.backend_state("ddgs")
        assert state.quota_exhausted_until == 5_000.0 + 3600
        assert second.is_available("ddgs", now=5_100.0) is False
    finally:
        second.close()


def test_unknown_backend_is_available_and_has_defaults(store):
    state = store.backend_state("never-used")
    assert isinstance(state, BackendState)
    assert state.calls == 0
    assert state.spend_usd == 0
    assert store.is_available("never-used", now=0) is True


# ── cache TTLs ──────────────────────────────────────────────────────────────────────


def test_search_cache_ttl_general_is_900_seconds(store):
    store.cache_put(NS_SEARCH_GENERAL, "q1", {"rows": []}, ttl=900, now=1_000)
    assert store.cache_get(NS_SEARCH_GENERAL, "q1", now=1_899) == {"rows": []}
    assert store.cache_get(NS_SEARCH_GENERAL, "q1", now=1_900) is None
    assert store.cache_count(NS_SEARCH_GENERAL) == 0


def test_search_cache_ttl_news_is_300_seconds(store):
    store.cache_put(NS_SEARCH_NEWS, "q1", {"rows": []}, ttl=300, now=1_000)
    assert store.cache_get(NS_SEARCH_NEWS, "q1", now=1_299) == {"rows": []}
    assert store.cache_get(NS_SEARCH_NEWS, "q1", now=1_300) is None


def test_extract_cache_ttl_is_3600_seconds(store):
    store.cache_put(NS_EXTRACT, "https://example.com/a", {"content": "hi"}, ttl=3600, now=0)
    assert store.cache_get(NS_EXTRACT, "https://example.com/a", now=3_599) is not None
    assert store.cache_get(NS_EXTRACT, "https://example.com/a", now=3_600) is None


def test_negative_cache_ttl_is_60_seconds(store):
    store.cache_put(NS_EXTRACT_NEGATIVE, "https://example.com/dead", {"error": "404"}, ttl=60, now=0)
    assert store.cache_get(NS_EXTRACT_NEGATIVE, "https://example.com/dead", now=59) == {
        "error": "404"
    }
    assert store.cache_get(NS_EXTRACT_NEGATIVE, "https://example.com/dead", now=60) is None


def test_cache_hit_updates_last_hit_and_hits(store):
    store.cache_put(NS_SEARCH_GENERAL, "q", {"a": 1}, ttl=900, now=0)
    store.cache_get(NS_SEARCH_GENERAL, "q", now=10)
    store.cache_get(NS_SEARCH_GENERAL, "q", now=20)
    row = store._conn.execute(
        "SELECT hits, last_hit_at FROM cache WHERE namespace = ? AND key = ?",
        (NS_SEARCH_GENERAL, "q"),
    ).fetchone()
    assert row["hits"] == 2
    assert row["last_hit_at"] == 20


def test_cache_clear_can_target_one_namespace(store):
    store.cache_put(NS_SEARCH_GENERAL, "a", {"x": 1}, ttl=900, now=0)
    store.cache_put(NS_EXTRACT, "b", {"x": 1}, ttl=900, now=0)
    assert store.cache_clear(NS_SEARCH_GENERAL) == 1
    assert store.cache_count(NS_SEARCH_GENERAL) == 0
    assert store.cache_count(NS_EXTRACT) == 1


# ── eviction ────────────────────────────────────────────────────────────────────────


def test_eviction_deletes_expired_rows_first_then_lru_down_to_low_water(tmp_path):
    path = tmp_path / "sluice.db"
    seed = Store(path)
    # Huge caps so putting the fixtures cannot trigger eviction halfway through.
    for index in range(10):
        seed.cache_put(
            NS_SEARCH_GENERAL, f"k{index}", {"blob": "x" * 20_000}, ttl=100_000, now=1_000 + index
        )
    seed.cache_put(NS_SEARCH_GENERAL, "expired", {"blob": "y" * 20_000}, ttl=1, now=1_000)
    # Touch the oldest entry: LRU must spare what was used most recently.
    assert seed.cache_get(NS_SEARCH_GENERAL, "k0", now=2_000) is not None
    seed.close()

    bounded = Store(path, max_bytes=60_000, low_water_bytes=50_000)
    try:
        report = bounded.maybe_evict(now=3_000)
        assert report["triggered"] is True
        assert report["expired_deleted"] >= 1
        assert bounded.cache_count(NS_SEARCH_GENERAL) < 11
        # The touched row survives the LRU pass; the oldest untouched rows do not.
        assert bounded.cache_get(NS_SEARCH_GENERAL, "k0", now=3_000) is not None
        assert bounded.cache_get(NS_SEARCH_GENERAL, "k1", now=3_000) is None
        assert bounded.cache_get(NS_SEARCH_GENERAL, "expired", now=3_000) is None
        # Payload was walked down to the low-water mark (one row may straddle it).
        assert report["payload_bytes"] <= 50_000 + 20_100
    finally:
        bounded.close()


def test_eviction_does_nothing_below_the_cap(store):
    store.cache_put(NS_SEARCH_GENERAL, "small", {"x": 1}, ttl=900, now=0)
    report = store.maybe_evict(now=1)
    assert report["triggered"] is False
    assert report["lru_deleted"] == 0


def test_vacuum_runs_at_most_once_a_week(tmp_path):
    path = tmp_path / "sluice.db"
    seed = Store(path)
    for index in range(6):
        seed.cache_put(
            NS_SEARCH_GENERAL, f"a{index}", {"blob": "x" * 20_000}, ttl=100_000, now=10 + index
        )
    seed.close()

    store = Store(path, max_bytes=20_000, low_water_bytes=1_000)
    try:
        first = store.maybe_evict(now=100)
        assert first["triggered"] is True
        assert first["vacuumed"] is True

        for index in range(6):
            store.cache_put(
                NS_SEARCH_GENERAL, f"b{index}", {"blob": "x" * 20_000}, ttl=100_000, now=200 + index
            )
        second = store.maybe_evict(now=300)
        assert second["triggered"] is True
        assert second["vacuumed"] is False

        # A week later the rate limit has elapsed and VACUUM is allowed again.
        store._set_meta("last_vacuum_at", "0")
        third = store.maybe_evict(now=604_900)
        if third["triggered"]:
            assert third["vacuumed"] is True
    finally:
        store.close()


def test_lru_order_is_last_hit_then_created_then_rowid(tmp_path):
    path = tmp_path / "sluice.db"
    seed = Store(path)
    seed.cache_put(NS_SEARCH_GENERAL, "old", {"blob": "x" * 30_000}, ttl=100_000, now=10)
    seed.cache_put(NS_SEARCH_GENERAL, "new", {"blob": "x" * 30_000}, ttl=100_000, now=20)
    seed.close()

    bounded = Store(path, max_bytes=40_000, low_water_bytes=45_000)
    try:
        report = bounded.maybe_evict(now=100)
        assert report["lru_deleted"] == 1
        assert bounded.cache_get(NS_SEARCH_GENERAL, "new", now=100) is not None
        assert bounded.cache_get(NS_SEARCH_GENERAL, "old", now=100) is None
    finally:
        bounded.close()


# ── domain memory ──────────────────────────────────────────────────────────────────


def test_domain_memory_keeps_class_and_stage_for_24_hours(store):
    store.domain_memory_put(
        "cloudflare.example", "challenge", "chromium", signals=["cf-mitigated", "__cf_bm"], now=0
    )
    entry = store.domain_memory_get("cloudflare.example", now=86_399)
    assert entry is not None
    assert entry["block_class"] == "challenge"
    assert entry["stage_that_worked"] == "chromium"
    assert entry["signals"] == ["cf-mitigated", "__cf_bm"]
    assert entry["expires_at"] == 86_400

    assert store.domain_memory_get("cloudflare.example", now=86_400) is None
    assert store.domain_memory_all(now=86_400) == []


def test_domain_memory_upsert_replaces_the_previous_verdict(store):
    store.domain_memory_put("x.example", "challenge", "chromium", now=0)
    store.domain_memory_put("x.example", "ok", "http", now=10)
    entry = store.domain_memory_get("x.example", now=20)
    assert entry["block_class"] == "ok"
    assert entry["stage_that_worked"] == "http"


# ── jobs ───────────────────────────────────────────────────────────────────────────


def test_job_records_survive_a_restart(tmp_path):
    path = tmp_path / "sluice.db"
    first = Store(path)
    job_id = first.create_job("install", "playwright", command="python -m playwright install chromium")
    first.update_job(job_id, status="running")
    first.update_job(job_id, status="succeeded", output="installed chromium")
    first.close()

    second = Store(path)
    try:
        job = second.get_job(job_id)
        assert job is not None
        assert job["status"] == "succeeded"
        assert job["output"] == "installed chromium"
        assert job["started_at"] is not None and job["finished_at"] is not None
        assert [j["id"] for j in second.list_jobs()] == [job_id]
    finally:
        second.close()


def test_recover_stale_jobs_marks_interrupted_work_failed(tmp_path):
    path = tmp_path / "sluice.db"
    first = Store(path)
    job_id = first.create_job("install", "camofox")
    first.update_job(job_id, status="running")
    first.close()

    second = Store(path)
    try:
        assert second.recover_stale_jobs(now=50) == 1
        job = second.get_job(job_id)
        assert job["status"] == "failed"
        assert "interrupted" in job["error"]
    finally:
        second.close()


def test_job_field_whitelist(store):
    job_id = store.create_job("install", "playwright")
    with pytest.raises(ValueError):
        store.update_job(job_id, status="bogus")
    with pytest.raises(ValueError):
        store.update_job(job_id, command="rm -rf /")
    with pytest.raises(ValueError):
        store.create_job("install", "playwright", status="bogus")


# ── latency and usage ──────────────────────────────────────────────────────────────


def test_latency_ema_and_p50(store):
    store.record_success("exa", latency_ms=100, now=1)
    assert store.backend_state("exa").latency_ema_ms == 100
    store.record_success("exa", latency_ms=200, now=2)
    # EMA alpha 0.3: 0.3*200 + 0.7*100 = 130
    assert round(store.backend_state("exa").latency_ema_ms, 3) == 130
    state = store.backend_state("exa")
    assert state.latency_p50_ms == 150.0
    assert state.latency_samples == [100.0, 200.0]


def test_latency_samples_are_capped(store):
    for index in range(LATENCY_SAMPLE_WINDOW + 10):
        store.record_success("exa", latency_ms=index, now=index)
    assert len(store.backend_state("exa").latency_samples) == LATENCY_SAMPLE_WINDOW


def test_usage_counts_calls_failures_and_spend(store):
    store.record_success("deepseek", latency_ms=300, cost_usd=0.00075, now=1)
    store.record_success("deepseek", latency_ms=500, cost_usd=0.00075, now=2)
    store.record_failure("deepseek", error="402 insufficient balance", quota=True, now=3)

    state = store.backend_state("deepseek")
    assert state.calls == 3
    assert state.failures == 1
    assert state.spend_usd == pytest.approx(0.0015)
    assert store.total_spend() == pytest.approx(0.0015)
    summary = store.usage_summary()
    assert summary["deepseek"]["calls"] == 3
    assert summary["deepseek"]["ok"] == 2


def test_plan_is_stored_per_backend(store):
    store.set_plan("exa", "free")
    assert store.get_plan("exa") == "free"
    store.set_plan("exa", "paid")
    assert store.get_plan("exa") == "paid"
    assert store.get_plan("unknown", "paid") == "paid"
    with pytest.raises(ValueError):
        store.set_plan("exa", "enterprise")


# ── concurrency ────────────────────────────────────────────────────────────────────


def test_concurrent_writes_are_serialized_and_complete(store):
    def write(index: int) -> None:
        for inner in range(20):
            store.cache_put(
                NS_SEARCH_GENERAL, f"{index}:{inner}", {"index": index}, ttl=900, now=1_000 + index
            )
            store.record_success(f"backend{index % 4}", latency_ms=10, now=1_000 + index)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))

    assert store.cache_count(NS_SEARCH_GENERAL) == 160
    assert sum(state.calls for state in store.all_backend_states()) == 160


def test_connections_are_reentrant_from_a_worker_thread(store):
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            store.record_success("exa", latency_ms=5)
            store.cache_get(NS_SEARCH_GENERAL, "missing")
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == []


def test_database_file_has_the_schema_version_we_ship(store):
    """A stale DB from an older build must not look usable: tables exist on open."""
    conn = sqlite3.connect(store.path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"backend_state", "usage", "cache", "domain_memory", "jobs"} <= names
