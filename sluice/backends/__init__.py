"""One module per vendor backend, one registry entry per vendor.

DESIGN.md:136-141 fixes the shape: **one entry per vendor, not one per access mode**. A
vendor declares the access modes it offers and the router uses the cheapest enabled one it
has — ``keyless``, then ``free`` (a free-plan key), then ``paid``. That matches reality:
Exa, Tavily, Firecrawl and Keenable use the same key on a free allowance and a paid plan,
and their APIs do not say which.

Mode resolution, in one sentence: **a vendor with a key runs keyed; a vendor without one
runs its keyless path when keyless tiers are enabled; otherwise it is skipped.** The tier
label the router stamps on a result follows the mode actually used, so "free" is never
claimed for a paid key and a keyless call never hides behind a paid label. The one thing
that does not happen is a keyless call silently outranking a paid key (DESIGN.md:55):
having a key is what selects the keyed path.

Adapters are self-contained HTTP modules. They never import Hermes' built-in provider
modules: those are undocumented internals and a catalog reviewer flags the import
(DESIGN.md:132-134). Payload shapes live in ``references/endpoints.md``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Sequence

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

#: Vendors whose API accepts an anonymous call. The tier label is ``keyless``.
KEYLESS_VENDORS: frozenset[str] = frozenset(
    {"searxng", "ddgs", "exa", "parallel", "firecrawl", "tavily", "keenable"}
)

#: Published per-call price where the vendor documents one. Everything else records 0.0
#: and the pane labels the column "spend (known prices)". DeepSeek: $0.75 / 1,000
#: successful searches (references/endpoints.md:174).
KNOWN_PRICE_PER_CALL_USD: dict[str, float] = {"deepseek": 0.00075}


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
    #: The PLAN.md task that implemented the adapter (T4, T5 or T6).
    task: str = ""
    #: Per-backend wall clock this adapter needs to answer, in ms. ``0`` means "no opinion":
    #: the router's ``per_backend_budget_ms`` setting applies. A non-zero value only ever
    #: *raises* that window for this one backend, and never past the router's total budget or
    #: the window it reserves for the candidates behind this one, so a backend whose transport
    #: is inherently slower than the global default — a local multi-engine scraper, not an API
    #: call — is not killed before it can answer.
    budget_ms: int = 0
    #: Non-secret settings this backend reads (e.g. a base URL override).
    settings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cheapest_tier(self) -> str:
        return self.tiers[0]

    @property
    def is_free_tier(self) -> bool:
        """True when the backend can serve without a paid plan (local/keyless/free)."""
        return any(tier in ("local", "keyless", "free") for tier in self.tiers)

    @property
    def keyless_capable(self) -> bool:
        return self.id in KEYLESS_VENDORS and "keyless" in self.tiers


class Backend:
    """Base adapter. Subclasses set ``spec`` and implement the capabilities they advertise."""

    spec: ClassVar[BackendSpec]
    #: An adapter that cannot serve a capability raises ``NotImplementedError`` from it.
    capabilities: ClassVar[tuple[str, ...]] = ("search",)

    def __init__(self, settings: Mapping[str, Any] | None = None) -> None:
        self.settings: dict[str, Any] = dict(settings or {})

    # ── configuration, no network ───────────────────────────────────────────────

    @property
    def key(self) -> str:
        env = self.spec.secret_env
        return str(self.settings.get(env) or "").strip() if env else ""

    @property
    def keyless_enabled(self) -> bool:
        return bool(self.settings.get("keyless_enabled", True))

    @property
    def keyless_capable(self) -> bool:
        return self.spec.keyless_capable

    def missing_message(self) -> str:
        """Actionable configuration error, or ``""`` when the backend can run."""
        from ._http import missing_key_message

        if self.spec.id == "searxng":
            error = str(self.settings.get("searxng_url_error") or "").strip()
            if error:
                return error
            if not str(self.settings.get("searxng_url") or "").strip():
                return (
                    "SEARXNG_URL is not set. Point it at a running instance, for example "
                    "http://localhost:8080 (docker/searxng/ in this repository ships one with "
                    "search.formats: [html, json])."
                )
        if self.spec.cheapest_tier == "local":
            return ""
        if self.key:
            return ""
        if self.keyless_capable and self.keyless_enabled:
            return ""
        if self.spec.secret_env:
            return missing_key_message(self.spec.secret_env, self.spec.id)
        return (
            f"{self.spec.label} is a keyless-only backend and the keyless tier is disabled. "
            "Enable keyless_enabled in Settings ▸ Plugins, or configure a keyed backend."
        )

    def mode(self) -> str:
        """``local`` | ``keyless`` | ``keyed`` | ``unavailable`` — never a network call."""
        if self.missing_message():
            return "unavailable"
        if self.spec.cheapest_tier == "local":
            return "local"
        return "keyed" if self.key else "keyless"

    def is_configured(self) -> bool:
        return self.mode() != "unavailable"

    def tier(self, plan: str = "paid") -> str:
        """The tier label for the mode in use, used by the router's ordering and output."""
        mode = self.mode()
        if mode in ("local", "keyless"):
            return mode
        if mode == "keyed":
            return "free" if plan == "free" else "paid"
        return ""

    def cost_per_call(self) -> float:
        return KNOWN_PRICE_PER_CALL_USD.get(self.spec.id, 0.0)

    # ── capabilities ────────────────────────────────────────────────────────────

    def probe(self) -> dict[str, Any]:
        """Cheap health check: one tiny real call, or the configuration error."""
        missing = self.missing_message()
        if missing:
            return {"ok": False, "skipped": True, "error": missing, "mode": "unavailable"}
        if "search" not in self.capabilities:
            return {
                "ok": False,
                "skipped": True,
                "error": f"{self.spec.label} has no search capability",
                "mode": self.mode(),
            }
        try:
            result = self.search("sluice probe", limit=1)
        except NotImplementedError as exc:  # pragma: no cover - contract guard
            return {"ok": False, "error": str(exc), "mode": self.mode()}
        if result.get("ok"):
            return {"ok": True, "error": None, "mode": self.mode(), "results": len(result.get("results") or [])}
        return {
            "ok": False,
            "error": result.get("error") or "probe failed",
            "mode": self.mode(),
            "quota": bool(result.get("quota")),
        }

    def search(self, query: str, limit: int = 5, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(f"{self.spec.id} is search-only or has no search adapter")

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(f"{self.spec.id} has no extract adapter")


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


def backend_class(backend_id: str) -> type[Backend]:
    """The adapter class for one vendor id."""
    if backend_id not in BACKEND_MODULES:
        raise KeyError(f"unknown backend {backend_id!r}; expected one of {', '.join(BACKEND_MODULES)}")
    module = importlib.import_module(f"{__name__}.{backend_id}")
    return getattr(module, f"{_class_stem(backend_id)}Backend")


def _class_stem(backend_id: str) -> str:
    return {
        "searxng": "Searxng",
        "ddgs": "Ddgs",
        "exa": "Exa",
        "parallel": "Parallel",
        "firecrawl": "Firecrawl",
        "tavily": "Tavily",
        "keenable": "Keenable",
        "brave": "Brave",
        "perplexity": "Perplexity",
        "deepseek": "Deepseek",
        "tinyfish": "Tinyfish",
        "xai": "Xai",
    }[backend_id]


def make_backend(backend_id: str, settings: Mapping[str, Any] | None = None) -> Backend:
    """Instantiate one adapter with settings. No network, no side effects."""
    return backend_class(backend_id)(settings)


def all_backends(settings: Mapping[str, Any] | None = None) -> dict[str, Backend]:
    return {backend_id: make_backend(backend_id, settings) for backend_id in BACKEND_MODULES}


def tiers() -> Mapping[str, tuple[str, ...]]:
    """Vendor ids grouped by cheapest access mode, for ``hermes sluice-search doctor``."""
    grouped: dict[str, list[str]] = {}
    for vendor in specs().values():
        grouped.setdefault(vendor.cheapest_tier, []).append(vendor.id)
    return {tier: tuple(ids) for tier, ids in grouped.items()}
