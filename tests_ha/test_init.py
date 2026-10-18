"""Contract tests against a real Home Assistant instance."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import entity_platform, issue_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.osservaprezzi_carburanti import async_setup
from custom_components.osservaprezzi_carburanti.const import (
    CONF_STATION_ID,
    DOMAIN,
    SERVICE_CLEAR_CACHE,
    SERVICE_COMPARE_STATIONS,
    SERVICE_FORCE_CSV_UPDATE,
    SERVICE_REFRESH_PRICES,
    SERVICE_SEARCH_REGISTRY,
)
from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager

STATION_ID = "54233"


async def test_station_not_found_repair_issue_is_removed_after_refresh(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Report a missing station and remove the issue after it becomes available."""
    not_found = aiohttp.ClientResponseError(
        request_info=cast(Any, SimpleNamespace(real_url="https://example.test")),
        history=(),
        status=404,
        message="not found",
    )
    fetch_station_data = AsyncMock(side_effect=[not_found, _station_payload()])
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        fetch_station_data,
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    monkeypatch.setattr(
        CSVStationManager,
        "get_station_by_id",
        lambda self, station_id: {"latitude": 41.8759, "longitude": 12.4633},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Missing station",
        unique_id=STATION_ID,
        data={CONF_STATION_ID: STATION_ID},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    repairs = issue_registry.async_get(hass)
    issue_id = f"station_not_found_{entry.entry_id}"
    assert repairs.async_get_issue(DOMAIN, issue_id) is not None

    await hass.data[DOMAIN][entry.entry_id]["coordinator"].async_refresh()
    assert repairs.async_get_issue(DOMAIN, issue_id) is None

    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("cancel_during", ["forward_drain", "platform_unload"])
async def test_cancelled_setup_unloads_forwarded_platforms_and_can_retry(
    hass: HomeAssistant, monkeypatch, cancel_during: str
) -> None:
    """Rollback platforms that loaded before setup was cancelled."""
    from custom_components.osservaprezzi_carburanti import binary_sensor

    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        AsyncMock(return_value=_station_payload()),
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    monkeypatch.setattr(
        CSVStationManager,
        "get_station_by_id",
        lambda self, station_id: {"latitude": 41.8759, "longitude": 12.4633},
    )

    binary_setup_started = asyncio.Event()
    release_binary_setup = asyncio.Event()
    platform_unload_started = asyncio.Event()
    release_platform_unload = asyncio.Event()
    original_binary_setup = binary_sensor.async_setup_entry
    original_unload_platforms = hass.config_entries.async_unload_platforms

    async def blocked_binary_setup(hass, entry, async_add_entities) -> None:
        binary_setup_started.set()
        await release_binary_setup.wait()
        await original_binary_setup(hass, entry, async_add_entities)

    monkeypatch.setattr(binary_sensor, "async_setup_entry", blocked_binary_setup)

    async def delayed_unload_platforms(entry, platforms):
        if cancel_during == "platform_unload":
            platform_unload_started.set()
            await release_platform_unload.wait()
        return await original_unload_platforms(entry, platforms)

    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", delayed_unload_platforms
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cancellation test",
        unique_id=STATION_ID,
        data={CONF_STATION_ID: STATION_ID},
    )
    entry.add_to_hass(hass)
    setup_task = hass.async_create_task(hass.config_entries.async_setup(entry.entry_id))

    await asyncio.wait_for(binary_setup_started.wait(), timeout=10)
    sensor_entity_id = f"sensor.{STATION_ID}_location"
    for _ in range(100):
        if hass.states.get(sensor_entity_id) is not None:
            break
        await asyncio.sleep(0.01)
    assert hass.states.get(sensor_entity_id) is not None

    setup_task.cancel()
    await asyncio.sleep(0)
    assert not setup_task.done()
    try:
        if cancel_during == "forward_drain":
            setup_task.cancel()
            await asyncio.sleep(0)
            release_binary_setup.set()
        else:
            release_binary_setup.set()
            await asyncio.wait_for(platform_unload_started.wait(), timeout=10)
            setup_task.cancel()
            await asyncio.sleep(0)
            release_platform_unload.set()
        with pytest.raises(asyncio.CancelledError):
            await setup_task
        await hass.async_block_till_done()
    finally:
        release_binary_setup.set()
        release_platform_unload.set()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    sensor_state = hass.states.get(sensor_entity_id)
    assert sensor_state is None or sensor_state.attributes.get("restored") is True
    owned_platforms = [
        platform
        for platform in entity_platform.async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry
    ]
    assert owned_platforms
    assert all(not platform.entities for platform in owned_platforms)

    monkeypatch.setattr(binary_sensor, "async_setup_entry", original_binary_setup)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(sensor_entity_id) is not None
    assert sum(
        entity.config_entry_id == entry.entry_id
        and entity.unique_id == f"{STATION_ID}_Benzina_self"
        for entity in er.async_get(hass).entities.values()
    ) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cancelled_restore_keeps_resources_owned_by_concurrent_setup(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Keep the shared registry while another entry is awaiting storage."""
    from homeassistant.setup import async_setup_component
    from custom_components.osservaprezzi_carburanti.coordinator import (
        CarburantiDataUpdateCoordinator,
    )

    assert await async_setup_component(hass, DOMAIN, {})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
        AsyncMock(return_value=_station_payload()),
    )
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    pending = MockConfigEntry(domain=DOMAIN, unique_id=STATION_ID,
                              data={CONF_STATION_ID: STATION_ID})
    cancelled = MockConfigEntry(domain=DOMAIN, unique_id="54234",
                                data={CONF_STATION_ID: "54234"})
    for entry in (pending, cancelled):
        entry.add_to_hass(hass)
    started = {entry.entry_id: asyncio.Event() for entry in (pending, cancelled)}
    release_restore = asyncio.Event()
    original_restore = CarburantiDataUpdateCoordinator.async_restore

    async def blocked_restore(self: CarburantiDataUpdateCoordinator) -> None:
        started[self.config_entry.entry_id].set()
        await release_restore.wait()
        await original_restore(self)

    monkeypatch.setattr(CarburantiDataUpdateCoordinator, "async_restore", blocked_restore)
    pending_task = hass.async_create_task(hass.config_entries.async_setup(pending.entry_id))
    await asyncio.wait_for(started[pending.entry_id].wait(), timeout=10)
    manager = hass.data[DOMAIN]["csv_manager"]
    timer = hass.data[DOMAIN]["csv_update_listener"]
    cancelled_task = hass.async_create_task(hass.config_entries.async_setup(cancelled.entry_id))
    try:
        await asyncio.wait_for(started[cancelled.entry_id].wait(), timeout=10)
        with pytest.raises(HomeAssistantError, match="No matching active"):
            await hass.services.async_call(
                DOMAIN, SERVICE_REFRESH_PRICES, {}, blocking=True, return_response=True
            )
        cancelled_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_task
        assert cancelled.entry_id not in hass.data[DOMAIN]
        assert hass.data[DOMAIN]["csv_manager"] is manager
        assert hass.data[DOMAIN]["csv_update_listener"] is timer
    finally:
        if not cancelled_task.done():
            cancelled_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled_task
        release_restore.set()
        assert await pending_task
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(pending.entry_id)
    assert "csv_manager" not in hass.data[DOMAIN]
    assert "csv_update_listener" not in hass.data[DOMAIN]


async def test_cache_services_raise_without_active_entries(hass: HomeAssistant) -> None:
    """Expose no-op cache actions as service failures through Home Assistant."""
    assert await async_setup(hass, {})

    with pytest.raises(HomeAssistantError, match="No active"):
        await hass.services.async_call(DOMAIN, SERVICE_FORCE_CSV_UPDATE, {}, blocking=True)
    with pytest.raises(HomeAssistantError, match="No active"):
        await hass.services.async_call(DOMAIN, SERVICE_CLEAR_CACHE, {}, blocking=True)


def _station_payload(station_id: str = STATION_ID) -> dict[str, Any]:
    """Return deterministic upstream data with every representative platform."""
    return {
        "id": int(station_id),
        "name": "UNION - BORGHESANO LUCCHESE",
        "nomeImpianto": "UNION - BORGHESANO LUCCHESE",
        "address": "BORGHESANO LUCCHESE 2 - 00146 ROMA (RM)",
        "brand": "PompeBianche",
        "company": "UNION GESTIONI SRL",
        "fuels": [{"price": 1.798, "name": "Benzina", "fuelId": 1, "isSelf": True,
                   "serviceAreaId": int(station_id), "insertDate": "2026-06-06T18:15:30Z",
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
    fetch_station_data = AsyncMock(side_effect=lambda hass, station_id: _station_payload(station_id))
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
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_PRICES)
    assert hass.services.has_service(DOMAIN, SERVICE_SEARCH_REGISTRY)
    await hass.services.async_call(DOMAIN, SERVICE_FORCE_CSV_UPDATE, {}, blocking=True)
    await hass.services.async_call(DOMAIN, SERVICE_CLEAR_CACHE, {}, blocking=True)
    comparison = await hass.services.async_call(
        DOMAIN, SERVICE_COMPARE_STATIONS, {}, blocking=True, return_response=True
    )
    assert comparison is not None
    assert comparison["stations"][entry.entry_id]["station_id"] == int(STATION_ID)
    json.dumps(comparison)
    refresh = await hass.services.async_call(
        DOMAIN,
        SERVICE_REFRESH_PRICES,
        {"station_ids": [STATION_ID]},
        blocking=True,
        return_response=True,
    )
    assert refresh == {
        "refreshed_station_ids": [STATION_ID],
        "refreshed_count": 1,
    }
    registry_search = await hass.services.async_call(
        DOMAIN,
        SERVICE_SEARCH_REGISTRY,
        {"municipality": "Roma"},
        blocking=True,
        return_response=True,
    )
    assert registry_search is not None
    assert registry_search["result_count"] == 0
    json.dumps(registry_search)

    entity_ids_before_reload = {
        entity.entity_id for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    coordinator_before_reload = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    listener_before_reload = hass.data[DOMAIN][entry.entry_id]["listener"]
    assert listener_before_reload is not None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    entity_ids_after_reload = {
        entity.entity_id for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    }
    assert entity_ids_after_reload == entity_ids_before_reload
    assert sum(
        isinstance(value, dict) and "coordinator" in value
        for value in hass.data[DOMAIN].values()
    ) == 1
    assert hass.data[DOMAIN][entry.entry_id]["coordinator"] is not coordinator_before_reload
    assert hass.data[DOMAIN][entry.entry_id]["listener"] is not None
    assert hass.data[DOMAIN][entry.entry_id]["listener"] is not listener_before_reload

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    csv_manager = coordinator.csv_manager
    registry_listener = hass.data[DOMAIN]["csv_update_listener"]

    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Second station",
        unique_id="54234",
        data={CONF_STATION_ID: "54234"},
    )
    second_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second_entry.entry_id)
    await hass.async_block_till_done()
    second_coordinator = hass.data[DOMAIN][second_entry.entry_id]["coordinator"]
    assert second_coordinator.csv_manager is csv_manager
    assert hass.data[DOMAIN]["csv_update_listener"] is registry_listener

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]
    assert hass.data[DOMAIN]["csv_manager"] is csv_manager
    assert hass.services.has_service(DOMAIN, SERVICE_FORCE_CSV_UPDATE)

    assert await hass.config_entries.async_unload(second_entry.entry_id)
    await hass.async_block_till_done()
    assert "csv_manager" not in hass.data[DOMAIN]
    assert "csv_update_listener" not in hass.data[DOMAIN]
    assert not hass.services.has_service(DOMAIN, SERVICE_FORCE_CSV_UPDATE)
    assert not hass.services.has_service(DOMAIN, SERVICE_CLEAR_CACHE)
    assert not hass.services.has_service(DOMAIN, SERVICE_COMPARE_STATIONS)
    assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH_PRICES)
    assert not hass.services.has_service(DOMAIN, SERVICE_SEARCH_REGISTRY)
    unloaded_states = {
        entity_id: hass.states.get(entity_id).state
        for entity_id in entity_ids_after_reload
        if hass.states.get(entity_id) is not None
    }
    assert unloaded_states == {
        entity_id: "unavailable" for entity_id in entity_ids_after_reload
    }
    assert fetch_station_data.await_count >= 3


async def test_refresh_button_is_translated_and_targets_its_station(hass, monkeypatch) -> None:
    """Register a native device action and exercise it through HA services."""
    from homeassistant.helpers import device_registry as dr

    fetch = AsyncMock(return_value=_station_payload())
    monkeypatch.setattr("custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data", fetch)
    monkeypatch.setattr(CSVStationManager, "is_data_available", lambda self: True)
    monkeypatch.setattr(CSVStationManager, "get_station_by_id", lambda self, station_id: {})
    entry = MockConfigEntry(domain=DOMAIN, title="Station", unique_id=f"station_{STATION_ID}", data={CONF_STATION_ID: STATION_ID})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{STATION_ID}_refresh_prices")
    assert entity_id is not None
    entity = registry.async_get(entity_id)
    device = dr.async_get(hass).async_get(entity.device_id)
    assert (DOMAIN, STATION_ID) in device.identifiers
    assert hass.states.get(entity_id).attributes["friendly_name"].endswith("Refresh prices")
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    refresh = AsyncMock()
    monkeypatch.setattr(coordinator, "async_request_refresh", refresh)
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    refresh.assert_awaited_once()
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != "unavailable"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("button", DOMAIN, f"{STATION_ID}_refresh_prices") == entity_id
    assert hass.states.get(entity_id).state != "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
