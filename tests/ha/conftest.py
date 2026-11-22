"""Fixtures for real Home Assistant contract tests."""

from __future__ import annotations

from importlib.util import find_spec

import pytest

pytest_plugins = (
    ("pytest_homeassistant_custom_component",)
    if find_spec("pytest_homeassistant_custom_component") is not None
    else ()
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load this repository's custom integration."""
