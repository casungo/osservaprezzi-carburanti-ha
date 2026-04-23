from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_CRON_EXPRESSION,
    DEFAULT_CRON_EXPRESSION,
    SERVICE_FORCE_CSV_UPDATE,
    SERVICE_CLEAR_CACHE,
    SERVICE_COMPARE_STATIONS,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .cron_helper import get_next_run_time

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CarburantiDataUpdateCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        raise
    
    cron_expression = entry.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
    _LOGGER.info("Setting up cron schedule for %s with expression: %s", entry.title, cron_expression)
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "listener": None,
    }

    def _schedule_next_refresh() -> None:
        try:
            next_run_time = get_next_run_time(cron_expression)
        except Exception as err:
            _LOGGER.error("Failed to compute next cron schedule for %s: %s", entry.title, err)
            raise

        _LOGGER.info(
            "Scheduling next refresh for %s at %s",
            entry.title,
            next_run_time,
        )
        listener: Callable[[], None] = async_track_point_in_utc_time(
            hass,
            _request_refresh,
            dt_util.as_utc(next_run_time),
        )
        hass.data[DOMAIN][entry.entry_id]["listener"] = listener

    async def _request_refresh(now: datetime) -> None:
        _LOGGER.info("Executing scheduled refresh for %s at %s", entry.title, now)
        try:
            await coordinator.async_request_refresh()
        finally:
            _schedule_next_refresh()

    try:
        _schedule_next_refresh()
    except Exception:
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return False

    _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.data.get(_SERVICES_REGISTERED):
        return
    hass.data[_SERVICES_REGISTERED] = True

    async def _handle_force_csv_update(call: ServiceCall) -> None:
        _LOGGER.info("Service force_csv_update triggered")
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if not isinstance(entry_data, dict):
                continue
            coordinator: CarburantiDataUpdateCoordinator = entry_data.get("coordinator")  # type: ignore[assignment]
            if coordinator is None:
                continue
            success = await coordinator.async_force_csv_update()
            if success:
                await coordinator.async_request_refresh()
                _LOGGER.info("CSV update and refresh completed for entry %s", entry_id)
            else:
                _LOGGER.warning("CSV update failed for entry %s", entry_id)

    async def _handle_clear_cache(call: ServiceCall) -> None:
        _LOGGER.info("Service clear_cache triggered")
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if not isinstance(entry_data, dict):
                continue
            coordinator: CarburantiDataUpdateCoordinator = entry_data.get("coordinator")  # type: ignore[assignment]
            if coordinator is None:
                continue
            await coordinator.csv_manager.async_clear_cache()
            await coordinator.csv_manager.async_initialize()
            await coordinator.async_request_refresh()
            _LOGGER.info("Cache cleared and re-initialized for entry %s", entry_id)

    async def _handle_compare_stations(call: ServiceCall) -> ServiceResponse:
        _LOGGER.info("Service compare_stations triggered")
        comparison: dict[str, Any] = {}
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if not isinstance(entry_data, dict):
                continue
            coordinator: CarburantiDataUpdateCoordinator = entry_data.get("coordinator")  # type: ignore[assignment]
            if coordinator is None or not coordinator.data:
                continue
            station_info = coordinator.data.get("station_info", {})
            station_name = station_info.get("nomeImpianto") or station_info.get("name") or entry_id
            fuels: dict[str, Any] = {}
            for fuel_key, fuel_info in coordinator.data.get("fuels", {}).items():
                fuels[fuel_key] = {
                    "price": fuel_info.get("price"),
                    "previous_price": fuel_info.get("previous_price"),
                    "price_changed_at": fuel_info.get("price_changed_at"),
                    "is_self": fuel_info.get("is_self"),
                    "last_update": fuel_info.get("last_update"),
                }
            comparison[entry_id] = {
                "station_name": station_name,
                "station_id": station_info.get("id"),
                "brand": station_info.get("brand"),
                "address": station_info.get("address"),
                "fuels": fuels,
            }
        return {"stations": comparison}

    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_CSV_UPDATE, _handle_force_csv_update,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_CACHE, _handle_clear_cache,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COMPARE_STATIONS, _handle_compare_stations,
        supports_response=SupportsResponse.ONLY,
    )


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.debug("Migrating config entry from version %s", config_entry.version)

    if config_entry.version == 1:
        new_data = config_entry.data.copy()
        new_data.pop("config_type", None)

        hass.config_entries.async_update_entry(config_entry, data=new_data, version=2)
        _LOGGER.info("Migrated config entry from version 1 to 2, removed config_type")

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["listener"]()
        await entry_data["coordinator"].async_shutdown()

        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_CSV_UPDATE)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CACHE)
            hass.services.async_remove(DOMAIN, SERVICE_COMPARE_STATIONS)
            hass.data.pop(_SERVICES_REGISTERED, None)

    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _LOGGER.info("Reloading entry %s to apply new cron schedule", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
