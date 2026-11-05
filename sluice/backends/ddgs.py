"""ddgs: the local ``ddgs`` package, no HTTP of our own.

Contract: ``references/endpoints.md#ddgs`` (lines 47-53) and PLAN.md:74-80. Search only.

``ddgs`` (and its ``primp`` transport) can block inside native code while holding the GIL,
so a thread-pool timeout may never fire and Ctrl+C can freeze the process (PLAN.md:78,
DESIGN.md:191-193). Every search therefore runs in a **spawned child process** that the
parent terminates and then kills after a hard 12 s wall clock.
"""

from __future__ import annotations

import multiprocessing
import time
from typing import Any, Callable, Sequence

from . import Backend, BackendSpec
from ._http import failure, search_row

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


def _worker(connection: Any, query: str, limit: int, timelimit: str) -> None:
    """Child-process entry point: query ddgs and send one ``(status, payload)`` message."""
    try:
        from ddgs import DDGS  # type: ignore

        rows: list[dict[str, Any]] = []
        with DDGS(timeout=10) as client:
            kwargs: dict[str, Any] = {"max_results": limit}
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
        connection.send(("ok", rows))
    except BaseException as exc:  # noqa: BLE001 - every child failure becomes a message
        try:
            connection.send(("error", f"{type(exc).__name__}: {exc}"))
        except Exception:  # noqa: BLE001 - the parent may already have closed the pipe
            pass
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def run_in_child(
    target: Callable[..., None],
    args: tuple[Any, ...],
    *,
    timeout: float = WALL_CLOCK_SECONDS,
) -> tuple[str, Any]:
    """Run *target* in a spawned process; ``("ok"|"error"|"timeout", payload)``.

    The child is terminated and, if it ignores SIGTERM, killed. Nothing is left running:
    a leaked ddgs child would keep a core pinned long after the search was abandoned.
    """
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(child, *args), daemon=True)
    process.start()
    child.close()
    try:
        if parent.poll(timeout):
            status, payload = parent.recv()
        else:
            status, payload = "timeout", None
    except EOFError:  # child died before sending anything
        status, payload = "error", "the ddgs worker exited without a result"
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive():
                process.kill()
                process.join(1.0)
        else:
            process.join(0.1)
    return status, payload


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
        status, payload = run_in_child(_worker, (query, safe_limit, timelimit))
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if status == "timeout":
            return failure(
                f"ddgs did not answer within {int(WALL_CLOCK_SECONDS)}s and the worker was killed",
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
