"""Block classifier and fetch ladder.

Contract: PLAN.md:95-106 and RESEARCH-blocks.md:129-182 (§6, the feature spec). The classifier is deterministic and
offline: status, headers, cookie names, body markers, body size and content type in, one
of nine classes out. The ladder is policy, honest HTTP (``Sluice/1.0`` plus trafilatura),
real Chromium through Playwright, then the Camofox REST server, then stop.

Hard rules from PLAN.md:105 that this module must enforce: 401, 402 and 403 stop the
ladder; 429 honours ``Retry-After``; a CAPTCHA is never solved; identity is never rotated
after a refusal. A robots.txt or Content-Signal disallow returns ``blocked_by_policy``
with the rule quoted.

Implemented in T7.
"""

from __future__ import annotations

from typing import Any, Mapping

#: The nine classes, in the order PLAN.md:98 lists them.
BLOCK_CLASSES: tuple[str, ...] = (
    "ok",
    "js_required",
    "rate_limited",
    "challenge",
    "forbidden",
    "auth_required",
    "payment_required",
    "policy_blocked",
    "error",
)

#: Classes that stop the ladder instead of climbing (RESEARCH-blocks.md:145-149).
STOP_CLASSES: frozenset[str] = frozenset(
    {"forbidden", "auth_required", "payment_required", "policy_blocked", "error"}
)

#: Ladder rungs, cheapest first (RESEARCH-blocks.md:151-163).
STAGES: tuple[str, ...] = ("http", "chromium", "camofox")

CHROMIUM_TIMEOUT_SECONDS = 15
ROBOTS_TTL_SECONDS = 86400


def classify(
    status: int,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    content_type: str = "",
) -> str:
    """Name what a response is. Implemented in T7; every input above is part of the contract."""
    raise NotImplementedError(
        "sluice.ladder.classify lands in T7 (PLAN.md:95-106, RESEARCH-blocks.md:134-149): "
        "deterministic classification into the nine block classes."
    )


def fetch(url: str, **kwargs: Any) -> dict[str, Any]:
    """Run one URL through the ladder and return a per-URL item. Implemented in T7."""
    raise NotImplementedError(
        "sluice.ladder.fetch lands in T7 (PLAN.md:95-106): robots.txt gate, honest HTTP, "
        "real Chromium, Camofox, then stop with a block report."
    )
