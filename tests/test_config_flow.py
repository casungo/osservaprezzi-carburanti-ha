"""Tests for config flow validation helpers."""
from __future__ import annotations

import asyncio
import sys
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.config_flow import (  # noqa: E402
    CannotConnect,
    InvalidStation,
    _validate_station,
)


def _make_response_error(status: int) -> aiohttp.ClientResponseError:
    """Create a minimal response error for tests."""
    return aiohttp.ClientResponseError(
        request_info=cast(Any, None),
        history=(),
        status=status,
        message="test",
        headers=cast(Any, None),
    )


def test_validate_station_success(monkeypatch):
    hass_mock = AsyncMock()
    fetch_mock = AsyncMock(return_value={"id": "1234", "name": "Test Station"})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        fetch_mock,
    )

    result = asyncio.run(_validate_station(hass_mock, " 1234 "))
    assert result == {"name": "Test Station"}
    fetch_mock.assert_awaited_once_with(hass_mock, "1234")


def test_validate_station_not_found(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(side_effect=_make_response_error(404)),
    )

    try:
        asyncio.run(_validate_station(AsyncMock(), "1234"))
    except InvalidStation:
        pass
    else:
        raise AssertionError("Expected InvalidStation")


def test_validate_station_connection_error(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(side_effect=aiohttp.ClientError("boom")),
    )

    try:
        asyncio.run(_validate_station(AsyncMock(), "1234"))
    except CannotConnect:
        pass
    else:
        raise AssertionError("Expected CannotConnect")


def test_validate_station_empty_id():
    try:
        asyncio.run(_validate_station(AsyncMock(), "   "))
    except InvalidStation:
        pass
    else:
        raise AssertionError("Expected InvalidStation")
