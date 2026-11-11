"""T8 manager tests: detection, install plans/jobs and the config route.

Every external fact is injected (``which``, ``http_get``, ``runner``) so the suite never
touches the network, writes Hermes' config, installs a browser or starts a container.
The one contract that must survive a process restart — a job record — is proved by
closing and re-opening the store on the same file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sluice import manage
from sluice.manage import (
    CAMOFOX_CONTAINER,
    CAMOFOX_IMAGE,
    CAMOFOX_NPM_PACKAGE,
    CONFIG_ROUTES,
    CONFIG_SET_TIMEOUT_SECONDS,
    Detection,
    clear_outer_web_cache,
    config_command,
    detect,
    hermes_web_cache_dir,
    install_plan,
    missing_steps,
    render_detection,
    run_config_set,
    setup_routes,
    start_install_job,
    stop_camofox,
)
from sluice.store import Store


def _which(*names: str):
    """A ``shutil.which`` stand-in that finds exactly *names*."""
    found = set(names)

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in found else None

    return which


def _http(ok: bool):
    calls: list[str] = []

    def http_get(url: str, timeout: float):
        calls.append(url)
        return {"ok": ok} if ok else None

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _settings(**overrides):
    from sluice.config import DEFAULTS

    base = {
        **DEFAULTS,
        "camofox_url": "http://localhost:9377",
        "BROWSERBASE_API_KEY": "",
        "SEARXNG_URL": "",
    }
    base.update(overrides)
    return base


# ── detection ────────────────────────────────────────────────────────────────────


def test_detect_finds_nothing_on_a_bare_machine(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(manage.Path, "home", staticmethod(lambda: tmp_path / "home"))
    detection = detect(settings=_settings(), home=tmp_path / "hermes", which=_which(), http_get=_http(False))
    assert detection.camofox_plugin is False
    assert detection.camofox_server is False
    assert detection.playwright_chromium is False
    assert detection.browserbase_key is False
    assert detection.packaged_chromium is False


def test_detect_finds_the_camofox_plugin_next_to_the_active_home(tmp_path):
    plugin = tmp_path / "hermes" / "plugins" / "camofox"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: camofox\n", encoding="utf-8")

    detection = detect(settings=_settings(), home=tmp_path / "hermes", which=_which(), http_get=_http(True))
    assert detection.camofox_plugin is True
    assert detection.camofox_server is True
    assert detection.details["camofox_plugin"] == str(plugin)
    assert detection.details["camofox_url"] == "http://localhost:9377"


def test_detect_reads_the_playwright_browser_cache(tmp_path, monkeypatch):
    browsers = tmp_path / "ms-playwright"
    (browsers / "chromium-1148").mkdir(parents=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

    detection = detect(settings=_settings(), home=tmp_path, which=_which("chromium"), http_get=_http(False))
    assert detection.playwright_chromium is True
    assert detection.details["playwright_chromium"].endswith("chromium-1148")
    assert detection.packaged_chromium is True
    assert detection.details["packaged_chromium"] == "/usr/bin/chromium"


def test_detect_ignores_the_headless_shell_only(tmp_path, monkeypatch):
    """The ladder must not count the old headless shell as the real Chromium build."""
    browsers = tmp_path / "ms-playwright"
    (browsers / "chromium_headless_shell-1148").mkdir(parents=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

    detection = detect(settings=_settings(), home=tmp_path, which=_which(), http_get=_http(False))
    assert detection.playwright_chromium is False


def test_detect_reports_the_browserbase_key_without_revealing_it(tmp_path):
    detection = detect(
        settings=_settings(BROWSERBASE_API_KEY="sk-live-secret"),
        home=tmp_path,
        which=_which(),
        http_get=_http(False),
    )
    assert detection.browserbase_key is True
    assert "sk-live-secret" not in render_detection(detection)


def test_camofox_health_requires_ok_true(tmp_path):
    assert manage.camofox_health("http://x", http_get=lambda url, timeout: {"ok": True}) is True
    assert manage.camofox_health("http://x", http_get=lambda url, timeout: {"ok": False}) is False
    assert manage.camofox_health("http://x", http_get=lambda url, timeout: None) is False
    assert manage.camofox_health("http://x", http_get=lambda url, timeout: {"status": "up"}) is False


def test_missing_steps_name_the_exact_next_command(tmp_path):
    detection = Detection(
        camofox_plugin=False,
        camofox_server=False,
        playwright_chromium=False,
        browserbase_key=False,
        packaged_chromium=False,
    )
    steps = {step["name"]: step for step in missing_steps(detection, settings=_settings())}
    assert steps["camofox plugin"]["next"] == "hermes plugins install camofox"
    assert steps["camofox server"]["next"] == "hermes sluice-search install camofox"
    assert steps["playwright chromium"]["next"] == "hermes sluice-search install playwright"
    assert "Settings" in steps["browserbase key"]["next"]


def test_missing_steps_are_empty_when_everything_is_present():
    detection = Detection(True, True, True, True, True)
    assert missing_steps(detection, settings=_settings()) == []


def test_render_detection_is_a_honest_summary():
    text = render_detection(Detection(True, False, None, False, True))
    assert "camofox plugin:       yes" in text
    assert "camofox server:       no" in text
    assert "playwright chromium:  unknown" in text
    assert "packaged chromium:    yes" in text


# ── config route ────────────────────────────────────────────────────────────────


def test_config_routes_cover_both_backends_and_the_outer_cache():
    assert CONFIG_ROUTES == {
        "web.search_backend": "sluice-search",
        "web.extract_backend": "sluice-search",
        "web.cache_enabled": "false",
    }
    assert config_command("web.cache_enabled") == ["hermes", "config", "set", "web.cache_enabled", "false"]
    with pytest.raises(KeyError):
        config_command("web.enabled")


def test_run_config_set_prints_without_applying():
    result = run_config_set("web.search_backend")
    assert result["applied"] is False
    assert result["ok"] is None
    assert result["command"] == ["hermes", "config", "set", "web.search_backend", "sluice-search"]


def test_run_config_set_runs_only_when_asked():
    seen: list[list[str]] = []

    def runner(command, *, timeout):
        seen.append(list(command))
        assert timeout == CONFIG_SET_TIMEOUT_SECONDS
        return 0, "ok", ""

    result = run_config_set("web.extract_backend", apply=True, runner=runner)
    assert seen == [["hermes", "config", "set", "web.extract_backend", "sluice-search"]]
    assert result["applied"] is True and result["ok"] is True
    assert result["returncode"] == 0


def test_run_config_set_surfaces_a_failure_without_retrying():
    calls: list[list[str]] = []

    def runner(command, *, timeout):
        calls.append(list(command))
        return 1, "", "profile is managed"

    result = run_config_set("web.search_backend", apply=True, runner=runner)
    assert len(calls) == 1
    assert result["ok"] is False
    assert "profile is managed" in result["error"]


def test_setup_routes_keep_the_design_order():
    def runner(command, *, timeout):
        return 0, "", ""

    results = setup_routes(apply=True, runner=runner)
    assert [item["key"] for item in results] == [
        "web.search_backend",
        "web.extract_backend",
        "web.cache_enabled",
    ]
    assert all(item["ok"] for item in results)


# ── install plans ───────────────────────────────────────────────────────────────


def test_playwright_plan_uses_the_running_interpreter():
    plan = install_plan("playwright", which=_which())
    assert plan["supported"] is True
    assert plan["commands"][0][1:] == ["-m", "playwright", "install", "chromium"]
    assert plan["next"] == "hermes sluice-search install camofox"


def test_camofox_prefers_podman_then_docker_then_npm(tmp_path, monkeypatch):
    monkeypatch.delenv("SLUICE_CAMOFOX_DIR", raising=False)
    podman = install_plan("camofox", which=_which("podman", "docker", "npm"))
    assert podman["commands"][0][0] == "/usr/bin/podman"
    assert CAMOFOX_CONTAINER in podman["commands"][0]
    assert CAMOFOX_IMAGE in podman["commands"][0]

    docker = install_plan("camofox", which=_which("docker", "npm"))
    assert docker["commands"][0][0] == "/usr/bin/docker"

    npm = install_plan("camofox", which=_which("npm"), home=tmp_path)
    command = npm["commands"][0]
    assert command[:4] == ["/usr/bin/npm", "install", "--prefix", str(tmp_path / "plugin-data" / "sluice-search" / "camofox")]
    assert command[4] == CAMOFOX_NPM_PACKAGE
    assert npm["next"] == f"npx --yes {CAMOFOX_NPM_PACKAGE}"


def test_camofox_without_a_runtime_or_npm_is_unsupported(tmp_path):
    plan = install_plan("camofox", which=_which(), home=tmp_path)
    assert plan["supported"] is False
    assert plan["commands"] == []
    assert "podman" in plan["next"] and "hermes sluice-search install camofox" in plan["next"]


def test_browserbase_is_a_key_route_not_an_install():
    plan = install_plan("browserbase", which=_which())
    assert plan["supported"] is False
    assert plan["commands"] == []
    assert plan["next"] == "hermes config set browser.cloud_provider browserbase"
    assert "BROWSERBASE_API_KEY" in plan["note"]


def test_unknown_install_target_is_named():
    with pytest.raises(KeyError, match="unknown install target"):
        install_plan("chrome")


# ── install jobs ────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "sluice.db")
    try:
        yield s
    finally:
        s.close()


def test_install_job_runs_inline_and_records_output(store):
    def runner(command):
        return 0, "downloaded chromium"

    result = start_install_job(
        store, "playwright", wait=True, runner=runner, which=_which(), home=store.path.parent
    )
    assert result["started"] is True
    job = result["job"]
    assert job["status"] == "succeeded"
    assert "downloaded chromium" in job["output"]
    assert job["started_at"] and job["finished_at"]
    assert "playwright install chromium" in job["command"]


def test_install_job_records_a_failure(store):
    def runner(command):
        return 1, "no network"

    result = start_install_job(
        store, "playwright", wait=True, runner=runner, which=_which(), home=store.path.parent
    )
    job = result["job"]
    assert job["status"] == "failed"
    assert "exited 1" in job["error"]


def test_install_job_record_survives_a_restart(tmp_path):
    path = tmp_path / "sluice.db"
    first = Store(path)
    result = start_install_job(
        first, "playwright", wait=True, runner=lambda command: (0, "ok"), which=_which(), home=tmp_path
    )
    job_id = result["job"]["id"]
    first.close()

    second = Store(path)
    try:
        job = second.get_job(job_id)
        assert job is not None and job["status"] == "succeeded"
    finally:
        second.close()


def test_a_job_left_running_by_a_dead_process_is_failed_on_reopen(tmp_path):
    path = tmp_path / "sluice.db"
    first = Store(path)
    job_id = first.create_job("install", "playwright", command="playwright install chromium", status="running")
    first.close()

    second = Store(path)
    try:
        assert second.recover_stale_jobs() == 1
        job = second.get_job(job_id)
        assert job["status"] == "failed"
        assert "interrupted" in job["error"]
    finally:
        second.close()


def test_install_job_for_browserbase_creates_no_job(store):
    result = start_install_job(
        store, "browserbase", wait=True, runner=lambda command: (0, ""), which=_which(), home=store.path.parent
    )
    assert result["started"] is False
    assert result["job"] is None
    assert store.list_jobs() == []


def test_install_job_runs_in_a_thread_when_not_waiting(store):
    result = start_install_job(
        store,
        "playwright",
        runner=lambda command: (0, "ok"),
        which=_which(),
        home=store.path.parent,
    )
    assert result["started"] is True
    job_id = result["job"]["id"]
    # The row exists before the work does: status is queued or already finished.
    assert store.get_job(job_id)["status"] in {"queued", "running", "succeeded"}


def test_stop_camofox_names_the_container(store):
    seen: list[list[str]] = []

    def runner(command):
        seen.append(list(command))
        return 0, "stopped"

    result = stop_camofox(which=_which("podman"), runner=runner)
    assert result["stopped"] is True
    assert seen == [["/usr/bin/podman", "stop", CAMOFOX_CONTAINER]]


def test_stop_camofox_without_a_runtime_says_so():
    result = stop_camofox(which=_which(), runner=lambda command: (0, ""))
    assert result["stopped"] is False
    assert "no container runtime" in result["error"]


# ── outer cache ─────────────────────────────────────────────────────────────────


def test_clear_outer_web_cache_deletes_files_and_counts_bytes(tmp_path):
    target = hermes_web_cache_dir(tmp_path)
    (target / "nested").mkdir(parents=True)
    (target / "a.json").write_text("0123456789", encoding="utf-8")
    (target / "nested" / "b.txt").write_text("abc", encoding="utf-8")

    result = clear_outer_web_cache(tmp_path)
    assert result["existed"] is True
    assert result["removed"] == 2
    assert result["bytes"] == 13
    assert not (target / "a.json").exists()
    assert not (target / "nested" / "b.txt").exists()


def test_clear_outer_web_cache_on_a_missing_dir_is_a_noop(tmp_path):
    result = clear_outer_web_cache(tmp_path / "absent")
    assert result == {"path": str(hermes_web_cache_dir(tmp_path / "absent")), "removed": 0, "bytes": 0, "existed": False}


def test_hermes_home_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    assert manage.hermes_home() == Path(tmp_path / "profile")
    monkeypatch.delenv("HERMES_HOME")
    monkeypatch.setattr(manage.Path, "home", staticmethod(lambda: tmp_path / "home"))
    assert manage.hermes_home() == tmp_path / "home" / ".hermes"
