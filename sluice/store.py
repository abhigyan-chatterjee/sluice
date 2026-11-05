"""SQLite state and caches in the plugin data directory.

Contract: PLAN.md:56-62. Five tables (``backend_state``, ``usage``, ``cache``,
``domain_memory``, ``jobs``), thread-safe connections, WAL, no ORM, and the cache/circuit
numbers from PLAN.md:18-23 implemented exactly. Implemented in T2; this module exists at
T1 so the numbers have one home and cannot drift between the manifest defaults, the
router and the store.

Eviction wording from PLAN.md:21-23 is ambiguous and is resolved in REQUIREMENTS.md
("Cache and circuit numbers"): the trigger is ``cache_max_mb`` and the post-eviction
target is a separate, smaller number. T2 must implement the resolved reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Table names fixed by PLAN.md:59.
TABLES: tuple[str, ...] = ("backend_state", "usage", "cache", "domain_memory", "jobs")


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
    #: PLAN.md:22 asks for one VACUUM per week at most.
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


def open_store(*args: Any, **kwargs: Any) -> Any:
    """Open (and migrate) the SQLite store. Implemented in T2."""
    raise NotImplementedError(
        "sluice.store.open_store lands in T2 (PLAN.md:56-62): SQLite in plugin_data_dir() "
        "with backend_state, usage, cache, domain_memory and jobs tables."
    )
