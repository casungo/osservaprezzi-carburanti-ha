"""Real Home Assistant regressions for station entity metadata."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.sensor import DATA_COMPONENT
from homeassistant.core import is_callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN
from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager
from tests.ha.test_init import STATION_ID, _station_payload


async def test_entities_keep_translation_keys_and_device_configuration_url(
    hass, monkeypatch
) -> None:
    """Expose localized names without changing stable entity or device identity."""
    await hass.config.async_set_time_zone("Europe/Rome")
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        AsyncMock(return_value=_station_payload()),
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    monkeypatch.setattr(
        CSVStationManager,
        "get_station_by_id",
        lambda self, station_id: {"id": station_id, "latitude": 41.8759, "longitude": 12.4633},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Station",
        unique_id=STATION_ID,
        data={CONF_STATION_ID: STATION_ID},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    for platform, unique_id, translation_key in (
        ("sensor", f"{STATION_ID}_name", "station_name"),
        ("sensor", f"{STATION_ID}_location", "location"),
        ("sensor", f"{STATION_ID}_next_change", "next_change"),
        ("binary_sensor", f"{STATION_ID}_open_closed", "station_open_closed"),
        ("binary_sensor", f"{STATION_ID}_service_1", "food_beverage"),
    ):
        entity_id = entity_registry.async_get_entity_id(platform, DOMAIN, unique_id)
        assert entity_id is not None
        assert entity_registry.async_get(entity_id).translation_key == translation_key

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier((DOMAIN, STATION_ID), entry.entry_id)
    assert device is not None
    assert device.configuration_url == (
        f"https://carburanti.mise.gov.it/ospzSearch/dettaglio/{STATION_ID}"
    )

    next_id = entity_registry.async_get_entity_id("sensor", DOMAIN, f"{STATION_ID}_next_change")
    next_entity = hass.data[DATA_COMPONENT].get_entity(next_id)
    assert is_callback(next_entity._handle_time_tick)
    assert next_entity._time_listener is None
    previous_tick = next_entity._next_change_now
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    assert next_entity._next_change_now == previous_tick

    coordinator = next(iter(entry.runtime_data.stations.values())).coordinator
    coordinator.async_set_updated_data(
        {
            **coordinator.data,
            "opening_hours": [
                {
                    "giornoSettimanaId": day,
                    "flagOrarioContinuato": True,
                    "oraAperturaOrarioContinuato": "08:00",
                    "oraChiusuraOrarioContinuato": "20:00",
                }
                for day in range(1, 8)
            ],
        }
    )
    assert next_entity._time_listener is not None
    _, transition_time = next_entity._next_change
    assert transition_time is not None
    assert next_entity.native_value == dt_util.as_utc(transition_time)
    assert next_entity.native_value.tzinfo is not None
    assert hass.states.get(next_id).attributes["device_class"] == "timestamp"
    async_fire_time_changed(hass, dt_util.as_utc(transition_time))
    await hass.async_block_till_done()
    assert next_entity._next_change_now >= transition_time
    assert next_entity._next_change_now.tzinfo == dt_util.get_default_time_zone()

    coordinator.async_set_updated_data({**coordinator.data, "opening_hours": []})
    assert hass.states.get(next_id).state == "unavailable"

    assert await hass.config_entries.async_unload(entry.entry_id)
