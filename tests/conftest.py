"""Shared test set-up: no test may read or write the real settings, memory or log of the person running
the tests (a test with an in-memory store used to append to their real tacosort.log)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _private_tacosort_dirs(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("tacosort-home")
    monkeypatch.setenv("TACOSORT_DATA", str(base / "data"))
    monkeypatch.setenv("TACOSORT_CONFIG", str(base / "config.yaml"))
