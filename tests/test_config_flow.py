"""Tests for config flow validation helpers."""
from __future__ import annotations

import asyncio
import sys
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.config_flow import (  # noqa: E402
    CannotConnect,
    InvalidStation,
    OptionsFlowHandler,
    OsservaprezziCarburantiConfigFlow,
    _validate_station,
)
from custom_components.osservaprezzi_carburanti.const import (  # noqa: E402
    CONF_CRON_EXPRESSION,
    CONF_STATION_ID,
    DEFAULT_CRON_EXPRESSION,
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


def test_validate_station_invalid_payload(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(return_value={"id": "1234"}),
    )

    with pytest.raises(InvalidStation, match="Invalid station data"):
        asyncio.run(_validate_station(AsyncMock(), "1234"))


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


def test_validate_station_service_error(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(side_effect=_make_response_error(500)),
    )

    with pytest.raises(CannotConnect, match="Service error: 500"):
        asyncio.run(_validate_station(AsyncMock(), "1234"))


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


def _make_config_flow(monkeypatch: pytest.MonkeyPatch) -> OsservaprezziCarburantiConfigFlow:
    flow = OsservaprezziCarburantiConfigFlow()
    flow.hass = AsyncMock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(
        side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
    )
    flow.async_show_form = MagicMock(
        side_effect=lambda **kwargs: {"type": "form", **kwargs}
    )
    return flow


def test_config_flow_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)
    validate_mock = AsyncMock(return_value={"name": "Station"})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        validate_mock,
    )

    result = asyncio.run(flow.async_step_user({CONF_STATION_ID: " 123 "}))

    assert result == {
        "type": "create_entry",
        "title": "Station",
        "data": {CONF_STATION_ID: "123"},
    }
    flow.async_set_unique_id.assert_awaited_once_with("station_123")
    flow._abort_if_unique_id_configured.assert_called_once()
    validate_mock.assert_awaited_once_with(flow.hass, "123")


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (InvalidStation("bad"), "invalid_station"),
        (CannotConnect("down"), "cannot_connect"),
        (ValueError("unexpected"), "unknown"),
    ],
)
def test_config_flow_user_errors_show_form(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Exception,
    error: str,
) -> None:
    flow = _make_config_flow(monkeypatch)
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(side_effect=side_effect),
    )

    result = asyncio.run(flow.async_step_user({CONF_STATION_ID: "123"}))

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


def test_config_flow_user_initial_form(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)

    result = asyncio.run(flow.async_step_user())

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}


def _make_options_flow(options: dict[str, str] | None = None) -> OptionsFlowHandler:
    handler = object.__new__(OptionsFlowHandler)
    handler.config_entry = MagicMock(title="Station")
    handler.options = options or {}
    handler.async_create_entry = MagicMock(
        side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
    )
    handler.async_show_form = MagicMock(
        side_effect=lambda **kwargs: {"type": "form", **kwargs}
    )
    return handler


def test_async_get_options_flow_returns_handler() -> None:
    handler = OsservaprezziCarburantiConfigFlow.async_get_options_flow(MagicMock())

    assert isinstance(handler, OptionsFlowHandler)


def test_options_flow_initial_form() -> None:
    handler = _make_options_flow({CONF_CRON_EXPRESSION: "0 6 * * *"})

    result = asyncio.run(handler.async_step_init())

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {}


def test_options_flow_valid_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _make_options_flow({CONF_CRON_EXPRESSION: DEFAULT_CRON_EXPRESSION})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.validate_cron_expression",
        lambda cron_expr: True,
    )

    result = asyncio.run(handler.async_step_init({CONF_CRON_EXPRESSION: "0 6 * * *"}))

    assert result == {
        "type": "create_entry",
        "title": "",
        "data": {CONF_CRON_EXPRESSION: "0 6 * * *"},
    }


def test_options_flow_invalid_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _make_options_flow()
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.validate_cron_expression",
        lambda cron_expr: False,
    )

    result = asyncio.run(handler.async_step_init({CONF_CRON_EXPRESSION: "bad"}))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_cron_expression"}
