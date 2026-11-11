"""T10 plugin API tests: the six routes, plus the two the pane needs.

The payload functions live in ``sluice.api`` and are exercised here without FastAPI; a
second block mounts the real router when FastAPI is installed (it is inside Hermes, not a
dependency of this package). Nothing hits the network: browser detection and routing are
injected, and the store points at a temp directory.
"""

from __future__ import annotations

import time

import pytest

from sluice import api
from sluice.config import DEFAULTS, SECRET_ENVS
from sluice.store import NS_SEARCH_GENERAL, open_store


def _settings(**overrides):
    base = {**DEFAULTS, **{env: "" for env in SECRET_ENVS}}
    base.update(overrides)
    return base


def _detection(**overrides):
    base = {
        "camofox_plugin": False,
        "camofox_server": False,
        "playwright_chromium": True,
        "browserbase_key": False,
        "packaged_chromium": True,
        "details": {"camofox_url": "http://localhost:9377"},
    }
    base.update(overrides)
    return base


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Temp data dir and Hermes home; the shared store is reset on the way out."""
    monkeypatch.setenv("SLUICE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    settings = _settings()
    # Hermes' own plugin-data resolver wins over SLUICE_DATA_DIR when Hermes is importable
    # (running these tests under the Hermes venv), so pin it and the settings loader.
    monkeypatch.setattr(api, "plugin_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(api, "load_settings", lambda ctx=None: dict(settings))
    api.reset()
    state = open_store(tmp_path / "data", settings=settings)
    yield {"settings": settings, "store": state, "tmp": tmp_path, "data": tmp_path / "data"}
    state.close()
    api.reset()


# ── status ─────────────────────────────────────────────────────────────────────


def test_status_lists_twelve_backends_with_health_and_plans(env):
    payload = api.status_payload(settings=env["settings"], state=env["store"], detection=_detection())
    assert payload["ok"] is True
    assert payload["plugin"] == "sluice"
    assert len(payload["backends"]) == 12
    ids = [item["id"] for item in payload["backends"]]
    assert ids[0] == "searxng" and ids[-1] == "xai"
    by_id = {item["id"]: item for item in payload["backends"]}
    assert by_id["ddgs"]["mode"] == "keyless"
    assert by_id["ddgs"]["key_present"] is False
    assert by_id["brave"]["health"] == "unconfigured"
    assert by_id["brave"]["key_env"] == "BRAVE_API_KEY"
    assert by_id["exa"]["plan"] == "free"
    assert by_id["deepseek"]["plan"] == "paid"
    assert payload["totals"]["backends"] == 12
    # No secret value leaks: only the env var name and a boolean.
    text = str(payload)
    for env_name in SECRET_ENVS:
        assert f"{env_name}=" not in text


def test_status_reports_circuit_and_quota(env):
    settings = _settings(BRAVE_API_KEY="k", EXA_API_KEY="k")
    for _ in range(3):
        env["store"].record_failure("brave", error="503", now=time.time())
    env["store"].record_failure("exa", error="credit spent", quota=True)
    payload = api.status_payload(settings=settings, state=env["store"], detection=_detection())
    by_id = {item["id"]: item for item in payload["backends"]}
    assert by_id["brave"]["health"] == "circuit"
    assert by_id["exa"]["health"] == "quota"


def test_status_reports_ok_after_a_success(env):
    settings = _settings(BRAVE_API_KEY="k")
    env["store"].record_failure("brave", error="503")
    env["store"].record_success("brave", latency_ms=42.0)
    payload = api.status_payload(settings=settings, state=env["store"], detection=_detection())
    brave = next(item for item in payload["backends"] if item["id"] == "brave")
    assert brave["health"] == "ok"
    assert brave["latency_p50_ms"] == 42.0
    assert brave["calls"] == 2 and brave["failures"] == 1


def test_status_includes_cache_and_block_report(env):
    env["store"].cache_put(NS_SEARCH_GENERAL, "k", {"rows": []}, 900)
    env["store"].domain_memory_put("example.com", "challenge", "chromium", signals=("cf-mitigated",))
    payload = api.status_payload(settings=env["settings"], state=env["store"], detection=_detection())
    assert payload["cache"]["rows"] == 1
    assert payload["domains"][0]["domain"] == "example.com"
    assert payload["domains"][0]["block_class"] == "challenge"


def test_status_answers_under_three_hundred_ms_with_a_warm_store(env):
    """PLAN.md:133-134 — measured on this machine, warm store and cached detection."""
    first = api.status_payload(settings=env["settings"], state=env["store"])
    assert first["ok"] is True
    started = time.perf_counter()
    second = api.status_payload(settings=env["settings"], state=env["store"])
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert second["ok"] is True
    assert elapsed_ms < 300, f"status took {elapsed_ms:.1f} ms"


def test_status_uses_the_shared_store(env):
    assert api.store(env["settings"]) is api.store(env["settings"])


# ── probe ──────────────────────────────────────────────────────────────────────


def test_probe_unknown_backend_is_a_clean_error(env):
    result = api.probe_payload("nope", settings=env["settings"], state=env["store"])
    assert result["ok"] is False
    assert "unknown backend" in result["error"]


def test_probe_records_usage(env, monkeypatch):
    class Fake:
        def probe(self):
            return {"ok": True, "error": None, "mode": "keyless", "results": 1}

    monkeypatch.setattr(api, "make_backend", lambda vendor, settings: Fake())
    result = api.probe_payload("ddgs", settings=env["settings"], state=env["store"])
    assert result["ok"] is True
    assert result["results"][0]["backend"] == "ddgs"
    assert env["store"].backend_state("ddgs").calls == 1


def test_probe_all_covers_only_configured_backends(env, monkeypatch):
    probed: list[str] = []

    class Fake:
        def probe(self):
            return {"ok": True, "error": None, "mode": "keyless", "results": 0}

    def fake_make(vendor, settings):
        probed.append(vendor)
        return Fake()

    monkeypatch.setattr(api, "make_backend", fake_make)
    api.probe_payload(None, settings=env["settings"], state=env["store"])
    assert "ddgs" in probed
    assert "brave" not in probed


# ── test ───────────────────────────────────────────────────────────────────────


def test_test_requires_a_query(env):
    assert api.test_payload("", settings=env["settings"], state=env["store"])["ok"] is False
    assert api.test_payload("   ", settings=env["settings"], state=env["store"])["ok"] is False


def test_test_pinned_backend_reports_tier_and_latency(env, monkeypatch):
    class Fake:
        key = ""
        keyless_capable = True

        def missing_message(self):
            return ""

        def mode(self):
            return "keyless"

        def tier(self, plan="paid"):
            return "keyless"

        def search(self, query, limit=5, **kwargs):
            return {"ok": True, "results": [{"url": "https://example.com", "title": "t"}]}

    monkeypatch.setattr(api, "make_backend", lambda vendor, settings: Fake())
    result = api.test_payload("q", backend_id="ddgs", settings=env["settings"], state=env["store"])
    assert result["ok"] is True
    assert result["backend"] == "ddgs"
    assert result["tier"] == "keyless"
    assert isinstance(result["latency_ms"], float)
    assert env["store"].backend_state("ddgs").calls == 1


def test_test_pinned_backend_without_a_key_explains(env, monkeypatch):
    class Fake:
        def missing_message(self):
            return "BRAVE_API_KEY is not set"

    monkeypatch.setattr(api, "make_backend", lambda vendor, settings: Fake())
    result = api.test_payload("q", backend_id="brave", settings=env["settings"], state=env["store"])
    assert result["ok"] is False
    assert "BRAVE_API_KEY" in result["error"]


def test_test_routed_names_the_winning_backend(env, monkeypatch):
    class FakeRouter:
        def __init__(self, store=None, settings=None):
            pass

        def search(self, query, limit=5):
            return {
                "success": True,
                "data": {"web": [{"backend": "ddgs", "tier": "keyless", "elapsed_ms": 12.5, "url": "u"}]},
            }

    monkeypatch.setattr(api, "Router", FakeRouter)
    result = api.test_payload("q", settings=env["settings"], state=env["store"])
    assert result["ok"] is True
    assert (result["backend"], result["tier"], result["latency_ms"]) == ("ddgs", "keyless", 12.5)


def test_test_routed_failure_returns_the_error(env, monkeypatch):
    class FakeRouter:
        def __init__(self, store=None, settings=None):
            pass

        def search(self, query, limit=5):
            return {"success": False, "error": "no backend answered"}

    monkeypatch.setattr(api, "Router", FakeRouter)
    result = api.test_payload("q", settings=env["settings"], state=env["store"])
    assert result["ok"] is False
    assert result["error"] == "no backend answered"


# ── install and jobs ───────────────────────────────────────────────────────────


def test_install_unknown_target_is_a_clean_error(env):
    result = api.install_payload("chrome", settings=env["settings"], state=env["store"])
    assert result["ok"] is False
    assert "unknown install target" in result["error"]


def test_install_browserbase_reports_the_key_route(env):
    result = api.install_payload("browserbase", settings=env["settings"], state=env["store"])
    assert result["ok"] is True
    assert result["started"] is False
    assert "browser.cloud_provider" in result["next"]


def test_install_playwright_returns_a_job(env, monkeypatch):
    monkeypatch.setattr(
        api,
        "start_install_job",
        lambda store, target, wait=False: {
            "started": True,
            "target": target,
            "job": {"id": "j1", "status": "queued"},
            "commands": [["python", "-m", "playwright", "install", "chromium"]],
            "note": "",
            "next": "hermes sluice-search install camofox",
        },
    )
    result = api.install_payload("playwright", settings=env["settings"], state=env["store"])
    assert result["ok"] is True and result["started"] is True
    assert result["job"]["id"] == "j1"


def test_install_stop_camofox(env, monkeypatch):
    monkeypatch.setattr(api, "stop_camofox", lambda: {"stopped": True, "runtime": "/usr/bin/podman"})
    result = api.install_payload("camofox", action="stop", settings=env["settings"], state=env["store"])
    assert result["ok"] is True and result["stopped"] is True


def test_install_stop_rejects_other_targets(env):
    result = api.install_payload("playwright", action="stop", settings=env["settings"], state=env["store"])
    assert result["ok"] is False


def test_job_payload_returns_the_record(env):
    job_id = env["store"].create_job("install", "playwright", command="x")
    assert api.job_payload(job_id, state=env["store"])["job"]["id"] == job_id
    missing = api.job_payload("nope", state=env["store"])
    assert missing["ok"] is False and missing["status"] == 404


# ── cache / settings / setup ───────────────────────────────────────────────────


def test_cache_clear_empties_both_caches(env):
    env["store"].cache_put(NS_SEARCH_GENERAL, "k", {"rows": []}, 900)
    outer = env["tmp"] / "hermes" / "cache" / "web"
    outer.mkdir(parents=True)
    (outer / "page.json").write_text("{}", encoding="utf-8")

    result = api.cache_clear_payload(settings=env["settings"], state=env["store"])
    assert result["ok"] is True
    assert result["sluice_rows"] == 1
    assert result["outer"]["removed"] == 1
    assert env["store"].cache_count() == 0


def test_settings_payload_lists_editable_keys_and_plans(env):
    payload = api.settings_payload(settings=env["settings"])
    assert payload["ok"] is True
    assert "tier_order" in payload["values"]
    assert "keyless_enabled" in payload["values"]
    assert payload["plans"]["exa"] == "free"
    assert payload["plans"]["deepseek"] == "paid"
    assert "EXA_API_KEY" not in payload["values"]


def test_save_settings_refuses_unknown_and_secret_keys(env):
    assert api.save_settings_payload({"EXA_API_KEY": "x"}, writer=lambda values: [])["ok"] is False
    assert api.save_settings_payload({}, writer=lambda values: [])["ok"] is False
    assert api.save_settings_payload({"nonsense": 1}, writer=lambda values: [])["ok"] is False


def test_save_settings_writes_through_the_injected_writer(env):
    seen: list[dict] = []

    def writer(values):
        seen.append(dict(values))
        return list(values)

    result = api.save_settings_payload({"keyless_enabled": False, "plan_brave": "paid"}, writer=writer)
    assert result["ok"] is True
    assert seen == [{"keyless_enabled": False, "plan_brave": "paid"}]
    assert sorted(result["written"]) == ["keyless_enabled", "plan_brave"]


def test_save_settings_refuses_a_plan_key_that_is_not_declared(env):
    """Only the ``plan_settings()`` keys are exempt from the editable-key allowlist.

    A ``plan_`` prefix used to be enough, so ``plan_nonsense`` was written into
    ``plugins.entries.sluice-search.settings`` where nothing reads it.
    """
    written: list[dict] = []
    result = api.save_settings_payload(
        {"plan_nonsense": "free"},
        writer=lambda values: written.append(dict(values)) or list(values),
    )
    assert result["ok"] is False
    assert "plan_nonsense" in result["error"]
    assert written == [], "an undeclared plan key must not reach the writer"


def test_save_settings_writes_every_declared_plan_key(env):
    from sluice.config import plan_settings

    keys = list(plan_settings())
    assert keys, "the manifest declares at least one plan_<vendor> key"
    for key in keys:
        written: list[dict] = []

        def writer(values, seen=written):
            seen.append(dict(values))
            return list(values)

        result = api.save_settings_payload({key: "paid"}, writer=writer)
        assert result["ok"] is True, (key, result)
        assert written == [{key: "paid"}], key


def test_save_settings_surfaces_a_refusal(env):
    def writer(values):
        raise PermissionError("Plugin settings cannot be changed in a managed install")

    result = api.save_settings_payload({"keyless_enabled": False}, writer=writer)
    assert result["ok"] is False
    assert "managed install" in result["error"]


def test_settings_payload_carries_the_searxng_refusal_reason(env):
    """F4 residual: the pane re-reads an empty URL, so the reason must travel with it."""
    reason = "SEARXNG_URL has no host: 'http://:8888'."
    payload = api.settings_payload(settings={**env["settings"], "searxng_url_error": reason})
    assert payload["values"]["searxng_url"] == ""
    assert payload["searxng_url_error"] == reason


def test_settings_payload_reports_no_searxng_error_for_a_missing_url(env):
    payload = api.settings_payload(settings=env["settings"])
    assert payload["searxng_url_error"] == ""


def test_save_settings_refuses_a_searxng_url_the_validator_rejects(env):
    written: list[dict] = []
    result = api.save_settings_payload(
        {"searxng_url": "http://host:abc"}, writer=lambda values: written.append(dict(values)) or list(values)
    )
    assert result["ok"] is False
    assert "not a parseable URL" in result["error"]
    assert written == [], "a refused URL must not be written before its reason is known"


def test_save_settings_still_writes_an_empty_or_usable_searxng_url(env):
    for value in ("", "http://127.0.0.1:8888"):
        written: list[dict] = []

        def writer(values, seen=written):
            seen.append(dict(values))
            return list(values)

        result = api.save_settings_payload({"searxng_url": value}, writer=writer)
        assert result["ok"] is True, (value, result)
        assert written == [{"searxng_url": value}]


def test_test_payload_answers_a_refused_searxng_address_instead_of_raising(env):
    """``api.test_payload`` backs ``hermes sluice-search test``; a refused address is a payload."""
    from sluice.config import normalize_searxng_url

    for raw in ("http://ho\u200bst:8888", "http://127.0.0.1:1/" + "a" * (1024 * 1024)):
        url, error = normalize_searxng_url(raw)
        settings = {**env["settings"], "searxng_url": url, "searxng_url_error": error}
        payload = api.test_payload("q", backend_id="searxng", settings=settings, state=env["store"])
        assert payload["ok"] is False, raw
        assert "SEARXNG_URL" in payload["error"], (raw, payload["error"])


def test_setup_dry_run_lists_three_routes(env, monkeypatch):
    seen: list[bool] = []

    def fake_setup(*, apply=False):
        seen.append(apply)
        return [
            {"key": key, "value": value, "command": ["hermes", "config", "set", key, value],
             "applied": apply, "ok": None, "returncode": None, "output": "", "error": None}
            for key, value in (
                ("web.search_backend", "sluice-search"),
                ("web.extract_backend", "sluice-search"),
                ("web.cache_enabled", "false"),
            )
        ]

    monkeypatch.setattr(api, "setup_routes", fake_setup)
    payload = api.setup_payload()
    assert payload["ok"] is True and payload["applied"] is False
    assert len(payload["routes"]) == 3
    assert seen == [False]


# ── the FastAPI router itself ──────────────────────────────────────────────────


def test_router_mounts_every_route(env):
    fastapi = pytest.importorskip("fastapi")
    client_mod = pytest.importorskip("fastapi.testclient")
    from dashboard import plugin_api

    app = fastapi.FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/sluice-search-search")
    client = client_mod.TestClient(app)

    response = client.get("/api/plugins/sluice-search-search/status")
    assert response.status_code == 200
    assert response.json()["plugin"] == "sluice"

    assert client.get("/api/plugins/sluice-search-search/jobs/nope").json()["status"] == 404
    assert client.post("/api/plugins/sluice-search-search/test", json={}).json()["ok"] is False
    assert client.post("/api/plugins/sluice-search-search/install", json={"target": "chrome"}).json()["ok"] is False
    assert client.get("/api/plugins/sluice-search-search/settings").json()["ok"] is True
    assert client.post("/api/plugins/sluice-search-search/cache/clear").json()["ok"] is True
    assert client.post("/api/plugins/sluice-search-search/setup", json={}).json()["applied"] is False


def test_router_folds_a_handler_exception_into_json(env):
    fastapi = pytest.importorskip("fastapi")
    client_mod = pytest.importorskip("fastapi.testclient")
    from dashboard import plugin_api

    def boom():
        raise RuntimeError("database is locked")

    original = plugin_api.api.status_payload
    plugin_api.api.status_payload = boom
    try:
        app = fastapi.FastAPI()
        app.include_router(plugin_api.router, prefix="/api/plugins/sluice-search-search")
        response = client_mod.TestClient(app).get("/api/plugins/sluice-search-search/status")
    finally:
        plugin_api.api.status_payload = original
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and "database is locked" in body["error"]


# ── malformed request bodies: the no-raise contract ────────────────────────────


def test_test_payload_coerces_a_malformed_limit(env, monkeypatch):
    """``api.test_payload`` is documented as never raising: a bad ``limit`` is defaulted."""

    class FakeRouter:
        def __init__(self, store=None, settings=None):
            self.seen = []

        def search(self, query, limit=5):
            self.seen.append(limit)
            return {
                "success": True,
                "data": {"web": [{"backend": "ddgs", "tier": "keyless", "elapsed_ms": 1.0}]},
            }

    made: list[FakeRouter] = []

    def fake_router(store=None, settings=None):
        router = FakeRouter(store=store, settings=settings)
        made.append(router)
        return router

    monkeypatch.setattr(api, "Router", fake_router)
    for bad in ("not-a-number", None, {"five": 5}, [5]):
        result = api.test_payload(
            "q", limit=bad, settings=env["settings"], state=env["store"]  # type: ignore[arg-type]
        )
        assert result["ok"] is True, bad
    assert [router.seen for router in made] == [[5], [5], [5], [5]]


def test_test_payload_rejects_a_non_string_query(env):
    result = api.test_payload(123, settings=env["settings"], state=env["store"])  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "non-empty string" in result["error"]


def test_router_never_answers_a_malformed_test_body_with_a_500(env, monkeypatch):
    """PLAN.md:127-134 / the plugin contract: operational failure is JSON, not a traceback.

    ``POST /test`` runs before the caller is known to have sent sane JSON, so the wrapper
    must hand the raw fields to ``_safe`` and let coercion/validation produce an envelope.
    """
    fastapi = pytest.importorskip("fastapi")
    client_mod = pytest.importorskip("fastapi.testclient")
    from dashboard import plugin_api

    class FakeRouter:
        def __init__(self, store=None, settings=None):
            pass

        def search(self, query, limit=5):
            return {
                "success": True,
                "data": {"web": [{"backend": "ddgs", "tier": "keyless", "elapsed_ms": 1.0}]},
            }

    monkeypatch.setattr(api, "Router", FakeRouter)
    app = fastapi.FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/sluice-search-search")
    client = client_mod.TestClient(app, raise_server_exceptions=False)

    bad_limit = client.post("/api/plugins/sluice-search-search/test", json={"query": "q", "limit": "bad"})
    assert bad_limit.status_code == 200
    assert bad_limit.json()["ok"] is True

    non_string = client.post("/api/plugins/sluice-search-search/test", json={"query": 123})
    assert non_string.status_code == 200
    assert non_string.json()["ok"] is False
    assert "non-empty string" in non_string.json()["error"]


def test_test_payload_reports_zero_matches_as_success(env, monkeypatch):
    """A vendor that answers "nothing" is still a successful request (0 rows, no error)."""

    class FakeRouter:
        def __init__(self, store=None, settings=None):
            pass

        def search(self, query, limit=5):
            return {"success": True, "data": {"web": []}}

    monkeypatch.setattr(api, "Router", FakeRouter)
    result = api.test_payload("no matches for this", settings=env["settings"], state=env["store"])
    assert result["ok"] is True
    assert result["rows"] == []
    assert result["error"] is None
    assert result["backend"] is None


def test_a_pinned_backend_receives_a_clamped_limit(env, monkeypatch):
    seen: dict[str, int] = {}

    class Fake:
        def missing_message(self):
            return ""

        def mode(self):
            return "keyless"

        def tier(self, plan="paid"):
            return "keyless"

        def search(self, query, limit=5, **kwargs):
            seen["limit"] = limit
            return {"ok": True, "results": []}

    monkeypatch.setattr(api, "make_backend", lambda vendor, settings: Fake())
    result = api.test_payload(
        "q", backend_id="ddgs", limit="-3", settings=env["settings"], state=env["store"]  # type: ignore[arg-type]
    )
    assert result["ok"] is True
    assert seen["limit"] == 1


# ── User-facing copy ──────────────────────────────────────────────────────────────
# Every `description` in the manifest renders verbatim in Settings ▸ Plugins and in the
# desktop pane. Internal build-plan bookkeeping (a doc name and a line number) is not
# copy a user should ever read, so the manifest is held to that rule here rather than
# relying on someone noticing it in a screenshot.

#: Substrings that mark a description as internal provenance rather than product copy.
_INTERNAL_MARKERS = ("PLAN.md", "DESIGN.md", "REQUIREMENTS.md", "references/", "RESEARCH")


def test_no_setting_description_carries_an_internal_document_reference():
    from sluice.config import manifest_schema

    offenders = {
        key: str(entry.get("description") or "")
        for key, entry in manifest_schema().items()
        if any(marker in str(entry.get("description") or "") for marker in _INTERNAL_MARKERS)
    }
    assert offenders == {}, f"internal doc references in user-facing copy: {offenders}"


def test_every_editable_setting_has_a_label_and_a_description():
    """The pane renders both, so a missing one is a blank row in the UI."""
    payload = api.settings_payload(settings=dict(DEFAULTS))
    schema = payload["schema"]
    assert set(schema) == set(payload["editable"])
    for key, entry in schema.items():
        assert entry["label"].strip(), f"{key} has no label"
        assert entry["description"].strip(), f"{key} has no description"
        assert entry["label"] != key.replace("_", " "), f"{key} fell back to a humanised key"


def test_the_schema_labels_match_the_manifest_exactly():
    """One source of truth: the pane must not keep its own copy of the labels."""
    from sluice.config import manifest_schema

    manifest = manifest_schema()
    payload = api.settings_payload(settings=dict(DEFAULTS))
    for key, entry in payload["schema"].items():
        assert entry["label"] == manifest[key]["label"], key
        assert entry["description"] == manifest[key]["description"], key
