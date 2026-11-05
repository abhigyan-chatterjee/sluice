"""SQLite state and caches in the plugin data directory.

Contract: PLAN.md:56-62. Five tables (``backend_state``, ``usage``, ``cache``,
``domain_memory``, ``jobs``), thread-safe connections, WAL, no ORM, and the cache/circuit
numbers from PLAN.md:18-23 implemented exactly. A sixth internal ``meta`` table holds
maintenance bookkeeping (the last VACUUM time) so the weekly VACUUM rate limit survives a
restart.

Shared state, never a second copy: the router, the fetch ladder, the CLI and the dashboard
API all open one ``Store`` on the same file. Writes are serialized on one connection
guarded by a re-entrant lock; WAL lets readers (the pane polling ``/status``) read while a
search writes.

Numbers, all overridable through ``plugin.yaml config_schema``:

===========================  =======  ============================================
Search cache TTL, general    900 s    PLAN.md:18
Search cache TTL, news       300 s    PLAN.md:18
Extract cache TTL            3600 s   PLAN.md:19
Negative cache (failed URL)  60 s     PLAN.md:19-20
Domain memory TTL            86400 s  PLAN.md:20
Failures that open circuit   3        PLAN.md:20
Circuit open after failures  900 s    PLAN.md:20
Circuit open after quota     3600 s   PLAN.md:20-21
Eviction high-water mark     200 MB   PLAN.md:22 (resolved in REQUIREMENTS.md §2.2)
Eviction low-water mark      150 MB   PLAN.md:22
VACUUM interval              604800 s PLAN.md:23
===========================  =======  ============================================
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Table names fixed by PLAN.md:59. ``meta`` is maintenance bookkeeping, not user state.
TABLES: tuple[str, ...] = ("backend_state", "usage", "cache", "domain_memory", "jobs")

#: Namespaces used by the router and the ladder. Constants stop a typo from silently
#: creating a second cache that never gets a hit.
NS_SEARCH_GENERAL = "search:general"
NS_SEARCH_NEWS = "search:news"
NS_EXTRACT = "extract"
NS_EXTRACT_NEGATIVE = "extract:negative"
NS_ROBOTS = "robots"

#: Latency samples kept per backend for the p50 in ``hermes sluice status`` and the pane.
LATENCY_SAMPLE_WINDOW = 20
LATENCY_EMA_ALPHA = 0.3


@dataclass(frozen=True)
class StorePolicy:
    """Defaults fixed by PLAN.md:18-23, overridable through ``plugin.yaml config_schema``."""

    search_ttl_general: int = 900
    search_ttl_news: int = 300
    extract_ttl: int = 3600
    negative_ttl: int = 60
    domain_memory_ttl: int = 86400
    circuit_failures: int = 3
    circuit_open_seconds: int = 900
    circuit_quota_seconds: int = 3600
    cache_max_mb: int = 200
    #: PLAN.md:22 "delete ... rows down to 200 MB" cannot be literal when the trigger is
    #: 150 MB; REQUIREMENTS.md §2.2 resolves the pair as a hysteresis band, so 150 MB is
    #: the low-water mark that makes the plan's second number do real work.
    cache_low_water_mb: int = 150
    #: PLAN.md:23: one VACUUM per week, at most.
    vacuum_interval_seconds: int = 604800

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> "StorePolicy":
        """Build from plugin settings, ignoring absent or non-integer values."""
        settings = settings or {}
        kwargs: dict[str, int] = {}
        for field_name in cls.__dataclass_fields__:
            raw = settings.get(field_name)
            if raw is None or isinstance(raw, bool):
                continue
            try:
                kwargs[field_name] = int(raw)
            except (TypeError, ValueError):
                continue
        return cls(**kwargs)


@dataclass
class BackendState:
    """One backend's persisted health. ``calls``/``failures``/``spend_usd`` come from usage."""

    backend: str
    plan: str = "paid"
    circuit_open_until: float = 0.0
    consecutive_failures: int = 0
    quota_exhausted_until: float = 0.0
    latency_ema_ms: float | None = None
    latency_p50_ms: float | None = None
    latency_samples: list[float] = field(default_factory=list)
    last_error: str | None = None
    last_error_at: float | None = None
    last_ok_at: float | None = None
    calls: int = 0
    failures: int = 0
    spend_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "plan": self.plan,
            "circuit_open_until": self.circuit_open_until,
            "consecutive_failures": self.consecutive_failures,
            "quota_exhausted_until": self.quota_exhausted_until,
            "latency_ema_ms": self.latency_ema_ms,
            "latency_p50_ms": self.latency_p50_ms,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_ok_at": self.last_ok_at,
            "calls": self.calls,
            "failures": self.failures,
            "spend_usd": self.spend_usd,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS backend_state (
    backend                TEXT PRIMARY KEY,
    plan                   TEXT NOT NULL DEFAULT 'paid',
    circuit_open_until     REAL NOT NULL DEFAULT 0,
    consecutive_failures   INTEGER NOT NULL DEFAULT 0,
    quota_exhausted_until  REAL NOT NULL DEFAULT 0,
    latency_ema_ms         REAL,
    latency_samples        TEXT NOT NULL DEFAULT '[]',
    last_error             TEXT,
    last_error_at          REAL,
    last_ok_at             REAL,
    updated_at             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    backend    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    latency_ms REAL,
    cost_usd   REAL NOT NULL DEFAULT 0,
    at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_backend_at ON usage(backend, at);
CREATE INDEX IF NOT EXISTS usage_at ON usage(at);

CREATE TABLE IF NOT EXISTS cache (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    last_hit_at REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    expires_at  REAL NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS cache_expiry ON cache(expires_at);
CREATE INDEX IF NOT EXISTS cache_lru ON cache(last_hit_at, created_at);

CREATE TABLE IF NOT EXISTS domain_memory (
    domain             TEXT PRIMARY KEY,
    block_class        TEXT NOT NULL,
    stage_that_worked  TEXT,
    signals            TEXT NOT NULL DEFAULT '',
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    expires_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS domain_memory_expiry ON domain_memory(expires_at);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL,
    status      TEXT NOT NULL,
    command     TEXT,
    output      TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_JOB_STATUSES = frozenset({"queued", "running", "succeeded", "failed"})
_JOB_TEXT_FIELDS = frozenset({"status", "output", "error"})


class Store:
    """Thread-safe SQLite store. Every public method takes the lock; no ORM in between."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        policy: StorePolicy | None = None,
        *,
        max_bytes: int | None = None,
        low_water_bytes: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy or StorePolicy()
        self._max_bytes = int(
            max_bytes if max_bytes is not None else self.policy.cache_max_mb * 1024 * 1024
        )
        low = (
            low_water_bytes
            if low_water_bytes is not None
            else self.policy.cache_low_water_mb * 1024 * 1024
        )
        # A low-water mark above the cap would make one write delete an unbounded number of
        # rows; clamp it so the band always points downward.
        self._low_water_bytes = max(0, min(int(low), self._max_bytes))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=15, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=15000")
            self._conn.executescript(_SCHEMA)

    # ── lifecycle ───────────────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def table_names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row["name"] for row in rows]

    # ── internals ───────────────────────────────────────────────────────────────────

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def _db_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(str(self.path) + suffix)
            except OSError:
                continue
        return total

    def _payload_bytes(self) -> int:
        """Approximate live payload; the SQLite file itself does not shrink on DELETE."""
        with self._lock:
            cache = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(value)), 0) AS n FROM cache"
            ).fetchone()["n"]
            jobs = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(COALESCE(output, ''))), 0) AS n FROM jobs"
            ).fetchone()["n"]
            memory = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(signals)), 0) AS n FROM domain_memory"
            ).fetchone()["n"]
        return int(cache) + int(jobs) + int(memory)

    def _get_meta(self, key: str) -> str | None:
        row = self._execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ── cache ───────────────────────────────────────────────────────────────────────

    def cache_put(
        self,
        namespace: str,
        key: str,
        value: Mapping[str, Any] | list[Any],
        ttl: float,
        *,
        now: float | None = None,
    ) -> None:
        """Store one JSON value; triggers eviction when the file is over the cap."""
        stamp = time.time() if now is None else float(now)
        blob = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._execute(
                "INSERT INTO cache(namespace, key, value, created_at, last_hit_at, hits, expires_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET "
                "value = excluded.value, expires_at = excluded.expires_at, "
                "created_at = excluded.created_at, last_hit_at = excluded.last_hit_at, hits = 0",
                (namespace, key, blob, stamp, stamp, stamp + max(0.0, float(ttl))),
            )
            self.maybe_evict(now=stamp)

    def cache_get(
        self, namespace: str, key: str, *, now: float | None = None
    ) -> dict[str, Any] | None:
        """Return a live cached value, or None. A hit updates ``last_hit_at`` and ``hits``."""
        stamp = time.time() if now is None else float(now)
        with self._lock:
            row = self._execute(
                "SELECT value, expires_at FROM cache WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= stamp:
                self._execute(
                    "DELETE FROM cache WHERE namespace = ? AND key = ?", (namespace, key)
                )
                return None
            self._execute(
                "UPDATE cache SET last_hit_at = ?, hits = hits + 1 WHERE namespace = ? AND key = ?",
                (stamp, namespace, key),
            )
        try:
            decoded = json.loads(row["value"])
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else {"value": decoded}

    def cache_clear(self, namespace: str | None = None) -> int:
        with self._lock:
            if namespace is None:
                cur = self._execute("DELETE FROM cache")
            else:
                cur = self._execute("DELETE FROM cache WHERE namespace = ?", (namespace,))
            return int(cur.rowcount or 0)

    def cache_count(self, namespace: str | None = None) -> int:
        with self._lock:
            if namespace is None:
                row = self._execute("SELECT COUNT(*) AS n FROM cache").fetchone()
            else:
                row = self._execute(
                    "SELECT COUNT(*) AS n FROM cache WHERE namespace = ?", (namespace,)
                ).fetchone()
        return int(row["n"])

    def cache_stats(self) -> dict[str, Any]:
        with self._lock:
            rows = self._execute(
                "SELECT namespace, COUNT(*) AS rows, "
                "COALESCE(SUM(LENGTH(value)), 0) AS payload, "
                "MIN(created_at) AS oldest, MAX(last_hit_at) AS newest "
                "FROM cache GROUP BY namespace ORDER BY namespace"
            ).fetchall()
            now = time.time()
            live = self._execute(
                "SELECT COUNT(*) AS n FROM cache WHERE expires_at > ?", (now,)
            ).fetchone()["n"]
        return {
            "size_bytes": self._db_size_bytes(),
            "max_bytes": self._max_bytes,
            "low_water_bytes": self._low_water_bytes,
            "rows": sum(int(r["rows"]) for r in rows),
            "live_rows": int(live),
            "namespaces": {
                r["namespace"]: {
                    "rows": int(r["rows"]),
                    "payload_bytes": int(r["payload"]),
                    "oldest": r["oldest"],
                    "newest_hit": r["newest"],
                }
                for r in rows
            },
        }

    # ── eviction ────────────────────────────────────────────────────────────────────

    def maybe_evict(self, *, now: float | None = None) -> dict[str, Any]:
        """Evict when the store is over the cap: expired rows first, then LRU to low water.

        The trigger is the on-disk size (the plan's cap is a file size). SQLite does not
        return deleted pages to the filesystem on its own, so the LRU phase walks the
        payload down to the low-water mark and then checkpoints. VACUUM, the only thing
        that shrinks the file, runs at most once per ``vacuum_interval_seconds``.
        """
        stamp = time.time() if now is None else float(now)
        size = self._db_size_bytes()
        report: dict[str, Any] = {
            "triggered": False,
            "size_bytes": size,
            "max_bytes": self._max_bytes,
            "expired_deleted": 0,
            "lru_deleted": 0,
            "vacuumed": False,
        }
        if size <= self._max_bytes:
            return report

        report["triggered"] = True
        with self._lock:
            expired = self._execute("DELETE FROM cache WHERE expires_at <= ?", (stamp,))
            report["expired_deleted"] = int(expired.rowcount or 0)
            expired_memory = self._execute(
                "DELETE FROM domain_memory WHERE expires_at <= ?", (stamp,)
            )
            report["expired_deleted"] += int(expired_memory.rowcount or 0)

            deleted = 0
            payload = self._payload_bytes()
            if payload > self._low_water_bytes:
                rows = self._execute(
                    "SELECT namespace, key, LENGTH(value) AS n FROM cache "
                    "ORDER BY last_hit_at ASC, created_at ASC, rowid ASC"
                ).fetchall()
                doomed: list[tuple[str, str]] = []
                for row in rows:
                    if payload <= self._low_water_bytes:
                        break
                    doomed.append((row["namespace"], row["key"]))
                    payload -= int(row["n"])
                if doomed:
                    self._conn.executemany(
                        "DELETE FROM cache WHERE namespace = ? AND key = ?", doomed
                    )
                    deleted = len(doomed)
            report["lru_deleted"] = deleted
            self._execute("PRAGMA wal_checkpoint(TRUNCATE)")

            last_vacuum = self._get_meta("last_vacuum_at")
            due = (
                last_vacuum is None
                or stamp - float(last_vacuum) >= self.policy.vacuum_interval_seconds
            )
            if due and self._db_size_bytes() > self._max_bytes:
                self._conn.execute("VACUUM")
                self._set_meta("last_vacuum_at", str(stamp))
                report["vacuumed"] = True

        report["size_bytes"] = self._db_size_bytes()
        report["payload_bytes"] = self._payload_bytes()
        return report

    # ── backend state, circuit and latency ──────────────────────────────────────────

    def _row_to_state(self, row: sqlite3.Row | None, backend: str) -> BackendState:
        if row is None:
            return BackendState(backend=backend)
        try:
            decoded = json.loads(row["latency_samples"] or "[]")
            samples = [float(x) for x in decoded][-LATENCY_SAMPLE_WINDOW:]
        except (TypeError, ValueError):
            samples = []
        return BackendState(
            backend=backend,
            plan=row["plan"] or "paid",
            circuit_open_until=float(row["circuit_open_until"] or 0),
            consecutive_failures=int(row["consecutive_failures"] or 0),
            quota_exhausted_until=float(row["quota_exhausted_until"] or 0),
            latency_ema_ms=row["latency_ema_ms"],
            latency_p50_ms=_p50(samples),
            latency_samples=samples,
            last_error=row["last_error"],
            last_error_at=row["last_error_at"],
            last_ok_at=row["last_ok_at"],
        )

    def backend_state(self, backend: str) -> BackendState:
        with self._lock:
            row = self._execute(
                "SELECT * FROM backend_state WHERE backend = ?", (backend,)
            ).fetchone()
            state = self._row_to_state(row, backend)
            usage = self._execute(
                "SELECT COUNT(*) AS calls, "
                "COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failures, "
                "COALESCE(SUM(cost_usd), 0) AS spend FROM usage WHERE backend = ?",
                (backend,),
            ).fetchone()
        state.calls = int(usage["calls"])
        state.failures = int(usage["failures"])
        state.spend_usd = round(float(usage["spend"]), 6)
        return state

    def all_backend_states(self) -> list[BackendState]:
        with self._lock:
            rows = self._execute("SELECT backend FROM backend_state ORDER BY backend").fetchall()
        return [self.backend_state(row["backend"]) for row in rows]

    def _ensure_state(self, backend: str, *, now: float) -> None:
        self._execute(
            "INSERT INTO backend_state(backend, updated_at) VALUES (?, ?) "
            "ON CONFLICT(backend) DO NOTHING",
            (backend, now),
        )

    def set_plan(self, backend: str, plan: str, *, now: float | None = None) -> None:
        if plan not in ("free", "paid"):
            raise ValueError(f"plan must be 'free' or 'paid', got {plan!r}")
        stamp = time.time() if now is None else float(now)
        with self._lock:
            self._ensure_state(backend, now=stamp)
            self._execute(
                "UPDATE backend_state SET plan = ?, updated_at = ? WHERE backend = ?",
                (plan, stamp, backend),
            )

    def get_plan(self, backend: str, default: str = "paid") -> str:
        with self._lock:
            row = self._execute(
                "SELECT plan FROM backend_state WHERE backend = ?", (backend,)
            ).fetchone()
        return (row["plan"] if row else default) or default

    def is_available(self, backend: str, *, now: float | None = None) -> bool:
        """Both breakers closed: circuit not open and the quota window not exhausted."""
        stamp = time.time() if now is None else float(now)
        state = self.backend_state(backend)
        return state.circuit_open_until <= stamp and state.quota_exhausted_until <= stamp

    def circuit_reason(self, backend: str, *, now: float | None = None) -> str:
        """Human-readable reason a backend is skipped, or ``""`` when it is available."""
        stamp = time.time() if now is None else float(now)
        state = self.backend_state(backend)
        if state.quota_exhausted_until > stamp:
            return f"quota exhausted for another {int(state.quota_exhausted_until - stamp)}s"
        if state.circuit_open_until > stamp:
            return f"circuit open for another {int(state.circuit_open_until - stamp)}s"
        return ""

    def record_success(
        self,
        backend: str,
        *,
        latency_ms: float | None = None,
        kind: str = "search",
        cost_usd: float = 0.0,
        now: float | None = None,
    ) -> BackendState:
        """A success resets the failure count and both breakers (PLAN.md:21)."""
        stamp = time.time() if now is None else float(now)
        with self._lock:
            self._ensure_state(backend, now=stamp)
            row = self._execute(
                "SELECT latency_ema_ms, latency_samples FROM backend_state WHERE backend = ?",
                (backend,),
            ).fetchone()
            samples: list[float] = []
            try:
                samples = [
                    float(x)
                    for x in json.loads((row["latency_samples"] if row else "") or "[]")
                ]
            except (TypeError, ValueError):
                samples = []
            ema = row["latency_ema_ms"] if row else None
            if latency_ms is not None:
                value = max(0.0, float(latency_ms))
                samples.append(value)
                samples = samples[-LATENCY_SAMPLE_WINDOW:]
                ema = (
                    value
                    if ema is None
                    else (LATENCY_EMA_ALPHA * value + (1 - LATENCY_EMA_ALPHA) * float(ema))
                )
            self._execute(
                "UPDATE backend_state SET consecutive_failures = 0, circuit_open_until = 0, "
                "quota_exhausted_until = 0, latency_ema_ms = ?, latency_samples = ?, "
                "last_ok_at = ?, updated_at = ? WHERE backend = ?",
                (ema, json.dumps(samples), stamp, stamp, backend),
            )
            self._record_usage(backend, kind, True, latency_ms, cost_usd, stamp)
        return self.backend_state(backend)

    def record_failure(
        self,
        backend: str,
        *,
        error: str,
        quota: bool = False,
        latency_ms: float | None = None,
        kind: str = "search",
        now: float | None = None,
    ) -> BackendState:
        """Record a failure. A quota error opens the circuit for an hour on its own."""
        stamp = time.time() if now is None else float(now)
        with self._lock:
            self._ensure_state(backend, now=stamp)
            row = self._execute(
                "SELECT consecutive_failures FROM backend_state WHERE backend = ?", (backend,)
            ).fetchone()
            failures = int((row["consecutive_failures"] if row else 0) or 0) + 1
            circuit_until = 0.0
            quota_until = 0.0
            if quota:
                quota_until = stamp + self.policy.circuit_quota_seconds
                circuit_until = quota_until
            elif failures >= self.policy.circuit_failures:
                circuit_until = stamp + self.policy.circuit_open_seconds
            self._execute(
                "UPDATE backend_state SET consecutive_failures = ?, circuit_open_until = ?, "
                "quota_exhausted_until = ?, last_error = ?, last_error_at = ?, updated_at = ? "
                "WHERE backend = ?",
                (failures, circuit_until, quota_until, error[:2000], stamp, stamp, backend),
            )
            self._record_usage(backend, kind, False, latency_ms, 0.0, stamp)
        return self.backend_state(backend)

    def note_quota(
        self, backend: str, *, seconds: int | None = None, now: float | None = None
    ) -> None:
        """Open just the quota window (the pane can do this by hand after a plan change)."""
        stamp = time.time() if now is None else float(now)
        until = stamp + (seconds if seconds is not None else self.policy.circuit_quota_seconds)
        with self._lock:
            self._ensure_state(backend, now=stamp)
            self._execute(
                "UPDATE backend_state SET quota_exhausted_until = ?, updated_at = ? WHERE backend = ?",
                (until, stamp, backend),
            )

    def _record_usage(
        self,
        backend: str,
        kind: str,
        ok: bool,
        latency_ms: float | None,
        cost_usd: float,
        stamp: float,
    ) -> None:
        self._execute(
            "INSERT INTO usage(backend, kind, ok, latency_ms, cost_usd, at) VALUES (?, ?, ?, ?, ?, ?)",
            (backend, kind, 1 if ok else 0, latency_ms, float(cost_usd), stamp),
        )

    # ── usage ───────────────────────────────────────────────────────────────────────

    def usage_summary(self, *, since: float | None = None) -> dict[str, dict[str, Any]]:
        sql = (
            "SELECT backend, COUNT(*) AS calls, "
            "COALESCE(SUM(ok), 0) AS ok, "
            "COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS failures, "
            "COALESCE(SUM(cost_usd), 0) AS spend, "
            "AVG(latency_ms) AS mean_latency "
            "FROM usage"
        )
        params: tuple[Any, ...] = ()
        if since is not None:
            sql += " WHERE at >= ?"
            params = (float(since),)
        sql += " GROUP BY backend ORDER BY backend"
        with self._lock:
            rows = self._execute(sql, params).fetchall()
        return {
            row["backend"]: {
                "calls": int(row["calls"]),
                "ok": int(row["ok"]),
                "failures": int(row["failures"]),
                "spend_usd": round(float(row["spend"]), 6),
                "mean_latency_ms": (
                    None if row["mean_latency"] is None else round(float(row["mean_latency"]), 1)
                ),
            }
            for row in rows
        }

    def total_spend(self) -> float:
        with self._lock:
            row = self._execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM usage").fetchone()
        return round(float(row["s"]), 6)

    # ── domain memory ───────────────────────────────────────────────────────────────

    def domain_memory_get(
        self, domain: str, *, now: float | None = None
    ) -> dict[str, Any] | None:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            row = self._execute(
                "SELECT * FROM domain_memory WHERE domain = ?", (domain,)
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= stamp:
                self._execute("DELETE FROM domain_memory WHERE domain = ?", (domain,))
                return None
        return {
            "domain": row["domain"],
            "block_class": row["block_class"],
            "stage_that_worked": row["stage_that_worked"],
            "signals": [s for s in (row["signals"] or "").split(",") if s],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
        }

    def domain_memory_put(
        self,
        domain: str,
        block_class: str,
        stage_that_worked: str | None,
        *,
        signals: Iterable[str] = (),
        now: float | None = None,
    ) -> None:
        stamp = time.time() if now is None else float(now)
        expires = stamp + self.policy.domain_memory_ttl
        joined = ",".join(str(s) for s in signals if s)
        with self._lock:
            self._execute(
                "INSERT INTO domain_memory(domain, block_class, stage_that_worked, signals, "
                "created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET block_class = excluded.block_class, "
                "stage_that_worked = excluded.stage_that_worked, signals = excluded.signals, "
                "updated_at = excluded.updated_at, expires_at = excluded.expires_at",
                (domain, block_class, stage_that_worked, joined, stamp, stamp, expires),
            )

    def domain_memory_all(self, *, now: float | None = None) -> list[dict[str, Any]]:
        stamp = time.time() if now is None else float(now)
        with self._lock:
            rows = self._execute(
                "SELECT domain FROM domain_memory WHERE expires_at > ? ORDER BY updated_at DESC",
                (stamp,),
            ).fetchall()
        return [entry for row in rows if (entry := self.domain_memory_get(row["domain"], now=stamp))]

    # ── jobs ────────────────────────────────────────────────────────────────────────

    def create_job(
        self,
        kind: str,
        target: str,
        *,
        command: str | None = None,
        status: str = "queued",
        now: float | None = None,
    ) -> str:
        if status not in _JOB_STATUSES:
            raise ValueError(f"unknown job status {status!r}")
        stamp = time.time() if now is None else float(now)
        job_id = uuid.uuid4().hex[:16]
        with self._lock:
            self._execute(
                "INSERT INTO jobs(id, kind, target, status, command, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, kind, target, status, command, stamp),
            )
        return job_id

    def update_job(self, job_id: str, *, now: float | None = None, **fields: Any) -> None:
        unknown = set(fields) - _JOB_TEXT_FIELDS
        if unknown:
            raise ValueError(f"unknown job field(s): {', '.join(sorted(unknown))}")
        stamp = time.time() if now is None else float(now)
        if "status" in fields and fields["status"] not in _JOB_STATUSES:
            raise ValueError(f"unknown job status {fields['status']!r}")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params: list[Any] = list(fields.values())
        extra = ""
        if fields.get("status") == "running":
            extra = ", started_at = ?"
            params.append(stamp)
        if fields.get("status") in ("succeeded", "failed"):
            extra = ", finished_at = ?"
            params.append(stamp)
        params.append(job_id)
        with self._lock:
            self._execute(f"UPDATE jobs SET {assignments}{extra} WHERE id = ?", params)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_stale_jobs(self, *, now: float | None = None) -> int:
        """Mark jobs left ``queued``/``running`` by a dead process as failed on next open."""
        stamp = time.time() if now is None else float(now)
        with self._lock:
            cur = self._execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, "
                "error = COALESCE(error, 'interrupted: the process exited while this job ran') "
                "WHERE status IN ('queued', 'running')",
                (stamp,),
            )
            return int(cur.rowcount or 0)


def _p50(samples: list[float]) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 1)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 1)


def default_data_dir() -> Path:
    """Where the store lives when no Hermes context supplies a data directory.

    Hermes' own convention is ``<hermes home>/plugin-data/<name>/``
    (``plugins.plugin_storage.plugin_data_dir``); the ``~/.hermes/plugins/sluice/data``
    path is the standalone fallback from the build plan.
    """
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir("sluice"))
    except Exception:  # noqa: BLE001 - standalone install without Hermes
        override = os.environ.get("SLUICE_DATA_DIR")
        if override:
            return Path(override).expanduser()
        return Path.home() / ".hermes" / "plugins" / "sluice" / "data"


def open_store(
    data_dir: str | os.PathLike[str] | None = None,
    *,
    settings: Mapping[str, Any] | None = None,
    path: str | os.PathLike[str] | None = None,
    max_bytes: int | None = None,
    low_water_bytes: int | None = None,
) -> Store:
    """Open (and migrate) the SQLite store. ``path`` wins over ``data_dir`` for tests."""
    policy = StorePolicy.from_settings(settings)
    db_path = Path(path) if path is not None else Path(data_dir or default_data_dir()) / "sluice.db"
    return Store(db_path, policy, max_bytes=max_bytes, low_water_bytes=low_water_bytes)
