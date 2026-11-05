"""T9 CLI tests: every command runs unattended, exits honestly and prints a next step.

Nothing here reaches the network or writes Hermes' config: the store points at a temp
directory, settings are injected, and the config/install seams are replaced with recorders.
"""

from __future__ import annotations

import pytest

from sluice import cli
from sluice.config import DEFAULTS, SECRET_ENVS
from sluice.manage import CONFIG_ROUTES, Detection
from sluice.store import NS_SEARCH_GENERAL, open_store


def _settings(**overrides):
    base = {**DEFAULTS, **{env: "" for env in SECRET_ENVS}}
    base.update(overrides)
    return base


def _detection() -> Detection:
    return Detection(
        camofox_plugin=True,
        camofox_server=True,
        playwright_chromium=True,
        browserbase_key=False,
        packaged_chromium=True,
        details={"camofox_url": "http://localhost:9377", "packaged_chromium": "/usr/bin/chromium"},
    )


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Temp data dir, temp Hermes home, injected settings and browser detection."""
    data = tmp_path / "data"
    monkeypatch.setenv("SLUICE_DATA_DIR", str(data))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    settings = _settings()
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(cli, "detect", lambda settings=None: _detection())
    return {"settings": settings, "data": data, "tmp": tmp_path}


def _run(argv):
    return cli.main(argv)


# ── doctor ──────────────────────────────────────────────────────────────────────


def test_doctor_lists_all_twelve_backends_with_tier_plan_and_key(isolated, capsys):
    assert _run(["doctor"]) == 0
    out = capsys.readouterr().out
    for vendor in (
        "searxng", "ddgs", "exa", "parallel", "firecrawl", "tavily",
        "keenable", "brave", "perplexity", "deepseek", "tinyfish", "xai",
    ):
        assert vendor in out
    assert "tier order:" in out
    assert "browser engines" in out
    # ddgs needs no key; every keyed vendor without one says why.
    assert "keyless" in out
    assert "missing:" in out
    assert "next: hermes sluice setup --apply" in out


def test_doctor_names_the_searxng_compose_command(isolated, capsys):
    assert _run(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "SEARXNG_URL" in out
    assert "hermes config set plugins.entries.sluice.settings.searxng_url" in out
    assert "docker compose -f docker/searxng/docker-compose.yml up -d" in out


def test_doctor_reports_a_ready_backend(isolated, capsys):
    isolated["settings"]["BRAVE_API_KEY"] = "key"
    assert _run(["doctor"]) == 0
    out = capsys.readouterr().out
    line = next(row for row in out.splitlines() if row.startswith("brave"))
    assert "ready" in line


# ── status ─────────────────────────────────────────────────────────────────────


def test_status_shows_persisted_state(isolated, capsys):
    store = open_store(isolated["data"], settings=isolated["settings"])
    store.record_success("exa", latency_ms=120.0)
    store.record_failure("brave", error="quota exhausted, upgrade your plan", quota=True)
    store.cache_put(NS_SEARCH_GENERAL, "k", {"rows": []}, 900)
    store.close()

    assert _run(["status"]) == 0
    out = capsys.readouterr().out
    assert "exa" in out and "ok" in out
    brave = next(row for row in out.splitlines() if row.startswith("brave"))
    assert "quota" in brave
    assert "cache: 1 live / 1 rows" in out
    assert "next: hermes sluice test" in out


# ── setup / use ────────────────────────────────────────────────────────────────


def test_setup_prints_the_three_routes_without_applying(isolated, capsys):
    assert _run(["setup"]) == 0
    out = capsys.readouterr().out
    for key, value in CONFIG_ROUTES.items():
        assert f"hermes config set {key} {value}" in out
    assert "dry run" in out


def test_setup_apply_reports_success(isolated, capsys, monkeypatch):
    seen: list[bool] = []

    def fake_setup(*, apply=False, runner=None):
        seen.append(apply)
        return [
            {"key": key, "value": value, "command": ["hermes", "config", "set", key, value],
             "applied": True, "ok": True, "returncode": 0, "output": "", "error": None}
            for key, value in CONFIG_ROUTES.items()
        ]

    monkeypatch.setattr(cli, "setup_routes", fake_setup)
    assert _run(["setup", "--apply"]) == 0
    assert seen == [True]
    out = capsys.readouterr().out
    assert "[ok]" in out
    assert "done:" in out


def test_setup_apply_fails_non_zero(isolated, capsys, monkeypatch):
    def fake_setup(*, apply=False, runner=None):
        return [
            {"key": "web.search_backend", "value": "sluice",
             "command": ["hermes", "config", "set", "web.search_backend", "sluice"],
             "applied": True, "ok": False, "returncode": 1, "output": "", "error": "locked"}
        ]

    monkeypatch.setattr(cli, "setup_routes", fake_setup)
    assert _run(["setup", "--apply"]) == 1
    assert "failed" in capsys.readouterr().err


def test_use_switches_the_named_key(isolated, capsys, monkeypatch):
    seen: list[tuple[str, bool]] = []

    def fake_run(key, *, apply=False, runner=None, timeout=10):
        seen.append((key, apply))
        return {"key": key, "value": "sluice", "command": ["hermes", "config", "set", key, "sluice"],
                "applied": apply, "ok": True if apply else None, "returncode": 0 if apply else None,
                "output": "", "error": None}

    monkeypatch.setattr(cli, "run_config_set", fake_run)
    assert _run(["use", "extract"]) == 0
    assert seen == [("web.extract_backend", False)]
    assert "hermes config set web.extract_backend sluice" in capsys.readouterr().out

    assert _run(["use", "search", "--apply"]) == 0
    assert seen[-1] == ("web.search_backend", True)


# ── test ───────────────────────────────────────────────────────────────────────


class _FakeBackend:
    def __init__(self, *, vendor="ddgs", ok=True, missing="", error="boom"):
        from sluice.backends import spec

        self.spec = spec(vendor)
        self.key = ""
        self.keyless_enabled = True
        self.keyless_capable = self.spec.keyless_capable
        self._ok = ok
        self._missing = missing
        self._error = error

    def missing_message(self):
        return self._missing

    def mode(self):
        return "keyless"

    def tier(self, plan="paid"):
        return "keyless"

    def search(self, query, limit=5, **kwargs):
        if self._ok:
            return {"ok": True, "results": [{"url": "https://example.com", "title": "Example", "description": "d"}]}
        return {"ok": False, "error": self._error}


def test_test_command_prints_rows_and_records_usage(isolated, capsys, monkeypatch):
    monkeypatch.setattr(cli, "make_backend", lambda vendor, settings: _FakeBackend())
    assert _run(["test", "--backend", "ddgs", "zstd vs gzip"]) == 0
    out = capsys.readouterr().out
    assert "ddgs (keyless, keyless) answered in" in out
    assert "https://example.com" in out

    store = open_store(isolated["data"], settings=isolated["settings"])
    assert store.backend_state("ddgs").calls == 1
    store.close()


def test_test_command_exits_non_zero_on_a_backend_failure(isolated, capsys, monkeypatch):
    monkeypatch.setattr(cli, "make_backend", lambda vendor, settings: _FakeBackend(ok=False, error="HTTP 429"))
    assert _run(["test", "--backend", "ddgs", "q"]) == 1
    captured = capsys.readouterr()
    assert "HTTP 429" in captured.err
    assert "next: hermes sluice status" in captured.err


def test_test_command_names_the_missing_key_and_next_command(isolated, capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "make_backend",
        lambda vendor, settings: _FakeBackend(vendor="brave", missing="BRAVE_API_KEY is not set"),
    )
    assert _run(["test", "--backend", "brave", "q"]) == 1
    captured = capsys.readouterr()
    assert "cannot run" in captured.err
    assert "Settings" in captured.err


def test_test_command_prints_a_refused_searxng_reason_without_a_traceback(isolated, capsys):
    """A value httpx's IDNA encoder refuses used to escape as a raw traceback."""
    from sluice.config import normalize_searxng_url

    url, error = normalize_searxng_url("http://ho\u200bst:8888")
    isolated["settings"]["searxng_url"] = url
    isolated["settings"]["searxng_url_error"] = error
    assert _run(["test", "--backend", "searxng", "q"]) == 1
    captured = capsys.readouterr()
    assert "cannot run" in captured.err
    assert "SEARXNG_URL" in captured.err
    assert "IDNA" in captured.err
    assert "next:" in captured.err
    assert "Traceback" not in captured.err


def test_unknown_backend_exits_two(isolated, capsys):
    assert _run(["test", "--backend", "nope", "q"]) == 2
    assert "unknown backend" in capsys.readouterr().err


# ── cache clear ────────────────────────────────────────────────────────────────


def test_cache_clear_empties_both_caches(isolated, capsys):
    store = open_store(isolated["data"], settings=isolated["settings"])
    store.cache_put(NS_SEARCH_GENERAL, "k", {"rows": []}, 900)
    store.close()
    outer = isolated["tmp"] / "hermes" / "cache" / "web"
    outer.mkdir(parents=True)
    (outer / "page.json").write_text("{}", encoding="utf-8")

    assert _run(["cache", "clear"]) == 0
    out = capsys.readouterr().out
    assert "deleted 1 row(s)" in out
    assert "deleted 1 file(s)" in out
    assert not (outer / "page.json").exists()

    store = open_store(isolated["data"], settings=isolated["settings"])
    assert store.cache_count() == 0
    store.close()


def test_cache_without_an_action_exits_two(isolated, capsys):
    assert _run(["cache"]) == 2
    assert "cache needs an action" in capsys.readouterr().err


# ── install ────────────────────────────────────────────────────────────────────


def test_install_reports_a_successful_job(isolated, capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "start_install_job",
        lambda store, target, wait=False: {
            "started": True,
            "target": target,
            "job": {"id": "abc123", "status": "succeeded", "command": "playwright install chromium",
                    "output": "downloaded", "error": None},
            "commands": [],
            "note": "",
            "next": "hermes sluice install camofox",
        },
    )
    assert _run(["install", "playwright"]) == 0
    out = capsys.readouterr().out
    assert "job abc123 [succeeded]" in out
    assert "done. next: hermes sluice install camofox" in out


def test_install_reports_a_failed_job_non_zero(isolated, capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "start_install_job",
        lambda store, target, wait=False: {
            "started": True,
            "target": target,
            "job": {"id": "abc123", "status": "failed", "command": "playwright install chromium",
                    "output": "", "error": "exited 1"},
            "commands": [],
            "note": "",
            "next": "hermes sluice doctor",
        },
    )
    assert _run(["install", "playwright"]) == 1
    assert "exited 1" in capsys.readouterr().err


def test_install_browserbase_prints_the_key_route(isolated, capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "start_install_job",
        lambda store, target, wait=False: {
            "started": False,
            "target": target,
            "job": None,
            "commands": [],
            "note": "nothing to install",
            "next": "hermes config set browser.cloud_provider browserbase",
        },
    )
    assert _run(["install", "browserbase"]) == 0
    out = capsys.readouterr().out
    assert "nothing to install" in out
    assert "hermes config set browser.cloud_provider browserbase" in out


# ── entry points ───────────────────────────────────────────────────────────────


def test_no_command_prints_help_and_exits_two(capsys):
    assert _run([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_handle_command_accepts_the_parser_from_hermes(capsys):
    assert cli.handle_command(cli.build_parser().parse_args(["doctor"])) == 0
    assert "Sluice" in capsys.readouterr().out
