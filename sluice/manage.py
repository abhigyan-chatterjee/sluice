"""Detect, install and configure the browser engines and the backend switch.

Contract: PLAN.md:108-117, DESIGN.md:212-235.

* Detect: the camofox plugin and its server health, Playwright browsers, a Browserbase key,
  packaged Chromium. Detection is read-only: no install, no start, no config write.
* Install: ``playwright install chromium`` and the camofox server through the user's
  container runtime (podman, then docker, then an npm prefix install) as persisted jobs.
  Never automatic — an install only ever starts from an explicit user command or an
  explicit dashboard request.
* Config route: ``hermes config set ...`` with the active profile's environment, a 10 s
  timeout and ``--apply`` opt-in. Sluice never writes ``config.yaml`` itself
  (DESIGN.md:59-64).

The npm fallback installs ``@askjo/camofox-browser`` into a managed directory rather than
starting it: ``npx @askjo/camofox-browser`` is a foreground server, and a job that never
returns would be a lie in the jobs table. ``install_plan()['next']`` carries the exact
command that starts it, and the first start downloads Camoufox (the vendor's own docs say
"hundreds of MB", so Sluice prints that instead of a number it cannot verify).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

CONFIG_SET_TIMEOUT_SECONDS = 10

#: Env keys child install/config processes may inherit. Provider API keys and
#: every other agent credential stay in the parent; npm, podman/docker, the
#: Playwright installer and ``hermes config set`` need only to launch, find the
#: Hermes home and reach the network.
_CHILD_ENV_KEYS: tuple[str, ...] = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH",
    "SYSTEMROOT", "TMPDIR", "TEMP", "TMP",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def _minimal_child_env() -> dict[str, str]:
    """Only allowlisted keys from the parent environment."""
    return {key: os.environ[key] for key in _CHILD_ENV_KEYS if key in os.environ}

#: The Hermes-level keys Sluice may switch, and their values (DESIGN.md:65-67, 209-210).
CONFIG_ROUTES: dict[str, str] = {
    "web.search_backend": "sluice-search",
    "web.extract_backend": "sluice-search",
    "web.cache_enabled": "false",
}

#: Install targets ``hermes sluice-search install`` accepts (PLAN.md:123).
INSTALL_TARGETS: tuple[str, ...] = ("playwright", "camofox", "browserbase")

#: A browser download can be slow; the job timeout is generous but finite.
INSTALL_TIMEOUT_SECONDS = 1800

#: Container the camofox install creates, so ``stop`` can name it without guessing.
CAMOFOX_CONTAINER = "sluice-camofox"
#: Pinned by digest (index digest of the :latest manifest at review time).
CAMOFOX_IMAGE = (
    "ghcr.io/jo-inc/camofox-browser@"
    "sha256:49f8e02f9fb0e191b406845482745efc72f835db06789e3a34e77a6ff1497046"
)
CAMOFOX_NPM_PACKAGE = "@askjo/camofox-browser@1.19.0"

#: Packaged Chromium builds Hermes or the OS may ship. ``shutil.which`` decides presence.
CHROMIUM_BINARIES: tuple[str, ...] = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)

#: Package managers that can run the camofox server as a detached container, in PLAN order.
CONTAINER_RUNTIMES: tuple[str, ...] = ("podman", "docker")


@dataclass(frozen=True)
class Detection:
    """What is present on this machine. ``None`` means the probe did not run."""

    camofox_plugin: bool | None = None
    camofox_server: bool | None = None
    playwright_chromium: bool | None = None
    browserbase_key: bool | None = None
    packaged_chromium: bool | None = None
    details: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "camofox_plugin": self.camofox_plugin,
            "camofox_server": self.camofox_server,
            "playwright_chromium": self.playwright_chromium,
            "browserbase_key": self.browserbase_key,
            "packaged_chromium": self.packaged_chromium,
            "details": dict(self.details),
        }


# ── environment probes ─────────────────────────────────────────────────────────


def hermes_home() -> Path:
    """Hermes' home for the running profile, or ``~/.hermes`` outside Hermes."""
    raw = os.environ.get("HERMES_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def _plugin_roots(home: Path | None = None) -> list[Path]:
    """Where a plugin directory can live: the active home, then the shared root."""
    roots: list[Path] = []
    for candidate in ((home or hermes_home()) / "plugins", Path.home() / ".hermes" / "plugins"):
        if candidate not in roots:
            roots.append(candidate)
    return roots


def _camofox_plugin_dir(home: Path | None = None) -> Path | None:
    for root in _plugin_roots(home):
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or "camofox" not in child.name.lower():
                continue
            if (child / "plugin.yaml").exists() or (child / "__init__.py").exists():
                return child
    return None


def _playwright_browsers_path() -> Path:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    return Path(override).expanduser() if override else Path.home() / ".cache" / "ms-playwright"


def _playwright_chromium_dir() -> Path | None:
    """A full Chromium build, not the headless shell the ladder refuses to rely on alone."""
    root = _playwright_browsers_path()
    try:
        children = sorted(root.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_dir() and child.name.startswith("chromium-"):
            return child
    return None


def _default_http_get(url: str, timeout: float) -> dict[str, Any] | None:
    """GET a JSON object; ``None`` on any non-200, non-JSON or transport failure."""
    try:
        import httpx
    except ModuleNotFoundError:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
        if response.status_code != 200:
            return None
        payload = response.json()
    except Exception:  # noqa: BLE001 - a health probe must never raise
        return None
    return payload if isinstance(payload, dict) else None


def camofox_health(
    url: str,
    *,
    http_get: Callable[[str, float], dict[str, Any] | None] | None = None,
    timeout: float = 2.0,
) -> bool:
    """``GET {url}/health`` reports ``ok: true`` (the camofox-browser contract)."""
    getter = http_get or _default_http_get
    payload = getter(url.rstrip("/") + "/health", timeout)
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("ok"))


def detect(
    *,
    settings: Mapping[str, Any] | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] | None = None,
    http_get: Callable[[str, float], dict[str, Any] | None] | None = None,
    health_timeout: float = 2.0,
) -> Detection:
    """Probe every browser engine without side effects. Injectable for tests."""
    which = which or shutil.which
    details: dict[str, str] = {}

    plugin_dir = _camofox_plugin_dir(home)
    if plugin_dir is not None:
        details["camofox_plugin"] = str(plugin_dir)

    if settings is None:
        from .config import load_settings

        settings = load_settings()
    url = str(settings.get("camofox_url") or settings.get("CAMOFOX_URL") or "http://localhost:9377")
    details["camofox_url"] = url
    server_ok = camofox_health(url, http_get=http_get, timeout=health_timeout)

    playwright_dir = _playwright_chromium_dir()
    if playwright_dir is not None:
        details["playwright_chromium"] = str(playwright_dir)
    details["playwright_browsers_path"] = str(_playwright_browsers_path())

    for binary in CHROMIUM_BINARIES:
        found = which(binary)
        if found:
            details["packaged_chromium"] = found
            break

    browserbase = bool(str(settings.get("BROWSERBASE_API_KEY") or "").strip())

    return Detection(
        camofox_plugin=plugin_dir is not None,
        camofox_server=server_ok,
        playwright_chromium=playwright_dir is not None,
        browserbase_key=browserbase,
        packaged_chromium=bool(details.get("packaged_chromium")),
        details=details,
    )


def missing_steps(
    detection: Detection, *, settings: Mapping[str, Any] | None = None
) -> list[dict[str, str]]:
    """Actionable gaps, each with the exact next command (PLAN.md:111, DESIGN.md:223-225)."""
    steps: list[dict[str, str]] = []
    if not detection.camofox_plugin:
        steps.append(
            {
                "name": "camofox plugin",
                "detail": "the optional camofox plugin is not installed; Sluice works without it",
                "next": "hermes plugins install camofox",
            }
        )
    if not detection.camofox_server:
        steps.append(
            {
                "name": "camofox server",
                "detail": "no camofox server answered /health; the ladder stops at Chromium",
                "next": "hermes sluice-search install camofox",
            }
        )
    if not detection.playwright_chromium:
        steps.append(
            {
                "name": "playwright chromium",
                "detail": "no Playwright Chromium build; the ladder falls back to packaged Chromium",
                "next": "hermes sluice-search install playwright",
            }
        )
    if not detection.browserbase_key:
        steps.append(
            {
                "name": "browserbase key",
                "detail": "BROWSERBASE_API_KEY is not set; Browserbase is optional",
                "next": "set BROWSERBASE_API_KEY in Settings ▸ Plugins",
            }
        )
    return steps


# ── config route ────────────────────────────────────────────────────────────────


def config_command(key: str) -> list[str]:
    """The exact command Sluice prints (and runs only with ``--apply``) for one route."""
    if key not in CONFIG_ROUTES:
        raise KeyError(f"{key!r} is not a Sluice-managed config key; expected one of {sorted(CONFIG_ROUTES)}")
    return ["hermes", "config", "set", key, CONFIG_ROUTES[key]]


def _default_config_runner(
    command: Sequence[str], *, timeout: float
) -> tuple[int, str, str]:
    """Run one ``hermes config set`` with only the allowlisted environment. No retry, no shell."""
    try:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_minimal_child_env(),
        )
    except FileNotFoundError:
        return 127, "", "the 'hermes' executable is not on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {int(timeout)}s"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_config_set(
    key: str,
    *,
    apply: bool = False,
    runner: Callable[..., tuple[int, str, str]] | None = None,
    timeout: float = CONFIG_SET_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Print the route, and run it only when *apply* is explicit."""
    command = config_command(key)
    result: dict[str, Any] = {
        "key": key,
        "value": CONFIG_ROUTES[key],
        "command": command,
        "applied": False,
        "ok": None,
        "returncode": None,
        "output": "",
        "error": None,
    }
    if not apply:
        return result
    run = runner or _default_config_runner
    returncode, stdout, stderr = run(command, timeout=timeout)
    output = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    result.update(
        applied=True,
        ok=returncode == 0,
        returncode=returncode,
        output=output,
        error=None if returncode == 0 else (output or f"command exited {returncode}"),
    )
    return result


def setup_routes(
    *, apply: bool = False, runner: Callable[..., tuple[int, str, str]] | None = None
) -> list[dict[str, Any]]:
    """All three routes in the order DESIGN.md:209-210 states them."""
    return [run_config_set(key, apply=apply, runner=runner) for key in CONFIG_ROUTES]


# ── outer web cache ────────────────────────────────────────────────────────────


def hermes_web_cache_dir(home: Path | None = None) -> Path:
    """Hermes' own ``cache/web`` directory (DESIGN.md:80-82, 209-210)."""
    return (home or hermes_home()) / "cache" / "web"


def clear_outer_web_cache(home: Path | None = None) -> dict[str, Any]:
    """Delete Hermes' outer web cache. Returns counts; never raises."""
    target = hermes_web_cache_dir(home)
    removed = 0
    bytes_freed = 0
    if not target.exists():
        return {"path": str(target), "removed": 0, "bytes": 0, "existed": False}
    for entry in sorted(target.rglob("*"), reverse=True):
        try:
            if entry.is_file() or entry.is_symlink():
                bytes_freed += entry.stat().st_size
                entry.unlink()
                removed += 1
            elif entry.is_dir():
                try:
                    entry.rmdir()
                except OSError:
                    pass
        except OSError:
            continue
    return {"path": str(target), "removed": removed, "bytes": bytes_freed, "existed": True}


# ── install plans and jobs ─────────────────────────────────────────────────────


def _default_install_runner(command: Sequence[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
            env=_minimal_child_env(),
        )
    except FileNotFoundError:
        return 127, f"{command[0]} is not on PATH"
    except subprocess.TimeoutExpired:
        return 124, f"install timed out after {INSTALL_TIMEOUT_SECONDS}s"
    return proc.returncode, "\n".join(
        part for part in ((proc.stdout or "").strip(), (proc.stderr or "").strip()) if part
    )


def camofox_package_dir(home: Path | None = None) -> Path:
    override = os.environ.get("SLUICE_CAMOFOX_DIR")
    if override:
        return Path(override).expanduser()
    return (home or hermes_home()) / "plugin-data" / "sluice-search" / "camofox"


def install_plan(
    target: str, *, which: Callable[[str], str | None] | None = None, home: Path | None = None
) -> dict[str, Any]:
    """The commands one install job runs, plus the command that comes after it.

    ``supported: False`` means there is nothing to install on this machine and ``next``
    names the action that does work instead (a key route for Browserbase, or installing a
    runtime for camofox when neither a container runtime nor npm is present).
    """
    if target not in INSTALL_TARGETS:
        raise KeyError(f"unknown install target {target!r}; expected one of {list(INSTALL_TARGETS)}")
    which = which or shutil.which
    if target == "playwright":
        return {
            "target": target,
            "supported": True,
            "commands": [[sys.executable, "-m", "playwright", "install", "chromium"]],
            "next": "hermes sluice-search install camofox",
            "note": "Downloads the Chromium build Playwright drives. Explicit action only.",
        }

    if target == "camofox":
        runtime = next((found for name in CONTAINER_RUNTIMES if (found := which(name))), "")
        if runtime:
            return {
                "target": target,
                "supported": True,
                "commands": [
                    [
                        runtime,
                        "run",
                        "-d",
                        "--name",
                        CAMOFOX_CONTAINER,
                        "-p",
                        "9377:9377",
                        "-e",
                        "CAMOFOX_PORT=9377",
                        CAMOFOX_IMAGE,
                    ]
                ],
                "next": "hermes sluice-search doctor",
                "note": "Starts the camofox server in a detached container; the first start downloads Camoufox.",
            }
        npm = which("npm")
        if not npm:
            return {
                "target": target,
                "supported": False,
                "commands": [],
                "next": "install podman, docker or Node.js, then run: hermes sluice-search install camofox",
                "note": "No container runtime and no npm on PATH.",
            }
        prefix = camofox_package_dir(home)
        return {
            "target": target,
            "supported": True,
            "commands": [[npm, "install", "--prefix", str(prefix), CAMOFOX_NPM_PACKAGE]],
            "next": f"npx --yes {CAMOFOX_NPM_PACKAGE}",
            "note": (
                "No container runtime: installs the server package into the Sluice data "
                "directory. Start it with the command above; the first start downloads Camoufox."
            ),
        }

    return {
        "target": "browserbase",
        "supported": False,
        "commands": [],
        "next": "hermes config set browser.cloud_provider browserbase",
        "note": (
            "Browserbase is a hosted browser service: there is nothing to install. Put "
            "BROWSERBASE_API_KEY in Settings ▸ Plugins, then point Hermes at it with the "
            "command above."
        ),
    }


def _run_install_job(
    store: Any,
    job_id: str,
    commands: Sequence[Sequence[str]],
    runner: Callable[[Sequence[str]], tuple[int, str]],
) -> None:
    """Execute the plan inside the job record; failures are recorded, never raised."""
    store.update_job(job_id, status="running")
    outputs: list[str] = []
    for command in commands:
        returncode, output = runner(command)
        outputs.append(f"$ {shlex.join(list(command))}\n{output}".strip())
        if returncode != 0:
            store.update_job(
                job_id,
                status="failed",
                output="\n\n".join(outputs),
                error=f"command exited {returncode}: {shlex.join(list(command))}",
            )
            return
    store.update_job(job_id, status="succeeded", output="\n\n".join(outputs))


def start_install_job(
    store: Any,
    target: str,
    *,
    wait: bool = False,
    runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    which: Callable[[str], str | None] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Create a persisted job, then run the plan (inline with *wait*, else in a thread).

    The row is written before any work starts, so a crash or restart leaves evidence and
    ``Store.recover_stale_jobs`` marks it failed on the next open instead of losing it.
    """
    plan = install_plan(target, which=which, home=home)
    if not plan["supported"]:
        return {
            "started": False,
            "target": target,
            "job": None,
            "commands": [],
            "note": plan["note"],
            "next": plan["next"],
        }
    command_line = " && ".join(shlex.join(list(command)) for command in plan["commands"])
    job_id = store.create_job("install", target, command=command_line, status="queued")
    run = runner or _default_install_runner
    if wait:
        _run_install_job(store, job_id, plan["commands"], run)
    else:
        thread = threading.Thread(
            target=_run_install_job,
            args=(store, job_id, plan["commands"], run),
            name=f"sluice-install-{job_id}",
            daemon=True,
        )
        thread.start()
    return {
        "started": True,
        "target": target,
        "job": store.get_job(job_id),
        "commands": [list(command) for command in plan["commands"]],
        "note": plan["note"],
        "next": plan["next"],
    }


def stop_camofox(
    *, which: Callable[[str], str | None] | None = None, runner: Callable[[Sequence[str]], tuple[int, str]] | None = None
) -> dict[str, Any]:
    """Stop the container the install created. A no-op when no container runtime exists."""
    which = which or shutil.which
    runtime = next((found for name in CONTAINER_RUNTIMES if (found := which(name))), "")
    if not runtime:
        return {
            "stopped": False,
            "runtime": "",
            "error": "no container runtime (podman or docker) on PATH; stop the server yourself",
        }
    command = [runtime, "stop", CAMOFOX_CONTAINER]
    run = runner or _default_install_runner
    returncode, output = run(command)
    return {
        "stopped": returncode == 0,
        "runtime": runtime,
        "command": command,
        "output": output,
        "error": None if returncode == 0 else output or f"command exited {returncode}",
    }


def render_detection(detection: Detection) -> str:
    """One-line-per-engine summary for ``hermes sluice-search doctor``."""
    def mark(value: bool | None) -> str:
        return "yes" if value else ("no" if value is False else "unknown")

    lines = [
        f"camofox plugin:       {mark(detection.camofox_plugin)}",
        f"camofox server:       {mark(detection.camofox_server)}"
        + (f"  ({detection.details.get('camofox_url')})" if detection.details.get("camofox_url") else ""),
        f"playwright chromium:  {mark(detection.playwright_chromium)}",
        f"packaged chromium:    {mark(detection.packaged_chromium)}"
        + (f"  ({detection.details['packaged_chromium']})" if detection.details.get("packaged_chromium") else ""),
        f"browserbase key:      {mark(detection.browserbase_key)}",
    ]
    return "\n".join(lines)

