"""Block classifier and fetch ladder.

Contract: PLAN.md:95-106 and RESEARCH-blocks.md:129-182 (§6, the feature spec). The classifier
is deterministic and offline: status, headers, cookie names, body markers, body size and
content type in, one of nine classes out. The ladder is policy, honest HTTP (``Sluice/1.0``
plus trafilatura), real Chromium through Playwright, then the Camofox REST server, then stop.

Hard rules this module enforces (PLAN.md:105):

* 401, 402 and 403 stop the ladder. A 403 is never retried, in this session or another.
* A 429 honours ``Retry-After`` and retries once, and only once.
* A CAPTCHA is never solved, no solver service is called, identity is never rotated.
* A robots.txt, ``X-Robots-Tag`` or ``Content-Signal`` disallow returns
  ``blocked_by_policy`` with the rule quoted.

Because ``tools/web_tools_truncate._trim_results`` drops every extract key except ``url``,
``title``, ``content``, ``error`` and ``blocked_by_policy`` (DESIGN.md:73-77), the class and
the final stage ride as one short line at the top of ``content`` instead of in ``metadata``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from html import unescape
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .store import NS_ROBOTS, Store

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

#: Classes a browser rung can plausibly fix, so a remembered verdict starts higher.
CLIMB_CLASSES: frozenset[str] = frozenset({"challenge", "js_required"})

#: Ladder rungs, cheapest first (RESEARCH-blocks.md:151-163).
STAGES: tuple[str, ...] = ("http", "chromium", "camofox")

CHROMIUM_TIMEOUT_SECONDS = 15
ROBOTS_TTL_SECONDS = 86400  # PLAN.md:99
HTTP_TIMEOUT_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 5.0
USER_AGENT = "Sluice/1.0"
PROVENANCE_PREFIX = "[sluice: stage={stage} status={block_class}]"

#: Body text no legitimate page carries. Checked only when the status already looks like a
#: refusal, because a blog post may legitimately discuss CAPTCHAs.
_BODY_CHALLENGE_MARKERS = (
    "just a moment...",
    "attention required! | cloudflare",
    "cf_chl_opt",
    "cf_chl_prog",
    "px-captcha",
    "captcha-delivery.com",
    "enable javascript and cookies to continue",
    "_incapsula_resource",
)
_JS_HINTS = (
    "enable javascript",
    "javascript is required",
    "javascript must be enabled",
    "please turn on javascript",
    "requires javascript",
)
_COOKIE_MARKERS = (
    "__cf_bm",
    "cf_clearance",
    "datadome",
    "_px3",
    "visid_incap",
    "incap_ses",
    "_abck",
    "_fs_ch_",
    "_fs_cd_cp_",
)
_BLOCK_STATUSES = frozenset({401, 402, 403, 406, 429, 503})


# ── classifier ─────────────────────────────────────────────────────────────────────


def _lower_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    merged: dict[str, list[str]] = {}
    for key, value in headers.items():
        merged.setdefault(str(key).lower(), []).append(str(value))
    return {key: ", ".join(values) for key, values in merged.items()}


def classify(
    status: int,
    headers: Mapping[str, str] | None = None,
    body: bytes | str = b"",
    content_type: str = "",
) -> str:
    """Name what a response is. Deterministic, offline, no guessing.

    Vendor markers are checked before status codes because the status alone lies: Cloudflare
    challenges arrive as 2xx or 403, Imperva answers 200 with an 83-byte page, AWS WAF uses
    202/405, Kasada 429 and Fastly 406 (RESEARCH-blocks.md:42-61).
    """
    head = _lower_headers(headers)
    raw = body if isinstance(body, bytes) else str(body or "").encode("utf-8", "replace")
    text = raw.decode("utf-8", errors="replace")
    lowered = text[:20_000].lower()
    ctype = (content_type or head.get("content-type", "")).lower()
    size = len(raw)

    if _has_challenge_marker(status, head, lowered):
        return "challenge"
    if status == 401:
        return "auth_required"
    if status == 402 or "crawler-price" in head:
        return "payment_required"
    if status == 403:
        return "forbidden"
    if status == 429:
        return "rate_limited"
    if status == 503 and "retry-after" in head:
        return "rate_limited"
    if status >= 500:
        return "error"
    if status >= 400 or status == 0:
        return "error"

    # 2xx from here. A non-HTML body (JSON, plain text, PDF) has no shell to detect.
    if ctype and "html" not in ctype and "xml" not in ctype:
        return "ok"
    if _looks_like_js_shell(lowered, size):
        return "js_required"
    return "ok"


def _has_challenge_marker(status: int, head: Mapping[str, str], lowered_body: str) -> bool:
    """Vendor markers, strongest first (RESEARCH-blocks.md:51-61).

    A cookie name alone is diagnostic, not decisive: ``__cf_bm`` and ``_abck`` are set on
    successful responses too. Cookies only name a challenge when the status is also a block
    status.
    """
    if "challenge" in head.get("cf-mitigated", "").lower():
        return True
    if "protected" in head.get("x-datadome", "").lower():
        return True
    if head.get("x-amzn-waf-action", "").lower() in ("challenge", "captcha"):
        return True
    if head.get("x-amzn-waf-action") and status in (202, 405):
        return True
    if any(key.startswith("x-kpsdk-") for key in head):
        return True
    if head.get("sec-cpt") and status == 428:
        return True
    if head.get("akamai-grn") and status in (403, 428):
        return True
    if head.get("x-iinfo") and (status in (403, 429) or len(lowered_body) < 1024):
        return True
    if status in (406, 403) and any(token in head.get("set-cookie", "") for token in ("_fs_ch_", "_fs_cd_cp_")):
        return True
    if status in _BLOCK_STATUSES and any(token in head.get("set-cookie", "").lower() for token in _COOKIE_MARKERS):
        return True
    return any(marker in lowered_body for marker in _BODY_CHALLENGE_MARKERS)


def _looks_like_js_shell(lowered_body: str, size: int) -> bool:
    if any(hint in lowered_body for hint in _JS_HINTS):
        return True
    visible = re.sub(
        r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", lowered_body, flags=re.DOTALL
    )
    # A near-empty HTML document with no readable text is a shell, not a page.
    return size < 600 and len(visible.split()) < 8


# ── policy stage ──────────────────────────────────────────────────────────────────


@dataclass
class RobotsRules:
    """One origin's rules for our token, merged from every matching group."""

    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow?, pattern)
    crawl_delay: float | None = None
    source_status: int = 200

    def allowed(self, path: str) -> tuple[bool, str]:
        """Longest match wins; Allow wins a tie (RFC 9309 §2.2.2)."""
        best: tuple[int, bool, str] | None = None
        for allow, pattern in self.rules:
            if not pattern:
                continue
            if _match_rule(pattern, path):
                score = len(pattern)
                if best is None or score > best[0] or (score == best[0] and allow and not best[1]):
                    best = (score, allow, pattern)
        if best is None:
            return True, ""
        return best[1], best[2]


def _match_rule(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = re.escape(body).replace(r"\*", ".*") + ("$" if anchored else ".*")
    try:
        return re.match(regex, path) is not None
    except re.error:  # pragma: no cover - re.escape output is always valid
        return False


def parse_robots(text: str, *, token: str = "sluice") -> RobotsRules:
    """Parse robots.txt for our product token, falling back to the ``*`` group.

    Groups are separated by blank lines (RFC 9309 §2.2.1); a ``User-agent`` line that
    follows rules starts a new group even without a blank line, which is how most real
    files are written. A group with no rules means "no restriction" and must still shadow
    the ``*`` group for the agents it names.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []

    def flush(agents: list[str], rules: list[tuple[bool, str]], delay: float | None) -> None:
        if agents:
            groups.append((list(agents), list(rules), delay))

    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    delay: float | None = None
    for raw in normalized.split("\n"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            flush(agents, rules, delay)
            agents, rules, delay = [], [], None
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if rules:
                flush(agents, rules, delay)
                agents, rules, delay = [], [], None
            agents.append(value.lower())
        elif key in ("allow", "disallow"):
            if agents:
                rules.append((key == "allow", value))
        elif key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                delay = None
    flush(agents, rules, delay)

    exact_rules: list[tuple[bool, str]] | None = None
    exact_delay: float | None = None
    wildcard_rules: list[tuple[bool, str]] | None = None
    wildcard_delay: float | None = None
    for group_agents, group_rules, group_delay in groups:
        names = [agent.split("/")[0].strip() for agent in group_agents]
        if token in names and exact_rules is None:
            exact_rules, exact_delay = group_rules, group_delay
        if "*" in names and wildcard_rules is None:
            wildcard_rules, wildcard_delay = group_rules, group_delay
    if exact_rules is not None:
        return RobotsRules(rules=exact_rules, crawl_delay=exact_delay)
    if wildcard_rules is not None:
        return RobotsRules(rules=wildcard_rules, crawl_delay=wildcard_delay)
    return RobotsRules()


def robots_allowed(
    url: str,
    *,
    store: Store | None = None,
    client: Any = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """``(allowed, rule)`` for one URL, from a 24 h per-origin cache.

    RFC 9309: a 4xx answer means crawlable, a 5xx answer means treat as disallowed
    (RESEARCH-blocks.md:109-112). A transport error is not an answer; the fetch proceeds and
    reports its own failure.
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    stamp = time.time() if now is None else float(now)

    cached = store.cache_get(NS_ROBOTS, origin, now=stamp) if store is not None else None
    if cached is None:
        status, text = _fetch_robots(origin, client)
        cached = {"status": status, "text": text}
        if store is not None:
            store.cache_put(NS_ROBOTS, origin, cached, ROBOTS_TTL_SECONDS, now=stamp)

    status = int(cached.get("status") or 0)
    if 400 <= status < 500:
        return True, ""
    if status >= 500:
        return False, f"robots.txt returned HTTP {status}, treated as disallow (RFC 9309)"
    if status == 0:
        return True, ""

    rules = parse_robots(str(cached.get("text") or ""))
    allowed, pattern = rules.allowed(path)
    if allowed:
        return True, ""
    return False, pattern


def _fetch_robots(origin: str, client: Any) -> tuple[int, str]:
    from .backends._http import new_client

    owns = client is None
    http = client or new_client(10.0)
    try:
        response = http.get(
            f"{origin}/robots.txt",
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
        )
    except Exception:  # noqa: BLE001 - an unreachable origin is not a robots verdict
        return 0, ""
    finally:
        if owns:
            http.close()
    return response.status_code, response.text or ""


def response_policy_block(headers: Mapping[str, str] | None) -> str | None:
    """A rule quoted from ``X-Robots-Tag`` or ``Content-Signal``, or ``None``.

    ``ai-train`` is deliberately not checked: it governs model training, not whether this
    user's extraction request may read the page. ``search=no`` and ``ai-input=no`` are.
    """
    head = _lower_headers(headers)
    tag = head.get("x-robots-tag", "")
    for directive in ("noindex", "noai", "none"):
        if directive in tag.lower():
            return f"X-Robots-Tag: {tag}"
    signal = head.get("content-signal", "")
    for directive in ("search=no", "ai-input=no"):
        if directive in signal.lower():
            return f"Content-Signal: {signal}"
    return None


# ── stages ────────────────────────────────────────────────────────────────────────


@dataclass
class StageOutcome:
    """What one rung saw. ``skipped`` means the engine is not installed, not that it failed."""

    stage: str
    block_class: str
    status: int | None = None
    url: str = ""
    title: str = ""
    content: str = ""
    error: str | None = None
    skipped: bool = False
    reason: str = ""
    elapsed_ms: float = 0.0
    signals: list[str] = field(default_factory=list)
    http_headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "block_class": self.block_class,
            "status": self.status,
            "ms": self.elapsed_ms,
            "skipped": self.skipped,
            "reason": self.reason,
            "signals": list(self.signals),
        }


def http_stage(
    url: str,
    *,
    client: Any = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> StageOutcome:
    """Stage 1: honest HTTP, no browser-only headers, trafilatura extraction."""
    from .backends._http import new_client

    started = time.monotonic()
    owns = client is None
    http = client or new_client(timeout)
    try:
        outcome = _http_attempt(http, url, timeout=timeout)
        if outcome.block_class == "rate_limited":
            wait = _retry_after(outcome.http_headers)
            if wait is not None and wait <= RETRY_AFTER_CAP_SECONDS:
                sleep(wait)
                outcome = _http_attempt(http, url, timeout=timeout)
        elif outcome.block_class == "error" and (outcome.status or 0) >= 500:
            outcome = _http_attempt(http, url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - one URL never raises the batch
        outcome = StageOutcome(
            stage="http", block_class="error", url=url, error=f"http stage failed: {exc}"
        )
    finally:
        if owns:
            http.close()
    outcome.elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    return outcome


def _http_attempt(http: Any, url: str, *, timeout: float) -> StageOutcome:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": f"{host_locale()},en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        response = http.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return StageOutcome(stage="http", block_class="error", url=url, error=str(exc))

    body = response.content or b""
    content_type = response.headers.get("content-type", "")
    block_class = classify(response.status_code, response.headers, body, content_type)
    final_url = str(response.url)
    outcome = StageOutcome(
        stage="http",
        block_class=block_class,
        status=response.status_code,
        url=final_url,
        http_headers={key.lower(): value for key, value in response.headers.items()},
        cookies=cookie_list(response),
        signals=signals(response.headers, body),
    )
    if block_class == "ok":
        outcome.title = page_title(response.text or "")
        outcome.content = extract_text(body, final_url)
    elif block_class in ("challenge", "forbidden", "auth_required", "payment_required", "rate_limited"):
        outcome.error = f"HTTP {response.status_code} classified as {block_class}"
    return outcome


def chromium_stage(
    url: str,
    *,
    cookies: Sequence[Mapping[str, Any]] = (),
    timeout: float = CHROMIUM_TIMEOUT_SECONDS,
    locale: str | None = None,
    timezone_id: str | None = None,
) -> StageOutcome:
    """Stage 2: the real Chromium build, never the headless shell.

    ``headless=False`` plus ``--headless=new`` runs the full browser without a display;
    Playwright's own ``headless=True`` launches the shell, which has no ``window.chrome``,
    no plugins and a ``HeadlessChrome`` user agent (RESEARCH-blocks.md:25-28, 71-77).
    ``--disable-blink-features=AutomationControlled`` is what actually clears
    ``navigator.webdriver`` without a detectable JS patch.
    """
    started = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return StageOutcome(
            stage="chromium",
            block_class="error",
            url=url,
            skipped=True,
            reason="Playwright is not installed (run `hermes sluice install playwright`)",
        )

    browser_locale = locale or host_locale()
    zone = timezone_id or host_timezone()
    args = [
        "--headless=new",
        "--disable-blink-features=AutomationControlled",
        f"--lang={browser_locale}",
        "--window-size=1440,900",
    ]
    try:
        with sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=False, args=args)
            except Exception as exc:  # noqa: BLE001 - missing browser binary
                return StageOutcome(
                    stage="chromium",
                    block_class="error",
                    url=url,
                    skipped=True,
                    reason=(
                        "Chromium is not installed for Playwright "
                        f"({exc}); run `hermes sluice install playwright`"
                    ),
                )
            try:
                context = browser.new_context(
                    locale=browser_locale,
                    timezone_id=zone,
                    viewport={"width": 1440, "height": 900},
                )
                if cookies:
                    try:
                        context.add_cookies([dict(cookie) for cookie in cookies])
                    except Exception:  # noqa: BLE001 - a bad cookie must not kill the rung
                        pass
                page = context.new_page()
                try:
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=int(timeout * 1000)
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=3_000)
                    except Exception:  # noqa: BLE001 - busy pages never reach networkidle
                        pass
                    status = response.status if response is not None else 0
                    headers = response.all_headers() if response is not None else {}
                    html = page.content()
                    block_class = classify(
                        status, headers, html, headers.get("content-type", "text/html")
                    )
                    outcome = StageOutcome(
                        stage="chromium",
                        block_class=block_class,
                        status=status,
                        url=page.url,
                        title=page.title(),
                        http_headers={key.lower(): value for key, value in headers.items()},
                        signals=signals(headers, html.encode("utf-8", errors="replace")),
                    )
                    if block_class == "ok":
                        outcome.content = extract_text(html.encode("utf-8"), page.url)
                    outcome.elapsed_ms = round((time.monotonic() - started) * 1000, 1)
                    return outcome
                finally:
                    try:
                        context.close()
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001 - launching the runtime itself failed
        return StageOutcome(
            stage="chromium", block_class="error", url=url, error=f"chromium failed: {exc}"
        )


def camofox_stage(
    url: str,
    *,
    base_url: str = "http://localhost:9377",
    timeout: float = 30.0,
    user_id: str = "sluice",
    client: Any = None,
) -> StageOutcome:
    """Stage 3: the local Camofox REST server, only when it is healthy.

    Endpoints are the documented ones (``GET /health``, ``POST /tabs``,
    ``GET /tabs/{tabId}/snapshot``, ``DELETE /tabs/{tabId}``). The snapshot is the
    accessibility text the server exposes to agents — the same surface the camofox plugin's
    own browser tools read. A response without a text field is reported as a failure instead
    of being guessed at.
    """
    from .backends._http import new_client

    base = (base_url or "").rstrip("/")
    if not base:
        return StageOutcome(
            stage="camofox", block_class="error", url=url, skipped=True, reason="no Camofox URL"
        )
    owns = client is None
    http = client or new_client(timeout)
    try:
        health = http.get(f"{base}/health", timeout=2.0)
        try:
            healthy = health.status_code < 400 and bool((health.json() or {}).get("ok"))
        except ValueError:
            healthy = False
        if not healthy:
            return StageOutcome(
                stage="camofox",
                block_class="error",
                url=url,
                skipped=True,
                reason=f"Camofox server at {base} is not healthy",
            )
        created = http.post(
            f"{base}/tabs",
            json={"userId": user_id, "sessionKey": "sluice-extract", "url": url},
            timeout=timeout,
        )
        if created.status_code >= 400:
            return StageOutcome(
                stage="camofox",
                block_class="error",
                url=url,
                error=f"Camofox could not open a tab: HTTP {created.status_code}",
            )
        tab_id = _first_str(created.json() or {}, "tabId", "tab_id", "id", "targetId")
        if not tab_id:
            return StageOutcome(
                stage="camofox",
                block_class="error",
                url=url,
                error="Camofox did not return a tab id; cannot read the page",
            )
        try:
            snapshot = http.get(
                f"{base}/tabs/{tab_id}/snapshot", params={"userId": user_id}, timeout=timeout
            )
            if snapshot.status_code >= 400:
                return StageOutcome(
                    stage="camofox",
                    block_class="error",
                    url=url,
                    error=f"Camofox snapshot failed: HTTP {snapshot.status_code}",
                )
            data = snapshot.json() or {}
            text = _first_str(data, "snapshot", "content", "text", "data", "markdown")
            if not text:
                return StageOutcome(
                    stage="camofox",
                    block_class="error",
                    url=url,
                    error="Camofox returned a snapshot with no text field",
                )
            block_class = classify(200, {"content-type": "text/plain"}, text, "text/plain")
            return StageOutcome(
                stage="camofox",
                block_class=block_class,
                status=200,
                url=url,
                title=_first_str(data, "title"),
                content=text if block_class == "ok" else "",
            )
        finally:
            try:
                http.request(
                    "DELETE", f"{base}/tabs/{tab_id}", json={"userId": user_id}, timeout=5.0
                )
            except Exception:  # noqa: BLE001 - closing a tab is best effort
                pass
    except Exception as exc:  # noqa: BLE001
        return StageOutcome(
            stage="camofox",
            block_class="error",
            url=url,
            skipped=True,
            reason=f"Camofox server unavailable: {exc}",
        )
    finally:
        if owns:
            http.close()


# ── the ladder ───────────────────────────────────────────────────────────────────


def fetch(
    url: str,
    *,
    store: Store | None = None,
    settings: Mapping[str, Any] | None = None,
    http_client: Any = None,
    chromium: Callable[..., StageOutcome] | None = None,
    camofox: Callable[..., StageOutcome] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one URL through the ladder. Always returns a per-URL item, never raises."""
    settings = dict(settings or {})
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _item(
            url=url,
            stage="",
            block_class="error",
            error=f"not an http(s) URL: {url!r}",
            stages=[],
            blocked_by_policy=False,
        )

    client = http_client
    owns_client = False
    if client is None:
        from .backends._http import new_client

        client = new_client(HTTP_TIMEOUT_SECONDS)
        owns_client = True

    stages: list[dict[str, Any]] = []
    domain = parsed.netloc.lower()
    try:
        allowed, rule = robots_allowed(url, store=store, client=client)
        if not allowed:
            message = f"robots.txt disallows {parsed.path or '/'} for Sluice ({rule})"
            if store is not None:
                store.domain_memory_put(domain, "policy_blocked", None, signals=[rule])
            return _item(
                url=url,
                stage="policy",
                block_class="policy_blocked",
                error=message,
                stages=[],
                blocked_by_policy=True,
                content=message,
            )

        start = _start_stage(store, domain)
        cookies: list[dict[str, Any]] = []
        last_real: StageOutcome | None = None
        last_any: StageOutcome | None = None

        for index in range(start, len(STAGES)):
            stage = STAGES[index]
            started = time.monotonic()
            if stage == "http":
                outcome = http_stage(url, client=client, sleep=sleep)
            elif stage == "chromium":
                outcome = (chromium or chromium_stage)(url, cookies=cookies)
            else:
                outcome = (camofox or camofox_stage)(
                    url, base_url=str(settings.get("camofox_url") or "http://localhost:9377")
                )
            outcome.elapsed_ms = outcome.elapsed_ms or round((time.monotonic() - started) * 1000, 1)
            stages.append(outcome.report())
            last_any = outcome

            if outcome.cookies:
                cookies = outcome.cookies
            if outcome.skipped:
                continue
            last_real = outcome

            # A policy header on the response we just read stops the ladder even when the
            # body itself is usable: X-Robots-Tag and Content-Signal are the site's answer.
            policy_rule = response_policy_block(outcome.http_headers)
            if policy_rule:
                if store is not None:
                    store.domain_memory_put(domain, "policy_blocked", None, signals=[policy_rule])
                message = f"{policy_rule} disallows this URL for Sluice"
                return _item(
                    url=url,
                    stage=outcome.stage,
                    block_class="policy_blocked",
                    error=message,
                    content=message,
                    stages=stages,
                    blocked_by_policy=True,
                    status=outcome.status,
                )

            if outcome.block_class == "ok":
                if store is not None:
                    store.domain_memory_put(
                        domain,
                        _first_class(stages),
                        outcome.stage,
                        signals=outcome.signals,
                    )
                return _item(
                    url=outcome.url or url,
                    stage=outcome.stage,
                    block_class="ok",
                    title=outcome.title,
                    content=outcome.content,
                    stages=stages,
                    blocked_by_policy=False,
                    status=outcome.status,
                    cookies=cookies,
                )

            # Stop classes end the ladder; a second browser rung cannot fix a refusal.
            # rate_limited already spent its single Retry-After wait in stage 1.
            if outcome.block_class in STOP_CLASSES or outcome.block_class == "rate_limited":
                break

        final = last_real or last_any
        final_class = final.block_class if final is not None else "error"
        if final is not None and final.skipped:
            final_class = "error"
        if store is not None:
            store.domain_memory_put(
                domain, final_class, None, signals=(final.signals if final else [])
            )
        return _item(
            url=url,
            stage=final.stage if final is not None else "",
            block_class=final_class,
            error=_failure_text(url, final),
            stages=stages,
            blocked_by_policy=False,
            status=final.status if final is not None else None,
            title=final.title if final is not None else "",
        )
    finally:
        if owns_client:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


def fetch_many(
    urls: Iterable[str],
    *,
    store: Store | None = None,
    settings: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Per-URL isolation: one bad URL never fails the batch (DESIGN.md:195)."""
    return [fetch(str(url), store=store, settings=settings, **kwargs) for url in urls]


def _start_stage(store: Store | None, domain: str) -> int:
    """Start at the rung that worked before, or at the browser after a challenge (PLAN.md:99-104)."""
    if store is None:
        return 0
    memory = store.domain_memory_get(domain)
    if not memory:
        return 0
    worked = memory.get("stage_that_worked")
    if worked in STAGES:
        return STAGES.index(worked)
    if memory.get("block_class") in CLIMB_CLASSES:
        return STAGES.index("chromium")
    return 0


def _first_class(stages: list[dict[str, Any]]) -> str:
    for stage in stages:
        if not stage.get("skipped") and stage.get("block_class") != "ok":
            return str(stage.get("block_class"))
    return "ok"


def _failure_text(url: str, last: StageOutcome | None) -> str:
    if last is None:
        return f"Sluice could not fetch {url}: no stage produced a response"
    reason = last.error or last.reason or f"final stage {last.stage} reported {last.block_class}"
    return f"{reason} (block class: {last.block_class}, stage: {last.stage})"


def _item(
    *,
    url: str,
    stage: str,
    block_class: str,
    stages: list[dict[str, Any]],
    blocked_by_policy: bool,
    title: str = "",
    content: str = "",
    error: str | None = None,
    status: int | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One extract item. The provenance line is the only thing that survives trimming."""
    body = (content or "").strip()
    header = PROVENANCE_PREFIX.format(stage=stage or "none", block_class=block_class)
    rendered = f"{header}\n\n{body}" if body else header
    item: dict[str, Any] = {
        "url": url,
        "title": title,
        "content": rendered,
        "raw_content": body,
        "block_class": block_class,
        "stage": stage,
        "stages": stages,
        "blocked_by_policy": blocked_by_policy,
        "status": status,
    }
    if error:
        item["error"] = error
    if cookies:
        item["cookies"] = cookies
    return item


def cookie_list(response: Any) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    try:
        jar = response.cookies.jar
    except Exception:  # noqa: BLE001
        return cookies
    for cookie in jar:
        name = getattr(cookie, "name", "")
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": getattr(cookie, "value", ""),
                "domain": getattr(cookie, "domain", "") or urlparse(str(response.url)).netloc,
                "path": getattr(cookie, "path", "") or "/",
            }
        )
    return cookies


def _retry_after(headers: Mapping[str, str]) -> float | None:
    from .backends._http import retry_after_seconds

    return retry_after_seconds(headers)


def signals(headers: Mapping[str, str], body: bytes | str) -> list[str]:
    """Short, human-readable markers for the pane's per-domain block report."""
    head = _lower_headers(headers)
    found: list[str] = []
    for key in (
        "cf-mitigated",
        "x-datadome",
        "x-iinfo",
        "sec-cpt",
        "akamai-grn",
        "x-amzn-waf-action",
        "x-kpsdk-ct",
        "content-signal",
        "x-robots-tag",
    ):
        if head.get(key):
            found.append(f"{key}: {head[key][:60]}")
    cookie_header = head.get("set-cookie", "")
    for token in _COOKIE_MARKERS:
        if token in cookie_header:
            found.append(f"cookie {token}")
    return found


def page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
    return unescape(match.group(1)).strip() if match else ""


def extract_text(body: bytes | str, url: str = "") -> str:
    """trafilatura first; a clean-text fallback for minimal pages it refuses.

    Returns "" when there is nothing readable, which the ladder treats as a shell page and
    climbs from.
    """
    html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
    if not html.strip():
        return ""
    try:
        from trafilatura import extract as trafilatura_extract

        text = trafilatura_extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception:  # noqa: BLE001 - extraction is best effort
        text = None
    if text and text.strip():
        return text.strip()
    return clean_html(html)


def clean_html(html: str) -> str:
    """Strip tags and scripts, keep block boundaries, collapse whitespace.

    The fallback for pages trafilatura rejects (a one-paragraph status page, a listing); it
    is deliberately dumb, and it never returns markup.
    """
    text = re.sub(
        r"<(script|style|noscript|template)\b.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"</(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _first_str(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def host_locale() -> str:
    import locale

    try:
        language, _encoding = locale.getlocale()
    except Exception:  # noqa: BLE001
        language = None
    return (language or "en-US").replace("_", "-")


def host_timezone() -> str:
    """IANA zone id from /etc/localtime when it is a zoneinfo symlink, else UTC."""
    import os

    marker = "zoneinfo/"
    path = os.path.realpath("/etc/localtime") if os.path.exists("/etc/localtime") else ""
    if marker in path:
        return path.split(marker, 1)[1]
    return os.environ.get("TZ") or "UTC"
