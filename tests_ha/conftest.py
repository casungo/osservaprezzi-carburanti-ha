"""Fixtures for real Home Assistant contract tests."""
from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

import pytest

pytest_plugins = (
    ("pytest_homeassistant_custom_component",)
    if find_spec("pytest_homeassistant_custom_component") is not None
    else ()
)


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:
    """Keep real-HA tests out of the lightweight default test lane."""
    return config.inipath is None or config.inipath.name != "pytest-ha.ini"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load this repository's custom integration."""
