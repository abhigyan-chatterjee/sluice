"""``hermes sluice`` and the standalone ``sluice`` console script.

Command surface fixed by PLAN.md:119-125: ``doctor``, ``status``, ``setup [--apply]``,
``use search|extract [--apply]``, ``test --backend <id> "<query>"``, ``cache clear`` and
``install playwright|camofox|browserbase``. Registered through
``ctx.register_cli_command`` (root ``__init__.py``), which hands this module the parser it
built for the command: ``setup_parser(parser)`` adds our subcommands, ``handle_command``
becomes ``args.func``.

Every command is unattended-safe: it never prompts, returns 0 only on success and prints
the exact command that comes next. ``setup`` and ``use`` print by default and touch
Hermes' config only with ``--apply`` (PLAN.md:114). Installs always run as a persisted job,
which is what makes them visible to the dashboard API as well (PLAN.md:112-113).
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Mapping, Sequence

from . import __version__
from .backends import BACKEND_MODULES, make_backend, specs
from .config import backend_plan, load_settings, plugin_data_dir
from .manage import (
    INSTALL_TARGETS,
    clear_outer_web_cache,
    detect,
    missing_steps,
    render_detection,
    run_config_set,
    setup_routes,
    start_install_job,
)
from .store import open_store

PROG = "hermes sluice"

#: ``use`` targets and the Hermes key each one switches (DESIGN.md:65-67).
USE_ROUTES: dict[str, str] = {
    "search": "web.search_backend",
    "extract": "web.extract_backend",
}


# ── shared helpers ─────────────────────────────────────────────────────────────


def _settings() -> dict[str, Any]:
    return load_settings()


def _store(settings: Mapping[str, Any]):
    return open_store(plugin_data_dir(), settings=settings)


def _key_state(backend: Any) -> str:
    env = backend.spec.secret_env
    if backend.spec.cheapest_tier == "local":
        return "n/a"
    if backend.key:
        return "set"
    if backend.keyless_capable:
        return "keyless" if backend.keyless_enabled else "off"
    return "missing"


def _next_command(backend: Any, settings: Mapping[str, Any]) -> str:
    """The exact next command for one unroutable backend."""
    if backend.spec.id == "searxng":
        return (
            "set SEARXNG_URL (for example http://localhost:8080) then: "
            "docker compose -f docker/searxng/docker-compose.yml up -d"
        )
    env = backend.spec.secret_env or ""
    if env and not settings.get(env):
        return f"set {env} in Settings ▸ Plugins, or enable keyless_enabled"
    if backend.keyless_capable and not backend.keyless_enabled:
        return "enable keyless_enabled in Settings ▸ Plugins"
    return "hermes sluice doctor"


def _print_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows, start=1):
        title = str(row.get("title") or "").strip() or "(untitled)"
        url = str(row.get("url") or "")
        description = " ".join(str(row.get("description") or "").split())
        if len(description) > 200:
            description = description[:197] + "..."
        position = row.get("position") or index
        print(f"{position:>2}. {title}")
        print(f"    {url}")
        if description:
            print(f"    {description}")


# ── doctor ─────────────────────────────────────────────────────────────────────


def _cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings()
    registry = specs()
    print(f"Sluice {__version__} — 12 backends, free first")
    print(f"tier order: {settings['tier_order']}   keyless: {'on' if settings['keyless_enabled'] else 'off'}")
    print()
    header = f"{'backend':<12} {'tier':<8} {'plan':<5} {'key':<8} status"
    print(header)
    print("-" * len(header))
    gaps: list[tuple[str, str, str]] = []
    for vendor in BACKEND_MODULES:
        spec = registry[vendor]
        backend = make_backend(vendor, settings)
        plan = backend_plan(settings, vendor)
        tier = backend.tier(plan) or spec.cheapest_tier
        missing = backend.missing_message()
        status = "ready" if not missing else "missing: " + missing.split(".")[0]
        print(f"{vendor:<12} {tier:<8} {plan:<5} {_key_state(backend):<8} {status}")
        if missing:
            gaps.append((vendor, missing, _next_command(backend, settings)))
    print()
    print("browser engines")
    detection = detect(settings=settings)
    print(render_detection(detection))
    engine_steps = missing_steps(detection, settings=settings)
    if engine_steps:
        print()
        print("optional browser gaps (the ladder falls back without them)")
        for step in engine_steps:
            print(f"  {step['name']:<20} {step['detail']}")
            print(f"  {'':<20} next: {step['next']}")
    if gaps:
        print()
        print("what is missing")
        for vendor, message, next_step in gaps:
            print(f"  {vendor}: {message}")
            print(f"    next: {next_step}")
    print()
    print("next: hermes sluice setup --apply   (point Hermes at Sluice)")
    return 0


# ── status ─────────────────────────────────────────────────────────────────────


def _cmd_status(args: argparse.Namespace) -> int:
    settings = _settings()
    store = _store(settings)
    try:
        now = time.time()
        print(f"Sluice {__version__} — persisted state")
        print()
        header = f"{'backend':<12} {'health':<9} {'p50':>7} {'ema':>7} {'calls':>6} {'spend':>9}  last error"
        print(header)
        print("-" * len(header))
        for vendor in BACKEND_MODULES:
            state = store.backend_state(vendor)
            if state.quota_exhausted_until > now:
                health = "quota"
            elif state.circuit_open_until > now:
                health = "circuit"
            elif state.last_ok_at and (not state.last_error_at or state.last_ok_at >= state.last_error_at):
                health = "ok"
            elif state.last_error:
                health = "error"
            else:
                health = "idle"
            p50 = "-" if state.latency_p50_ms is None else f"{state.latency_p50_ms:.0f}"
            ema = "-" if state.latency_ema_ms is None else f"{state.latency_ema_ms:.0f}"
            error = " ".join(str(state.last_error or "").split())[:60]
            print(
                f"{vendor:<12} {health:<9} {p50:>7} {ema:>7} {state.calls:>6} "
                f"{state.spend_usd:>9.4f}  {error}"
            )
        print()
        stats = store.cache_stats()
        print(
            f"cache: {stats['live_rows']} live / {stats['rows']} rows, "
            f"{stats['size_bytes']} bytes on disk (cap {stats['max_bytes']})"
        )
        print(f"spend: ${store.total_spend():.4f} across every backend (known prices only)")
        blocked = [entry for entry in store.domain_memory_all() if entry.get("block_class") not in (None, "ok")]
        if blocked:
            print("domain memory:")
            for entry in blocked[:10]:
                stage = entry.get("stage_that_worked") or "none"
                print(f"  {entry['domain']:<32} {entry['block_class']:<16} worked at: {stage}")
    finally:
        store.close()
    print()
    print("next: hermes sluice test --backend ddgs \"sluice smoke test\"")
    return 0


# ── setup / use ────────────────────────────────────────────────────────────────


def _render_route(result: Mapping[str, Any]) -> None:
    command = " ".join(result["command"])
    if not result["applied"]:
        print(command)
        return
    state = "ok" if result["ok"] else "FAILED"
    print(f"{command}   [{state}]")
    if result.get("output"):
        for line in str(result["output"]).splitlines():
            print(f"    {line}")


def _cmd_setup(args: argparse.Namespace) -> int:
    results = setup_routes(apply=args.apply)
    print("Sluice config route (Sluice never writes config.yaml itself):")
    for result in results:
        _render_route(result)
    print()
    if not args.apply:
        print("dry run: re-run with --apply to write these three keys")
    elif all(result["ok"] for result in results):
        print("done: Hermes now routes web_search and web_extract through Sluice, outer cache off")
        print("next: hermes sluice test --backend ddgs \"sluice smoke test\"")
    else:
        print("one or more config writes failed; fix the error above and re-run", file=sys.stderr)
        return 1
    return 0


def _cmd_use(args: argparse.Namespace) -> int:
    key = USE_ROUTES[args.target]
    result = run_config_set(key, apply=args.apply)
    if not result["applied"]:
        print(" ".join(result["command"]))
        print()
        print("dry run: re-run with --apply to switch it")
        return 0
    _render_route(result)
    if not result["ok"]:
        return 1
    print(f"next: hermes sluice test --backend ddgs \"sluice smoke test\"")
    return 0


# ── test ───────────────────────────────────────────────────────────────────────


def _cmd_test(args: argparse.Namespace) -> int:
    settings = _settings()
    vendor = args.backend
    try:
        backend = make_backend(vendor, settings)
    except KeyError as exc:
        print(f"sluice: {exc.args[0]}", file=sys.stderr)
        return 2
    missing = backend.missing_message()
    if missing:
        print(f"sluice: {vendor} cannot run: {missing}", file=sys.stderr)
        print(f"next: {_next_command(backend, settings)}", file=sys.stderr)
        return 1
    store = _store(settings)
    started = time.monotonic()
    try:
        result = backend.search(args.query, limit=args.limit)
    except NotImplementedError as exc:
        print(f"sluice: {exc}", file=sys.stderr)
        return 1
    finally:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    mode = backend.mode()
    tier = backend.tier(backend_plan(settings, vendor)) or mode
    ok = bool(result.get("ok"))
    try:
        if ok:
            store.record_success(vendor, latency_ms=elapsed_ms, kind="search")
        else:
            store.record_failure(
                vendor,
                error=str(result.get("error") or "search failed"),
                quota=bool(result.get("quota")),
                latency_ms=elapsed_ms,
                kind="search",
            )
    finally:
        store.close()
    if not ok:
        print(f"sluice: {vendor} failed in {elapsed_ms:.0f} ms: {result.get('error')}", file=sys.stderr)
        print(f"next: hermes sluice status", file=sys.stderr)
        return 1
    rows = result.get("results") or []
    print(f"{vendor} ({tier}, {mode}) answered in {elapsed_ms:.0f} ms with {len(rows)} result(s)")
    print()
    _print_rows(rows)
    return 0


# ── cache ──────────────────────────────────────────────────────────────────────


def _cmd_cache_clear(args: argparse.Namespace) -> int:
    settings = _settings()
    store = _store(settings)
    try:
        removed = store.cache_clear()
    finally:
        store.close()
    outer = clear_outer_web_cache()
    print(f"sluice cache: deleted {removed} row(s)")
    if outer["existed"]:
        print(f"hermes cache/web: deleted {outer['removed']} file(s), {outer['bytes']} bytes ({outer['path']})")
    else:
        print(f"hermes cache/web: nothing at {outer['path']}")
    print("next: hermes sluice status")
    return 0


# ── install ────────────────────────────────────────────────────────────────────


def _cmd_install(args: argparse.Namespace) -> int:
    settings = _settings()
    store = _store(settings)
    try:
        result = start_install_job(store, args.target, wait=True)
    finally:
        store.close()
    if not result["started"]:
        print(f"{args.target}: {result['note']}")
        print(f"next: {result['next']}")
        return 0
    job = result["job"] or {}
    print(f"job {job.get('id')} [{job.get('status')}] {job.get('command')}")
    output = str(job.get("output") or "").strip()
    if output:
        for line in output.splitlines():
            print(f"  {line}")
    if job.get("status") != "succeeded":
        print(f"error: {job.get('error') or 'install failed'}", file=sys.stderr)
        print("next: hermes sluice doctor", file=sys.stderr)
        return 1
    print(f"done. next: {result['next']}")
    return 0


# ── parser and dispatch ────────────────────────────────────────────────────────

_HANDLERS = {
    "doctor": _cmd_doctor,
    "status": _cmd_status,
    "setup": _cmd_setup,
    "use": _cmd_use,
    "test": _cmd_test,
    "cache": _cmd_cache_clear,
    "install": _cmd_install,
}


def _add_use_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run the printed `hermes config set` (default: print only)",
    )


def _add_subcommands(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="sluice_command", metavar="<command>")

    subs.add_parser("doctor", help="Show the twelve-backend inventory: free/paid and what is missing")

    subs.add_parser("status", help="Circuit, quota and cache state per backend")

    setup = subs.add_parser("setup", help="Turn off the outer web cache and point Hermes at Sluice")
    _add_use_apply(setup)

    use = subs.add_parser("use", help="Switch the Hermes web backend to Sluice")
    use.add_argument("target", choices=("search", "extract"), help="Which tool to switch")
    _add_use_apply(use)

    test = subs.add_parser("test", help="Send one query to one backend and report latency")
    test.add_argument("--backend", required=True, metavar="<id>", help="Backend id, e.g. exa or ddgs")
    test.add_argument("--limit", type=int, default=5, help="Results to ask for (default: 5)")
    test.add_argument("query", help="The query text")

    cache = subs.add_parser("cache", help="Inspect or clear the Sluice cache")
    cache_subs = cache.add_subparsers(dest="cache_command", metavar="<action>")
    cache_subs.add_parser("clear", help="Delete every cached search and extract row")

    install = subs.add_parser("install", help="Install a browser engine as a job")
    install.add_argument(
        "target",
        choices=INSTALL_TARGETS,
        help="What to install (always an explicit action, never automatic)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Parser for the standalone ``sluice`` entry point (``[project.scripts]``)."""
    parser = argparse.ArgumentParser(
        prog="sluice",
        description="Tiered free-first web search and extraction router for Hermes.",
    )
    parser.add_argument("--version", action="version", version=f"sluice {__version__}")
    _add_subcommands(parser)
    return parser


def setup_parser(parser: argparse.ArgumentParser) -> None:
    """Attach the Sluice command tree to the parser Hermes built for ``hermes sluice``."""
    parser.set_defaults(func=handle_command)
    _add_subcommands(parser)


def handle_command(args: argparse.Namespace) -> int:
    """Dispatch one ``hermes sluice`` invocation. Non-zero means the command failed."""
    command = getattr(args, "sluice_command", None)
    if command is None:
        build_parser().print_help(sys.stderr)
        return 2
    if command == "cache" and getattr(args, "cache_command", None) != "clear":
        print("sluice: cache needs an action; the only action is 'clear'", file=sys.stderr)
        return 2
    handler = _HANDLERS.get(command)
    if handler is None:  # pragma: no cover - argparse rejects unknown commands first
        print(f"sluice: unknown command {command!r}", file=sys.stderr)
        return 2
    return handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "sluice_command", None) is None:
        parser.print_help(sys.stderr)
        return 2
    return handle_command(args)


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
