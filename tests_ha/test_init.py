"""Contract tests against a real Home Assistant instance."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.osservaprezzi_carburanti.const import (
    CONF_STATION_ID,
    DOMAIN,
    SERVICE_CLEAR_CACHE,
    SERVICE_COMPARE_STATIONS,
    SERVICE_FORCE_CSV_UPDATE,
)
from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager

STATION_ID = "54233"


def _station_payload() -> dict[str, Any]:
    """Return deterministic upstream data with every representative platform."""
    return {
        "id": int(STATION_ID),
        "name": "UNION - BORGHESANO LUCCHESE",
        "nomeImpianto": "UNION - BORGHESANO LUCCHESE",
        "address": "BORGHESANO LUCCHESE 2 - 00146 ROMA (RM)",
        "brand": "PompeBianche",
        "company": "UNION GESTIONI SRL",
        "fuels": [{"price": 1.798, "name": "Benzina", "fuelId": 1, "isSelf": True,
                   "serviceAreaId": int(STATION_ID), "insertDate": "2026-06-06T18:15:30Z",
                   "validityDate": "2026-06-06T18:24:29Z"}],
        "services": [{"id": 1}],
        "orariapertura": [
            {"giornoSettimanaId": weekday, "flagH24": True, "flagChiusura": False,
             "flagNonComunicato": False, "flagOrarioContinuato": False}
            for weekday in range(1, 9)
        ],
    }


async def test_config_entry_lifecycle_and_services(hass: HomeAssistant, monkeypatch) -> None:
    """Exercise setup, entities, services, reload, and final unload in real HA."""
    fetch_station_data = AsyncMock(return_value=_station_payload())
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        fetch_station_data,
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: False)
    monkeypatch.setattr(CSVStationManager, "async_initialize", AsyncMock(return_value=True))
    monkeypatch.setattr(
        CSVStationManager,
        "get_station_by_id",
        lambda self, station_id: {"id": station_id, "latitude": 41.8759,
                                  "longitude": 12.4633, "operator": "UNION GESTIONI SRL",
                                  "station_type": "Stradale", "municipality": "Roma",
                                  "province": "RM"},
    )
    monkeypatch.setattr(CSVStationManager, "async_update_csv_data", AsyncMock(return_value=True))
    monkeypatch.setattr(CSVStationManager, "async_save_cached_data", AsyncMock(return_value=True))
    monkeypatch.setattr(CSVStationManager, "async_clear_cache", AsyncMock(return_value=True))

    entry = MockConfigEntry(domain=DOMAIN, title="UNION - BORGHESANO LUCCHESE",
                            unique_id=STATION_ID, data={CONF_STATION_ID: STATION_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    price_entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{STATION_ID}_Benzina_self")
    open_entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{STATION_ID}_open_closed")
    service_entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{STATION_ID}_service_1")
    assert price_entity_id is not None and hass.states.get(price_entity_id).state == "1.798"
    assert open_entity_id is not None and hass.states.get(open_entity_id).state == "on"
    assert service_entity_id is not None and hass.states.get(service_entity_id).state == "on"

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    refreshed_data = deepcopy(coordinator.data)
    refreshed_data["fuels"]["Gasolio_self"] = {
        "price": 1.689,
        "last_update": "2026-06-06T18:15:30Z",
        "validity_date": "2026-06-06T18:24:29Z",
    }
    refreshed_data["station_info"]["phoneNumber"] = "+39 06 000000"
    refreshed_data["services"].append({"id": 8})
    coordinator.async_set_updated_data(refreshed_data)
    await hass.async_block_till_done()
    gasolio_entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{STATION_ID}_Gasolio_self")
    phone_entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{STATION_ID}_phoneNumber")
    wifi_entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{STATION_ID}_service_8")
    assert gasolio_entity_id is not None and hass.states.get(gasolio_entity_id).state == "1.689"
    assert phone_entity_id is not None
    assert wifi_entity_id is not None and hass.states.get(wifi_entity_id).state == "on"
    dynamic_entity_ids = {gasolio_entity_id, phone_entity_id, wifi_entity_id}

    coordinator.async_set_updated_data(refreshed_data)
    await hass.async_block_till_done()
    assert {entity.entity_id for entity in registry.entities.values()
            if entity.config_entry_id == entry.entry_id}.issuperset(dynamic_entity_ids)
    disappeared_data = deepcopy(refreshed_data)
    del disappeared_data["fuels"]["Gasolio_self"]
    disappeared_data["station_info"].pop("phoneNumber")
    disappeared_data["services"] = [{"id": 1}]
    coordinator.async_set_updated_data(disappeared_data)
    coordinator.async_set_updated_data(refreshed_data)
    await hass.async_block_till_done()
    assert all(registry.async_get(entity_id) is not None for entity_id in dynamic_entity_ids)

    assert hass.services.has_service(DOMAIN, SERVICE_FORCE_CSV_UPDATE)
    assert hass.services.has_service(DOMAIN, SERVICE_CLEAR_CACHE)
    assert hass.services.has_service(DOMAIN, SERVICE_COMPARE_STATIONS)
    await hass.services.async_call(DOMAIN, SERVICE_FORCE_CSV_UPDATE, {}, blocking=True)
    await hass.services.async_call(DOMAIN, SERVICE_CLEAR_CACHE, {}, blocking=True)
    comparison = await hass.services.async_call(
        DOMAIN, SERVICE_COMPARE_STATIONS, {}, blocking=True, return_response=True
    )
    assert comparison is not None
    assert comparison["stations"][entry.entry_id]["station_id"] == int(STATION_ID)
    json.dumps(comparison)

    entity_ids_before_reload = {
        entity.entity_id for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    entity_ids_after_reload = {
        entity.entity_id for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    assert entity_ids_after_reload == entity_ids_before_reload

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator._csv_update_listener is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._csv_update_listener is None
    assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]
    assert not hass.services.has_service(DOMAIN, SERVICE_FORCE_CSV_UPDATE)
    assert not hass.services.has_service(DOMAIN, SERVICE_CLEAR_CACHE)
    assert not hass.services.has_service(DOMAIN, SERVICE_COMPARE_STATIONS)
    unloaded_states = {
        entity_id: hass.states.get(entity_id).state
        for entity_id in entity_ids_after_reload
        if hass.states.get(entity_id) is not None
    }
    assert unloaded_states == {
        entity_id: "unavailable" for entity_id in entity_ids_after_reload
    }
    assert fetch_station_data.await_count >= 3
