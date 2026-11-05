"""One module per vendor backend, one registry entry per vendor.

DESIGN.md:136-141 fixes the shape: **one entry per vendor, not one per access mode**. A
vendor declares the access modes it offers and the router uses the cheapest enabled one it
has — ``keyless``, then ``free`` (a free-plan key), then ``paid``. That matches reality:
Exa, Tavily, Firecrawl and Keenable use the same key on a free allowance and a paid plan,
and their APIs do not say which.

Adapters are self-contained HTTP modules. They never import Hermes' built-in provider
modules: those are undocumented internals and a catalog reviewer flags the import
(DESIGN.md:132-134). Payload shapes live in ``references/endpoints.md``.

T1 ships the metadata and the base class. T4, T5 and T6 fill in ``probe``/``search``/
``extract``; each module names its own task in ``SPEC.task``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

#: Module names under this package, in DESIGN.md:143-150 tier-table order.
BACKEND_MODULES: tuple[str, ...] = (
    "searxng",
    "ddgs",
    "exa",
    "parallel",
    "firecrawl",
    "tavily",
    "keenable",
    "brave",
    "perplexity",
    "deepseek",
    "tinyfish",
    "xai",
)


@dataclass(frozen=True)
class BackendSpec:
    """Static identity of one vendor. No I/O, no credentials."""

    id: str
    label: str
    #: Access modes this vendor offers, cheapest first: local | keyless | free | paid.
    tiers: tuple[str, ...]
    search: bool
    extract: bool
    #: Env var holding the key; ``None`` for backends that take no key.
    secret_env: str | None = None
    summary: str = ""
    #: The PLAN.md task that implements the adapter (T4, T5 or T6).
    task: str = ""
    #: Non-secret settings this backend reads (e.g. a base URL override).
    settings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cheapest_tier(self) -> str:
        return self.tiers[0]

    @property
    def is_free_tier(self) -> bool:
        """True when the backend can serve without a paid plan (local/keyless/free)."""
        return any(tier in ("local", "keyless", "free") for tier in self.tiers)


class Backend:
    """Base adapter. Subclasses set ``spec`` and implement the capabilities they advertise."""

    spec: ClassVar[BackendSpec]

    def probe(self) -> dict[str, Any]:
        """Cheap health check for the pane and ``hermes sluice status``."""
        raise NotImplementedError(self._not_yet("probe"))

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        raise NotImplementedError(self._not_yet("search"))

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(self._not_yet("extract"))

    def _not_yet(self, op: str) -> str:
        task = self.spec.task or "its adapter task"
        return (
            f"{self.spec.id}.{op} is not implemented yet — {task} implements it against "
            f"references/endpoints.md#{self.spec.id}"
        )


def specs() -> dict[str, BackendSpec]:
    """Every vendor spec, keyed by id. Imports the modules on demand; no network."""
    out: dict[str, BackendSpec] = {}
    for module_name in BACKEND_MODULES:
        module = importlib.import_module(f"{__name__}.{module_name}")
        out[module.SPEC.id] = module.SPEC
    return out


def spec(backend_id: str) -> BackendSpec:
    """One vendor spec by id, with a message that lists the valid ids."""
    try:
        return specs()[backend_id]
    except KeyError:
        raise KeyError(
            f"unknown backend {backend_id!r}; expected one of {', '.join(BACKEND_MODULES)}"
        ) from None


def tiers() -> Mapping[str, tuple[str, ...]]:
    """Vendor ids grouped by cheapest access mode, for ``hermes sluice doctor``."""
    grouped: dict[str, list[str]] = {}
    for vendor in specs().values():
        grouped.setdefault(vendor.cheapest_tier, []).append(vendor.id)
    return {tier: tuple(ids) for tier, ids in grouped.items()}
