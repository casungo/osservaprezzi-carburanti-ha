"""Real Home Assistant regressions for coordinator cache handling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN
from custom_components.osservaprezzi_carburanti.coordinator import (
    CarburantiDataUpdateCoordinator,
)


def _cached_payload(station_id: int | str) -> dict[str, object]:
    """Return a minimal structurally valid persisted payload."""
    return {
        "station_info": {"id": station_id, "name": "Station"},
        "fuels": {},
        "services": [],
        "opening_hours": [],
        "last_update": "2026-10-05T12:00:00+02:00",
    }


async def test_restore_accepts_numeric_station_id_and_rejects_mismatch(
    hass: HomeAssistant,
) -> None:
    """Restore validates the payload shape and configured station identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Station",
        unique_id="station_123",
        data={CONF_STATION_ID: "123"},
    )
    entry.add_to_hass(hass)
    coordinator = CarburantiDataUpdateCoordinator(hass, entry, MagicMock())
    coordinator._store.async_load = AsyncMock(return_value=_cached_payload(123))

    await coordinator.async_restore()

    assert coordinator.data == _cached_payload(123)
    assert coordinator.last_refresh_from_cache is False

    coordinator._store.async_load = AsyncMock(return_value=_cached_payload(456))
    coordinator.data = None
    await coordinator.async_restore()

    assert coordinator.data is None
