"""Keep every test off the developer's real Hermes state.

T13 requires ``pytest`` to be green on a clean checkout. Two entry points resolve the plugin
data directory — ``sluice.config.plugin_data_dir`` and ``sluice.store.default_data_dir`` —
and any test that builds a provider or an API payload without injecting a context would
otherwise read, and create, ``~/.hermes``. This fixture points both at a per-test temporary
directory, so a leak shows up as a failing assertion in the test that caused it instead of a
stray ``sluice.db`` on the machine that ran the suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def hermetic_hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HOME and plugin data directory for every test."""
    home = tmp_path / "hermes-home"
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("SLUICE_DATA_DIR", str(data))
    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    # F4: both URL overrides read the environment, so a host export would otherwise decide
    # which backend is "configured" inside a hermetic test.
    monkeypatch.delenv("SEARXNG_URL", raising=False)

    # Hermes' own resolver wins over SLUICE_DATA_DIR when Hermes is importable, so patch the
    # function every caller ends up in, not just the environment. Read the variable at call
    # time so a test that points SLUICE_DATA_DIR somewhere else still gets its own directory.
    def hermetic_default_data_dir() -> Path:
        return Path(os.environ.get("SLUICE_DATA_DIR") or data)

    monkeypatch.setattr("sluice.store.default_data_dir", hermetic_default_data_dir)
    return data
