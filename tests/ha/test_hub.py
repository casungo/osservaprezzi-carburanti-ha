"""Home Assistant contracts for the hub and station subentries."""

from __future__ import annotations

from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.osservaprezzi_carburanti import async_migrate_entry, config_flow
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN
from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager
from tests.ha.test_init import _station_payload


def station(station_id: str) -> dict:
    return {
        "data": {CONF_STATION_ID: station_id},
        "subentry_type": "station",
        "title": f"Station {station_id}",
        "unique_id": station_id,
    }


def hub(*stations: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id="hub",
        data={},
        subentries_data=[station(value) for value in stations],
    )


async def test_migration_consolidates_registries(hass, monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        AsyncMock(side_effect=lambda hass, station_id: _station_payload(station_id)),
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    monkeypatch.setattr(CSVStationManager, "get_station_by_id", lambda self, station_id: {})
    entries = [
        MockConfigEntry(
            domain=DOMAIN,
            version=2,
            unique_id=f"station_{value}",
            data={CONF_STATION_ID: value},
            title=f"Station {value}",
        )
        for value in ("123", "456")
    ]
    registry, devices = er.async_get(hass), dr.async_get(hass)
    original = []
    for entry, value in zip(entries, ("123", "456"), strict=True):
        entry.add_to_hass(hass)
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, value)}
        )
        entity = registry.async_get_or_create(
            "sensor", DOMAIN, f"{value}_Benzina_self", config_entry=entry, device_id=device.id
        )
        original.append((device.id, entity.entity_id))
    assert await async_migrate_entry(hass, entries[0])
    assert registry.async_get(original[0][1]) is not None
    assert await async_migrate_entry(hass, entries[1])
    assert registry.async_get(original[1][1]) is not None
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entries[1].entry_id) is None
    assert entries[0].unique_id == "hub"
    assert len(entries[0].subentries) == 2
    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()
    for value, (device_id, entity_id) in zip(("123", "456"), original, strict=True):
        subentry = next(s for s in entries[0].subentries.values() if s.unique_id == value)
        entity = registry.async_get(entity_id)
        assert entity.config_entry_id == entries[0].entry_id
        assert entity.config_subentry_id == subentry.subentry_id
        assert entity.device_id == device_id
        assert devices.async_get(device_id) is not None
    assert await hass.config_entries.async_unload(entries[0].entry_id)


async def test_subentry_add_and_reconfigure(hass, monkeypatch):
    entry = hub("123")
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        config_flow, "_validate_station", AsyncMock(return_value={"name": "Station"})
    )
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "station_id"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STATION_ID: "456"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(s for s in entry.subentries.values() if s.unique_id == "456")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_STATION_ID: "456",
            "cron_expression": "0 6 * * *",
            "price_stale_hours": "48",
            "tracked_fuels": ["Benzina"],
            "tracked_service_modes": ["self"],
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert subentry.data["price_stale_hours"] == 48
    assert subentry.data["cron_expression"] == "0 6 * * *"


async def test_market_summary_tracks_station_updates(hass, monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        AsyncMock(side_effect=lambda hass, station_id: _station_payload(station_id)),
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    monkeypatch.setattr(CSVStationManager, "get_station_by_id", lambda self, station_id: {})
    entry = hub("123", "456")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    cheapest = registry.async_get_entity_id("sensor", DOMAIN, "market_cheapest_benzina")
    average = registry.async_get_entity_id("sensor", DOMAIN, "market_average_benzina")
    assert cheapest
    assert average
    first, second = entry.runtime_data.stations.values()
    data = dict(second.coordinator.data)
    data["fuels"] = {"Benzina_self": {"price": 1.2}}
    second.coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()
    assert float(hass.states.get(cheapest).state) == 1.2
    assert float(hass.states.get(average).state) == 1.499
    assert registry.async_get(cheapest).config_subentry_id is None
    assert first.coordinator is not second.coordinator
    assert await hass.config_entries.async_unload(entry.entry_id)
