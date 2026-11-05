"""``hermes sluice`` and the standalone ``sluice`` console script.

Command surface fixed by PLAN.md:119-125: ``doctor``, ``status``, ``setup [--apply]``,
``use search|extract``, ``test --backend <id> "<query>"``, ``cache clear`` and
``install playwright|camofox|browserbase``. Registered through
``ctx.register_cli_command`` (root ``__init__.py``), which hands this module the parser it
built for the command: ``setup_parser(parser)`` adds our subcommands, ``handle_command``
becomes ``args.func``.

T1 ships the parser and the dispatch only. Each handler returns a non-zero exit code and
names the task that implements it, so an unattended run fails loudly instead of printing a
success line for work that has not happened.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__

PROG = "hermes sluice"

#: Subcommands in PLAN.md:122-123 order, with the task that implements each one.
COMMAND_OWNERS: dict[str, str] = {
    "doctor": "T9",
    "status": "T9",
    "setup": "T8/T9",
    "use": "T8/T9",
    "test": "T9",
    "cache": "T9",
    "install": "T8/T9",
}


def _add_subcommands(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="sluice_command", metavar="<command>")

    subs.add_parser("doctor", help="Show the twelve-backend inventory: free/paid and what is missing")

    subs.add_parser("status", help="Circuit, quota and cache state per backend")

    setup = subs.add_parser("setup", help="Turn off the outer web cache and point Hermes at Sluice")
    setup.add_argument(
        "--apply",
        action="store_true",
        help="Actually run the printed `hermes config set` commands (default: print only)",
    )

    use = subs.add_parser("use", help="Switch the Hermes web backend to Sluice")
    use.add_argument("target", choices=("search", "extract"), help="Which tool to switch")

    test = subs.add_parser("test", help="Send one query to one backend and report latency")
    test.add_argument("--backend", required=True, metavar="<id>", help="Backend id, e.g. exa or ddgs")
    test.add_argument("query", help="The query text")

    cache = subs.add_parser("cache", help="Inspect or clear the Sluice cache")
    cache_subs = cache.add_subparsers(dest="cache_command", metavar="<action>")
    cache_subs.add_parser("clear", help="Delete every cached search and extract row")

    install = subs.add_parser("install", help="Install a browser engine as a job")
    install.add_argument(
        "target",
        choices=("playwright", "camofox", "browserbase"),
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
    owner = COMMAND_OWNERS.get(command, "its owning task")
    print(
        f"sluice: '{command}' is not implemented in this scaffold — {owner} implements it "
        f"(PLAN.md:119-125). See REQUIREMENTS.md.",
        file=sys.stderr,
    )
    return 1


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
