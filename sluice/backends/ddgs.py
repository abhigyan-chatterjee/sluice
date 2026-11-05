"""ddgs: the local ``ddgs`` package, no HTTP of our own.

Contract: ``references/endpoints.md#ddgs`` (lines 47-53) and PLAN.md:74-80. Search only.

``ddgs`` (and its ``primp`` transport) can block inside native code while holding the GIL,
so a thread-pool timeout may never fire and Ctrl+C can freeze the process (PLAN.md:78,
DESIGN.md:191-193). Every search therefore runs in a **fresh interpreter** that the parent
kills after a hard 12 s wall clock.

The child is a ``subprocess`` running a ``-c`` script, not a ``multiprocessing`` spawn. A
spawned child unpickles the worker by module name; when Hermes loads this file as the
directory plugin ``hermes_plugins.sluice.sluice.backends.ddgs`` that name does not exist in
a fresh interpreter (``hermes_plugins`` is a synthetic package), so the child died with
``ModuleNotFoundError`` before running a query. A ``-c`` script imports nothing from the
loader namespace: it reads a JSON request on stdin, imports ``ddgs`` plus one helper from
this checkout (``PYTHONPATH`` starts with the repository root), and prints one JSON object.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from . import Backend, BackendSpec
from ._http import call_timeout, failure

SPEC = BackendSpec(
    id="ddgs",
    label="ddgs",
    tiers=("keyless",),
    search=True,
    extract=False,
    secret_env=None,
    summary="Local ddgs package, no HTTP; runs in a disposable child process with a hard kill and a 12 s wall clock.",
    task="T4",
)

#: PLAN.md:78 fixes the wall clock. The package's own ``timeout=10`` only bounds one HTTP
#: request inside its multi-engine retry loop, not the whole call.
WALL_CLOCK_SECONDS = 12.0

#: references/endpoints.md:50-51.
FRESHNESS_MAP: dict[str, str] = {"day": "d", "week": "w", "month": "m", "year": "y"}

#: The child program. It never imports this module (or the loader's ``hermes_plugins``
#: namespace): query, limit and timelimit arrive as one JSON object on stdin, and one JSON
#: object leaves on stdout. ``search_row`` comes from this checkout through ``PYTHONPATH``.
_CHILD_SOURCE = r"""
import json
import sys

from sluice.backends._http import search_row


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        query = str(payload.get("query") or "")
        limit = max(1, int(payload.get("limit") or 5))
        timelimit = str(payload.get("timelimit") or "")
        from ddgs import DDGS

        rows = []
        with DDGS(timeout=10) as client:
            kwargs = {"max_results": limit}
            if timelimit:
                kwargs["timelimit"] = timelimit
            for index, hit in enumerate(client.text(query, **kwargs)):
                if index >= limit:
                    break
                rows.append(
                    search_row(
                        str(hit.get("href") or hit.get("url") or ""),
                        str(hit.get("title") or ""),
                        str(hit.get("body") or hit.get("snippet") or ""),
                        index + 1,
                    )
                )
        result = {"status": "ok", "payload": rows}
    except BaseException as exc:  # noqa: BLE001 - every child failure becomes a JSON message
        result = {"status": "error", "payload": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


main()
"""


def _child_env() -> dict[str, str]:
    """The parent environment with this checkout's root first on ``PYTHONPATH``.

    ``Path(__file__).resolve().parents[2]`` is the repository root both for a git checkout
    and for ``~/.hermes/plugins/sluice``; the child needs it to import ``search_row`` from
    the same copy of Sluice that started it.
    """
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[2])
    entries = [entry for entry in (env.get("PYTHONPATH") or "").split(os.pathsep) if entry]
    entries = [entry for entry in entries if entry != root]
    env["PYTHONPATH"] = os.pathsep.join([root, *entries])
    return env


def run_in_child(
    query: str,
    limit: int,
    timelimit: str,
    *,
    timeout: float = WALL_CLOCK_SECONDS,
) -> tuple[str, Any]:
    """Run one query in a fresh interpreter; ``("ok"|"error"|"timeout", payload)``.

    The child is killed at the wall clock and reaped, then its pipes are closed. Nothing is
    left running: a leaked ddgs child would keep a core pinned long after the search was
    abandoned.
    """
    request = json.dumps({"query": query, "limit": limit, "timelimit": timelimit})
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", _CHILD_SOURCE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_child_env(),
        )
    except OSError as exc:
        return "error", f"could not start the ddgs worker: {exc}"

    try:
        try:
            stdout, stderr = process.communicate(request, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:  # pragma: no cover - kill is immediate on POSIX
                pass
            return "timeout", None
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 - a closed pipe is not an error
                    pass

    try:
        message = json.loads(stdout)
    except (TypeError, ValueError):
        lines = [line for line in (stderr or "").strip().splitlines() if line.strip()]
        detail = lines[-1] if lines else "the ddgs worker exited without a result"
        return "error", detail
    if not isinstance(message, dict):
        return "error", "the ddgs worker returned a malformed result"
    status = str(message.get("status") or "error")
    if status not in ("ok", "error"):
        return "error", f"the ddgs worker returned an unknown status: {status}"
    return status, message.get("payload")


class DdgsBackend(Backend):
    """Search-only vendor whose native code must never run on the agent thread."""

    spec = SPEC
    capabilities = ("search",)

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        *,
        runner: Callable[[str, int, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__(settings)
        #: Test seam: an in-process query function instead of a spawned child.
        self._runner = runner

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 50))
        timelimit = FRESHNESS_MAP.get(str(freshness or "").lower(), "")
        if self._runner is not None:
            try:
                rows = self._runner(query, safe_limit, timelimit)
            except Exception as exc:  # noqa: BLE001 - adapter never raises
                return failure(f"ddgs failed: {exc}")
            return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                    "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok"}

        started = time.monotonic()
        # The router's per-backend budget is shorter than the hard wall clock, so it
        # shortens this call; a direct call (the CLI, a probe) gets the full 12 s.
        wall_clock = call_timeout(kwargs, WALL_CLOCK_SECONDS)
        status, payload = run_in_child(query, safe_limit, timelimit, timeout=wall_clock)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if status == "timeout":
            return failure(
                f"ddgs did not answer within {wall_clock:.1f}s and the worker was killed",
                kind="timeout",
            )
        if status == "error":
            message = str(payload)
            if "ModuleNotFoundError" in message or "ImportError" in message:
                message = (
                    "the ddgs package is not installed. Install it with "
                    "`pip install 'ddgs>=9,<10'`, or disable the keyless tier."
                )
            return failure(f"ddgs failed: {message}")
        rows = payload if isinstance(payload, list) else []
        return {"ok": True, "results": rows, "items": [], "error": None, "quota": False,
                "out_of_credit": False, "retry_after": None, "status": 200, "kind": "ok",
                "elapsed_ms": elapsed_ms}

    def extract(self, urls: Sequence[str], **kwargs: Any) -> list[dict[str, Any]]:
        raise NotImplementedError("ddgs is search-only; extraction runs through the fetch ladder")
