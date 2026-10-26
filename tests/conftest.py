"""Keep developer credentials and external service settings out of every test."""

import os

import pytest

from etl_agent.config import Settings


@pytest.fixture(autouse=True)
def isolated_application_settings(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in tuple(os.environ):
        if name.startswith("ETL_"):
            monkeypatch.delenv(name)
