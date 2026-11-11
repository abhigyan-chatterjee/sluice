"""Surface contracts for the files a clean clone must contain (T10, T11, T12, T14).

Each test here pins a claim a manifest, a document or a demo makes about the tree: an entry
file the manifest declares, the hosts the README discloses, the imports the Desktop renderer
can resolve, the square brackets a SearXNG JSON API needs. They are text-level checks, plus
one loopback HTTP check for the demo fixtures; nothing reaches a vendor.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from sluice.config import SECRET_ENVS
from sluice.ladder import BLOCK_CLASSES, STAGES

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = "desktop/plugin.js"
SKILL = "skills/sluice-search/SKILL.md"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )


def _is_git_checkout() -> bool:
    result = _git("rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


# ── T10: dashboard manifest points at real, tracked files ──────────────────────


def test_dashboard_manifest_points_at_existing_tracked_files():
    """``dashboard/manifest.json`` declares an entry bundle and an API module.

    Both must exist, and both must be tracked by git: an entry file that is present only
    because a build ran locally is missing on a clean clone, which is exactly the bug this
    pins.
    """
    manifest = json.loads(_read("dashboard/manifest.json"))
    assert manifest["entry"] == "dist/index.js"
    assert manifest["api"] == "plugin_api.py"

    declared = {key: ROOT / "dashboard" / manifest[key] for key in ("entry", "api")}
    for key, path in declared.items():
        assert path.is_file(), f"dashboard/manifest.json {key}={path.name} is not on disk"

    if not _is_git_checkout():
        pytest.skip("not a git checkout: tracking cannot be verified")

    for key, path in declared.items():
        relative = path.relative_to(ROOT).as_posix()
        tracked = _git("ls-files", "--error-unmatch", relative)
        assert tracked.returncode == 0, (
            f"dashboard/manifest.json {key}={relative} is not tracked by git, "
            "so a clean clone has no such file"
        )


def test_no_declared_dashboard_file_is_gitignored():
    """.gitignore must not exclude a path the dashboard manifest declares."""
    if not _is_git_checkout():
        pytest.skip("not a git checkout: ignore rules cannot be evaluated")

    manifest = json.loads(_read("dashboard/manifest.json"))
    for key in ("entry", "api"):
        relative = f"dashboard/{manifest[key]}"
        ignored = _git("check-ignore", "--quiet", relative)
        assert ignored.returncode != 0, f"{relative} is declared but gitignored"


def test_dashboard_entry_bundle_registers_with_the_dashboard_loader():
    """The entry must call the loader hook, or the dashboard reports NO_REGISTER."""
    source = _read("dashboard/dist/index.js")
    assert "__HERMES_PLUGINS__" in source
    assert "registry.register('sluice-search'" in source


# ── T11: the desktop pane (Closed gap 3) ───────────────────────────────────────

_ALLOWED_ESM = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}


def _esm_specifiers(source: str) -> set[str]:
    from_imports = set(re.findall(r"""\bfrom\s+['"]([^'"]+)['"]""", source))
    bare_imports = set(re.findall(r"""(?m)^\s*import\s+['"]([^'"]+)['"]""", source))
    return from_imports | bare_imports


def test_desktop_pane_imports_only_the_sdk_and_react():
    """REQUIREMENTS.md §4.5: plain ESM, three importable specifiers, no build step."""
    source = _read(DESKTOP)
    assert _esm_specifiers(source) == _ALLOWED_ESM
    assert "import(" not in source
    assert "require(" not in source


def test_desktop_pane_is_a_right_side_380px_pane():
    source = _read(DESKTOP)
    assert re.search(r"\barea:\s*PANES_AREA\b", source)
    assert re.search(r"placement:\s*'right'", source)
    assert re.search(r"width:\s*'380px'", source)


def test_desktop_pane_registers_a_settings_page_and_three_palette_commands():
    source = _read(DESKTOP)
    assert "ctx.registerSettingsPage(" in source
    assert re.search(r"\barea:\s*PALETTE_AREA\b", source)
    for command in ("sluice-search.probe", "sluice-search.test", "sluice-search.cache.clear"):
        assert f"'{command}'" in source, command


def test_desktop_pane_polls_status_every_five_seconds_while_visible():
    source = _read(DESKTOP)
    assert re.search(r"const POLL_MS = 5000\b", source)
    assert "ctx.rest('/status')" in source
    assert "document.visibilityState === 'hidden'" in source
    assert "visibilitychange" in source


def test_desktop_pane_table_has_the_seven_planned_columns():
    source = _read(DESKTOP)
    header = re.search(r"const header = \[([^\]]+)\]", source)
    assert header is not None
    assert re.findall(r"'([^']+)'", header.group(1)) == [
        "backend",
        "tier",
        "key",
        "health",
        "p50",
        "calls",
        "spend",
    ]


def test_desktop_pane_uses_no_eval_no_websockets_and_no_markup_queries():
    """PLAN.md:140 and REQUIREMENTS.md §4.5 — no evaluation, no sockets, no app markup."""
    source = _read(DESKTOP)
    # Inert literals: this tuple is searched for, never evaluated. They name the constructs
    # the pane must not contain.
    forbidden = (
        "eval(",
        "new Function(",
        "WebSocket",
        "EventSource",
        "document.querySelector",
        "document.querySelectorAll",
        "document.getElementById",
        "document.getElementsBy",
        ".innerHTML",
    )
    for token in forbidden:
        assert token not in source, token


def test_desktop_pane_carries_no_secret_values():
    source = _read(DESKTOP)
    for env in SECRET_ENVS:
        assert env not in source, env
    assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}", source)
    assert not re.search(r"""['"][A-Fa-f0-9]{32,}['"]""", source)


# ── T12: the skill and the README disclosures ──────────────────────────────────


def test_skill_frontmatter_names_the_skill():
    text = _read(SKILL)
    front = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert front is not None
    fields = front.group(1)
    assert re.search(r"^name: sluice-search$", fields, re.MULTILINE)
    assert re.search(r"^description: \S", fields, re.MULTILINE)
    assert re.search(r"^version: 1\.0\.0$", fields, re.MULTILINE)


def test_skill_documents_every_block_class():
    """Closed gap/T7 fixes nine classes; the skill explains all of them."""
    text = _read(SKILL)
    assert len(BLOCK_CLASSES) == 9
    for block_class in BLOCK_CLASSES:
        assert f"`{block_class}`" in text, block_class


def test_skill_documents_every_ladder_stage_and_the_stop_rules():
    text = _read(SKILL)
    lowered = text.lower()
    for stage in STAGES:
        assert stage in lowered, stage
    assert "robots.txt" in text
    assert "--headless=new" in text
    assert "camofox rest" in lowered
    assert "retry-after" in lowered


def test_skill_explains_how_to_read_hermes_sluice_search_doctor():
    text = _read(SKILL)
    assert "hermes sluice-search doctor" in text
    for column in ("tier", "key", "status"):
        assert f"**{column}**" in text, column


_HOST = re.compile(r"https://([a-z0-9][a-z0-9.-]*\.[a-z]{2,})")


def _documented_hosts() -> set[str]:
    """Every host in ``references/endpoints.md``, the file the adapters are built from."""
    return set(_HOST.findall(_read("references/endpoints.md")))


def test_readme_discloses_every_vendor_host():
    """PLAN.md:148 — the README lists every vendor. The hosts come from the reference doc."""
    hosts = _documented_hosts()
    assert len(hosts) == 12, sorted(hosts)
    readme = _read("README.md")
    missing = sorted(host for host in hosts if host not in readme)
    assert not missing, f"README does not disclose {missing}"


def test_readme_has_no_misspelled_vendor_host():
    readme = _read("README.md")
    assert "api.keenable.com" not in readme
    assert "api.keenable.ai" in readme


def test_readme_discloses_processes_commands_data_dir_and_credentials():
    readme = _read("README.md")
    for token in (
        "Playwright Chromium",
        "Camofox",
        "podman|docker run",
        "python -m playwright install chromium",
        "hermes config set web.search_backend sluice-search",
        "plugin data directory",
        "get_provider_env",
        "Settings ▸ Plugins",
    ):
        assert token in readme, token


# ── T14: the SearXNG reference and the deterministic demo fixtures ─────────────


def test_searxng_settings_enable_the_json_api():
    """Sluice talks to SearXNG's JSON API; the stock config disables it."""
    text = _read("docker/searxng/settings.yml")
    assert re.search(r"^search:\s*$", text, re.MULTILINE)
    search_block = text.split("search:", 1)[1]
    formats = re.search(r"formats:\s*\n((?:\s*-\s*\w+\s*\n)+)", search_block)
    assert formats is not None, "search.formats is missing from docker/searxng/settings.yml"
    assert re.findall(r"-\s*(\w+)", formats.group(1)) == ["html", "json"]


def test_searxng_compose_binds_localhost_and_mounts_the_settings():
    text = _read("docker/searxng/docker-compose.yml")
    assert "127.0.0.1:8080:8080" in text
    assert "./settings.yml:/etc/searxng/settings.yml:ro" in text


def test_demo_script_exists_and_opens_with_the_inventory_beat():
    text = _read("demo/script.md")
    assert len(text) > 500
    assert "hermes sluice-search doctor" in text
    assert "demo/fixture_server.py" in text


@pytest.fixture(scope="module")
def fixture_base() -> Iterator[str]:
    """The demo fixture server on a loopback port, torn down after the module."""
    from demo.fixture_server import running_server

    with running_server() as base:
        yield base


def _get(base: str, path: str):
    request = urllib.request.Request(
        base + path, headers={"User-Agent": "sluice-tests/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8", "replace")


@pytest.mark.parametrize(
    "path,status,marker",
    [
        ("/ok", 200, "plain HTML"),
        ("/js", 200, '<div id="app">'),
        ("/cf", 403, "Just a moment"),
        ("/imperva", 200, "Incapsula incident ID"),
        ("/ratelimit", 429, "slow down"),
        ("/robots.txt", 200, "Disallow: /private"),
    ],
)
def test_fixture_server_serves_every_closed_gap_4_route(fixture_base, path, status, marker):
    code, _headers, body = _get(fixture_base, path)
    assert code == status
    assert marker in body


def test_fixture_server_sends_the_block_signature_headers(fixture_base):
    _code, headers, _body = _get(fixture_base, "/cf")
    assert headers.get("cf-mitigated") == "challenge"

    _code, headers, body = _get(fixture_base, "/imperva")
    assert headers.get("x-iinfo")
    assert len(body.encode("utf-8")) < 200, "the classifier keys on Imperva's tiny body"

    _code, headers, _body = _get(fixture_base, "/ratelimit")
    assert headers.get("Retry-After") == "2"


def test_fixture_server_serves_the_robots_policy_rules(fixture_base):
    _code, _headers, body = _get(fixture_base, "/robots.txt")
    assert "Disallow: /private" in body
    assert "Allow: /private/ok" in body
    assert _get(fixture_base, "/private/ok")[0] == 200
