"""Tests for the per-station refresh action."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.osservaprezzi_carburanti.button import (
    StationRefreshButton,
    async_setup_entry,
)
from custom_components.osservaprezzi_carburanti.const import DOMAIN


def test_button_setup_and_press_refreshes_only_its_coordinator() -> None:
    """Expose a stable action even before the station has any payload."""
    coordinator = MagicMock(data=None, last_update_success=True, last_refresh_from_cache=False)
    coordinator.async_request_refresh = AsyncMock()
    entry = MagicMock(entry_id="entry", data={"station_id": "123"})
    hass = MagicMock(data={DOMAIN: {"entry": {"coordinator": coordinator}}})
    add_entities = MagicMock()

    asyncio.run(async_setup_entry(hass, entry, add_entities))
    button = add_entities.call_args.args[0][0]
    assert isinstance(button, StationRefreshButton)
    assert button._attr_unique_id == "123_refresh_prices"
    assert button._attr_translation_key == "refresh_prices"
    assert button.available is True
    assert button.device_info["identifiers"] == {(DOMAIN, "123")}
    asyncio.run(button.async_press())
    coordinator.async_request_refresh.assert_awaited_once()


def test_button_allows_retry_after_failed_refresh() -> None:
    """Surface a failed request without disabling the retry action."""
    coordinator = MagicMock(last_update_success=False, last_refresh_from_cache=False)
    coordinator.async_request_refresh = AsyncMock()
    button = StationRefreshButton(coordinator, MagicMock(data={"station_id": "123"}))
    assert button.available is True
    with pytest.raises(HomeAssistantError):
        asyncio.run(button.async_press())

    coordinator.last_update_success = True
    asyncio.run(button.async_press())
    assert coordinator.async_request_refresh.await_count == 2


def test_button_rejects_refresh_that_only_restored_cached_data() -> None:
    """Do not report success when the coordinator could only use its cache."""
    coordinator = MagicMock(last_update_success=True, last_refresh_from_cache=True)
    coordinator.async_request_refresh = AsyncMock()
    button = StationRefreshButton(coordinator, MagicMock(data={"station_id": "123"}))

    with pytest.raises(HomeAssistantError):
        asyncio.run(button.async_press())

    coordinator.async_request_refresh.assert_awaited_once()


def test_button_propagates_coordinator_exception() -> None:
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock(side_effect=HomeAssistantError("failed"))
    button = StationRefreshButton(coordinator, MagicMock(data={"station_id": "123"}))
    with pytest.raises(HomeAssistantError, match="failed"):
        asyncio.run(button.async_press())
