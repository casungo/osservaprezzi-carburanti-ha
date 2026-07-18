from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRON_EXPRESSION,
    CONF_STATION_ID,
    DEFAULT_CRON_EXPRESSION,
    DOMAIN,
    SERVICE_COMPARE_STATIONS,
    SERVICE_CLEAR_CACHE,
    SERVICE_FORCE_CSV_UPDATE,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .cron_helper import get_next_run_time

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"

_LEGACY_DEFAULT_ENTITY_NAMES = frozenset(
    {
        "Address",
        "Brand",
        "Company",
        "Food & Beverage",
        "Workshop",
        "Camper/Truck Parking",
        "Camper Dump Station",
        "Children's Area",
        "Disabled Services",
        "Tire Service",
        "Car Wash",
        "EV Charging",
        "Food&Beverage",
        "Name",
        "Osservaprezzi ID",
        "Station ID",
        "Station Name",
        "Location",
        "Next Schedule Change",
        "Open",
        "Email",
        "Phone",
        "Website",
        "Servizi Disponibili",
        "Orari di Apertura",
        "Posizione Stazione",
    }
)

_LEGACY_REMOVED_ENTITY_UNIQUE_ID_SUFFIXES = frozenset(
    {
        "address",
        "opening_hours",
        "services",
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration-level services."""
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Osservaprezzi Carburanti from a config entry."""
    _async_register_services(hass)

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
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if entry_data is None or entry_data.get("coordinator") is not coordinator:
            return

        try:
            next_run_time = get_next_run_time(cron_expression)
        except (ImportError, TypeError, ValueError) as err:
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
        entry_data["listener"] = listener

    async def _request_refresh(now: datetime) -> None:
        _LOGGER.info("Executing scheduled refresh for %s at %s", entry.title, now)
        try:
            await coordinator.async_request_refresh()
        finally:
            _schedule_next_refresh()

    try:
        _schedule_next_refresh()
    except (ImportError, TypeError, ValueError):
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return False

    _async_cleanup_legacy_entity_registry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _async_cleanup_legacy_entity_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean stale entity registry data left by previous releases."""
    station_id = getattr(entry, "data", {}).get(CONF_STATION_ID)
    if not station_id:
        return

    entity_registry = er.async_get(hass)
    removed_unique_ids = {
        f"{station_id}_{suffix}" for suffix in _LEGACY_REMOVED_ENTITY_UNIQUE_ID_SUFFIXES
    }

    for entity_entry in list(entity_registry.entities.values()):
        if getattr(entity_entry, "platform", None) != DOMAIN:
            continue
        if getattr(entity_entry, "config_entry_id", None) != entry.entry_id:
            continue

        unique_id = getattr(entity_entry, "unique_id", None)
        entity_id = getattr(entity_entry, "entity_id", None)
        if not isinstance(unique_id, str) or not isinstance(entity_id, str):
            continue
        if not unique_id.startswith(f"{station_id}_"):
            continue

        if entity_id.startswith("sensor.") and unique_id.startswith(f"{station_id}_service_"):
            entity_registry.async_remove(entity_id)
            continue

        if unique_id in removed_unique_ids:
            entity_registry.async_remove(entity_id)
            continue

        registry_name = getattr(entity_entry, "name", None)
        if isinstance(registry_name, str) and registry_name in _LEGACY_DEFAULT_ENTITY_NAMES:
            entity_registry.async_update_entity(entity_id, name=None)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once per Home Assistant instance."""
    if hass.data.get(_SERVICES_REGISTERED):
        return
    hass.data[_SERVICES_REGISTERED] = True

    def _iter_coordinators() -> list[tuple[str, CarburantiDataUpdateCoordinator]]:
        coordinators: list[tuple[str, CarburantiDataUpdateCoordinator]] = []
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if not isinstance(entry_data, dict):
                continue
            coordinator = entry_data.get("coordinator")
            if isinstance(coordinator, CarburantiDataUpdateCoordinator):
                coordinators.append((entry_id, coordinator))
        return coordinators

    async def _handle_force_csv_update(call: ServiceCall) -> None:
        _LOGGER.info("Service force_csv_update triggered")
        coordinators = _iter_coordinators()
        if not coordinators:
            return

        entry_id, primary_coordinator = coordinators[0]
        success = await primary_coordinator.async_force_csv_update()
        if not success:
            _LOGGER.warning("CSV update failed for entry %s", entry_id)
            return

        refresh_targets = [(entry_id, primary_coordinator)]
        for entry_id, coordinator in coordinators[1:]:
            if not await coordinator.csv_manager.async_load_cached_data():
                _LOGGER.warning(
                    "Failed to load refreshed CSV cache for entry %s, trying re-initialization",
                    entry_id,
                )
                if not await coordinator.csv_manager.async_initialize():
                    _LOGGER.warning("Skipping refresh for entry %s because CSV sync failed", entry_id)
                    continue
            refresh_targets.append((entry_id, coordinator))

        for entry_id, coordinator in refresh_targets:
            await coordinator.async_request_refresh()
            _LOGGER.info("CSV update and refresh completed for entry %s", entry_id)

    async def _handle_clear_cache(call: ServiceCall) -> None:
        _LOGGER.info("Service clear_cache triggered")
        coordinators = _iter_coordinators()
        if not coordinators:
            return

        _, primary_coordinator = coordinators[0]
        if not await primary_coordinator.csv_manager.async_clear_cache():
            _LOGGER.warning("CSV cache clear failed; skipping station refresh")
            return
        if not await primary_coordinator.csv_manager.async_initialize():
            _LOGGER.warning("Cache cleared but CSV re-initialization failed; skipping station refresh")
            return

        refresh_targets = [coordinators[0]]
        for entry_id, coordinator in coordinators[1:]:
            if not await coordinator.csv_manager.async_load_cached_data():
                _LOGGER.warning(
                    "Failed to load rebuilt CSV cache for entry %s, trying re-initialization",
                    entry_id,
                )
                if not await coordinator.csv_manager.async_initialize():
                    _LOGGER.warning("Skipping refresh for entry %s because CSV sync failed", entry_id)
                    continue
            refresh_targets.append((entry_id, coordinator))

        for entry_id, coordinator in refresh_targets:
            await coordinator.async_request_refresh()
            _LOGGER.info("Cache cleared and re-initialized for entry %s", entry_id)

    async def _handle_compare_stations(call: ServiceCall) -> ServiceResponse:
        _LOGGER.info("Service compare_stations triggered")
        comparison: dict[str, Any] = {}
        for entry_id, coordinator in _iter_coordinators():
            if not coordinator.data:
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
    """Migrate older config entries to the current schema."""
    _LOGGER.debug("Migrating config entry from version %s", config_entry.version)

    if config_entry.version == 1:
        new_data = config_entry.data.copy()
        new_data.pop("config_type", None)

        hass.config_entries.async_update_entry(config_entry, data=new_data, version=2)
        _LOGGER.info("Migrated config entry from version 1 to 2, removed config_type")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        listener = entry_data.get("listener")
        if listener is not None:
            listener()
        await entry_data["coordinator"].async_shutdown()

        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_CSV_UPDATE)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CACHE)
            hass.services.async_remove(DOMAIN, SERVICE_COMPARE_STATIONS)
            hass.data.pop(_SERVICES_REGISTERED, None)

    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry when options change."""
    _LOGGER.info("Reloading entry %s to apply new cron schedule", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
