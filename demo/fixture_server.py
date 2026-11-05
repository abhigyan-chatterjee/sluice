"""Deterministic local targets for the Sluice demo and the ladder tests.

PLAN.md:35-39 / Closed gap 4 fixes the cases:

================  ==================================================================
``/ok``           plain HTML with real prose: classifies ``ok`` at the HTTP rung
``/js``           a shell whose content is injected by script: ``js_required``
``/cf``           403 plus ``cf-mitigated: challenge``: Cloudflare ``challenge``
``/imperva``      200 with an 83-byte body and ``x-iinfo``: Imperva ``challenge``
``/ratelimit``    429 with ``Retry-After: 2``: ``rate_limited``
``/ratelimit-once`` 429 once, then 200: proves the single Retry-After retry
``/private``      robots.txt disallows it: ``policy_blocked`` without a request
``/private/ok``   allowed by a longer ``Allow`` rule: ``ok``
================  ==================================================================

The challenge beat in the demo is recorded against this server, so the video never depends
on a live vendor behaving (PLAN.md:38-39).

Run it::

    python demo/fixture_server.py --port 8765
    curl -i http://127.0.0.1:8765/cf
"""

from __future__ import annotations

import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator
from contextlib import contextmanager

ROBOTS_TXT = """# Fixture robots file for the Sluice demo.
User-agent: *
Disallow: /private
Allow: /private/ok
"""

OK_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sluice fixture: OK</title></head>
<body>
<article>
<h1>Sluice fixture: plain HTML</h1>
<p>This page is served as plain HTML with no JavaScript at all. The honest HTTP rung can
read it, trafilatura extracts the article text, and the ladder stops at the first stage.
Nothing here is hidden behind a challenge, a script or a rate limit.</p>
<p>The paragraph exists so the document is comfortably larger than the shell threshold the
classifier uses. A real article has a title, a byline, several paragraphs and a date; this
stand-in has the title and two paragraphs, which is enough for the extractor to return
text and for the provenance line to say stage=http.</p>
</article>
</body></html>
"""

JS_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sluice fixture: JS shell</title>
<script>window.__STATE__={ready:false};</script></head>
<body><div id="app"></div>
<script>
document.getElementById('app').innerHTML =
  '<article><h1>Loaded by script</h1><p>This sentence only exists after JavaScript runs.</p></article>';
</script>
</body></html>
"""

CF_BODY = """<!doctype html>
<html><head><title>Just a moment...</title></head>
<body><h1>Just a moment...</h1><p>Checking your browser before accessing the site.</p>
<p>Enable JavaScript and cookies to continue.</p></body></html>
"""

#: Imperva's incident page is famously 83 bytes; the classifier keys on the tiny body plus
#: ``x-iinfo`` (RESEARCH-blocks.md:58). The same bytes live in
#: ``tests/fixtures/blocks/imperva.html``.
IMPERVA_BODY = "<html><body>Request unsuccessful. Incapsula incident ID: 123456789012</body></html>"


class FixtureHandler(BaseHTTPRequestHandler):
    """One deterministic response per path; no timestamps, no randomness."""

    server_version = "sluice-fixture/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # keep pytest output clean
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            self._send(200, ROBOTS_TXT, "text/plain", {})
        elif path == "/ok":
            self._send(200, OK_HTML, "text/html", {})
        elif path == "/js":
            self._send(200, JS_SHELL, "text/html", {})
        elif path == "/cf":
            self._send(
                403,
                CF_BODY,
                "text/html",
                {"cf-mitigated": "challenge", "server": "cloudflare"},
            )
        elif path == "/imperva":
            self._send(200, IMPERVA_BODY, "text/html", {"x-iinfo": "7-12345678-12345"})
        elif path == "/ratelimit":
            self._send(429, "slow down\n", "text/plain", {"Retry-After": "2"})
        elif path == "/ratelimit-once":
            self._ratelimit_once()
        elif path == "/private":
            self._send(403, "private\n", "text/plain", {})
        elif path == "/private/ok":
            self._send(200, OK_HTML.replace("plain HTML", "allowed private path"), "text/html", {})
        elif path == "/":
            self._send(
                200,
                "<html><body><h1>Sluice fixtures</h1>"
                "<ul><li>/ok</li><li>/js</li><li>/cf</li><li>/imperva</li>"
                "<li>/ratelimit</li><li>/ratelimit-once</li><li>/private</li>"
                "<li>/private/ok</li></ul></body></html>",
                "text/html",
                {},
            )
        else:
            self._send(404, "not found\n", "text/plain", {})

    def _ratelimit_once(self) -> None:
        counter = getattr(self.server, "ratelimit_once_hits", 0)
        self.server.ratelimit_once_hits = counter + 1
        if counter == 0:
            self._send(429, "slow down once\n", "text/plain", {"Retry-After": "0"})
        else:
            self._send(200, OK_HTML, "text/html", {})

    def _send(self, status: int, body: str, content_type: str, headers: dict[str, str]) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)


def make_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create the fixture server. Port 0 asks the OS for a free port."""
    server = ThreadingHTTPServer((host, port), FixtureHandler)
    server.daemon_threads = True
    return server


@contextmanager
def running_server(host: str = "127.0.0.1", port: int = 0) -> Iterator[str]:
    """``with running_server() as base_url`` — starts on a thread and always shuts down."""
    server = make_server(host, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sluice-fixture",
        description="Deterministic HTTP targets for the Sluice demo and ladder tests.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    server = make_server(args.host, args.port)
    base = f"http://{args.host}:{server.server_address[1]}"
    print(f"sluice fixtures on {base}")
    for path in (
        "/ok",
        "/js",
        "/cf",
        "/imperva",
        "/ratelimit",
        "/ratelimit-once",
        "/private",
        "/private/ok",
        "/robots.txt",
    ):
        print(f"  {base}{path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
