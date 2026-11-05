"""Detect, install and configure the browser engines and the backend switch.

Contract: PLAN.md:108-117.

* Detect: the camofox plugin and its server health, Playwright browsers, a Browserbase key,
  packaged Chromium.
* Install: ``playwright install chromium`` and the camofox server (podman, then docker, then
  npm) as jobs behind the dashboard API. Never automatic — an install only ever starts from
  an explicit user command.
* Config route: ``hermes config set web.search_backend sluice`` (and the extract
  equivalent) with the active profile's environment, a 10 s timeout and ``--apply`` opt-in.
  Sluice never writes ``config.yaml`` itself (DESIGN.md:59-64).

Implemented in T8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONFIG_SET_TIMEOUT_SECONDS = 10

#: The two Hermes-level keys Sluice may switch, and their values (DESIGN.md:65-67).
CONFIG_ROUTES: dict[str, str] = {
    "web.search_backend": "sluice",
    "web.extract_backend": "sluice",
}

#: Install targets ``hermes sluice install`` accepts (PLAN.md:123).
INSTALL_TARGETS: tuple[str, ...] = ("playwright", "camofox", "browserbase")


@dataclass(frozen=True)
class Detection:
    """What is present on this machine. All fields default to "unknown" until T8 probes."""

    camofox_plugin: bool | None = None
    camofox_server: bool | None = None
    playwright_chromium: bool | None = None
    browserbase_key: bool | None = None
    packaged_chromium: bool | None = None


def detect() -> Detection:
    """Probe the local environment without installing anything. Implemented in T8."""
    raise NotImplementedError(
        "sluice.manage.detect lands in T8 (PLAN.md:108-117): camofox plugin and server, "
        "Playwright browsers, Browserbase key, packaged Chromium."
    )


def config_command(key: str) -> list[str]:
    """The exact command Sluice prints (and runs only with ``--apply``) for one route."""
    if key not in CONFIG_ROUTES:
        raise KeyError(f"{key!r} is not a Sluice-managed config key; expected one of {sorted(CONFIG_ROUTES)}")
    return ["hermes", "config", "set", key, CONFIG_ROUTES[key]]


def run_config_set(key: str, *, apply: bool = False) -> dict[str, Any]:
    """Print the route, and run it only when *apply* is explicit. Implemented in T8."""
    command = config_command(key)
    if not apply:
        return {"applied": False, "command": command}
    raise NotImplementedError(
        "sluice.manage.run_config_set lands in T8 (PLAN.md:114): subprocess with the active "
        "profile environment, 10 s timeout, no blind retry."
    )
