from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRON_EXPRESSION,
    CONF_STATION_ID,
    DEFAULT_CRON_EXPRESSION,
    CSV_UPDATE_INTERVAL,
    DOMAIN,
    SERVICE_COMPARE_STATIONS,
    SERVICE_CLEAR_CACHE,
    SERVICE_FORCE_CSV_UPDATE,
    SERVICE_REFRESH_PRICES,
    SERVICE_SEARCH_REGISTRY,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .cron_helper import get_next_run_time
from .csv_manager import (
    CSV_MANAGER_DATA_KEY,
    CSVStationManager,
    RegistryUnavailableError,
    get_shared_csv_manager,
)
from .discovery import find_stations_by_area

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"
_CSV_MANAGER = CSV_MANAGER_DATA_KEY
_CSV_UPDATE_LISTENER = "csv_update_listener"

_REFRESH_PRICES_SCHEMA = vol.Schema(
    {
        vol.Optional("station_ids"): vol.All(
            cv.ensure_list,
            [str],
        )
    }
)
_SEARCH_REGISTRY_SCHEMA = vol.Schema(
    {
        vol.Optional("query", default=""): str,
        vol.Optional("municipality", default=""): str,
        vol.Optional("province", default=""): str,
        vol.Optional("station_type", default=""): str,
        vol.Optional("limit", default=20): vol.All(
            vol.Coerce(int),
            vol.Range(min=1, max=50),
        ),
    }
)

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


async def _async_initial_refresh(
    coordinator: CarburantiDataUpdateCoordinator,
    entry: ConfigEntry,
) -> None:
    """Load the first station payload without blocking Home Assistant startup."""
    try:
        await coordinator.async_config_entry_first_refresh()
    except asyncio.CancelledError:
        raise
    except ConfigEntryNotReady as err:
        _LOGGER.warning(
            "Initial refresh for station %s was not ready: %s",
            entry.title,
            err,
        )
    except Exception:
        _LOGGER.exception("Initial refresh failed for station %s", entry.title)


async def _async_cancel_initial_refresh(task: asyncio.Task[None] | None) -> None:
    """Cancel and drain the initial refresh task during unload."""
    if task is None or task.done():
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Osservaprezzi Carburanti from a config entry."""
    _async_register_services(hass)

    domain_data = hass.data.setdefault(DOMAIN, {})
    csv_manager = domain_data.get(_CSV_MANAGER)
    if not isinstance(csv_manager, CSVStationManager):
        csv_manager = CSVStationManager(hass)
        domain_data[_CSV_MANAGER] = csv_manager

    if domain_data.get(_CSV_UPDATE_LISTENER) is None:
        async def _async_csv_update_callback(now: datetime) -> None:
            _LOGGER.info("Performing periodic CSV data update at %s", now)
            if not await csv_manager.async_periodic_update():
                _LOGGER.warning("Periodic CSV update failed")

        domain_data[_CSV_UPDATE_LISTENER] = async_track_time_interval(
            hass,
            _async_csv_update_callback,
            timedelta(hours=CSV_UPDATE_INTERVAL),
        )

    coordinator = CarburantiDataUpdateCoordinator(hass, entry, csv_manager)

    domain_data[entry.entry_id] = {
        "coordinator": coordinator,
        "listener": None,
        "initial_refresh_task": None,
    }

    cron_expression = entry.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
    _LOGGER.info("Setting up cron schedule for %s with expression: %s", entry.title, cron_expression)

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
        _async_remove_csv_owner_if_unused(hass)
        return False

    _async_cleanup_legacy_entity_registry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    domain_data[entry.entry_id]["initial_refresh_task"] = hass.async_create_task(
        _async_initial_refresh(coordinator, entry),
        f"{DOMAIN}_{entry.entry_id}_initial_refresh",
    )
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
            entity_registry.async_remove(entity_id)
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


def _async_remove_csv_owner_if_unused(hass: HomeAssistant) -> bool:
    """Remove registry-wide resources when no config entries remain."""
    domain_data = hass.data.get(DOMAIN, {})
    if any(
        isinstance(value, dict) and "coordinator" in value
        for value in domain_data.values()
    ):
        return False

    listener = domain_data.pop(_CSV_UPDATE_LISTENER, None)
    if listener is not None:
        listener()
    domain_data.pop(_CSV_MANAGER, None)
    return True


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

    async def _async_refresh_coordinators(
        coordinators: list[tuple[str, CarburantiDataUpdateCoordinator]],
        action: str,
    ) -> None:
        failed_entry_ids: list[str] = []
        for entry_id, coordinator in coordinators:
            try:
                await coordinator.async_request_refresh()
            except Exception:  # noqa: BLE001 - service must attempt every configured station
                failed_entry_ids.append(entry_id)
                _LOGGER.exception("Station refresh failed for entry %s after %s", entry_id, action)
            else:
                _LOGGER.info("%s completed for entry %s", action, entry_id)

        if failed_entry_ids:
            raise HomeAssistantError(
                f"{action} succeeded, but {len(failed_entry_ids)} station refresh(es) failed"
            )

    async def _handle_force_csv_update(call: ServiceCall) -> None:
        _LOGGER.info("Service force_csv_update triggered")
        coordinators = _iter_coordinators()
        if not coordinators:
            raise HomeAssistantError("No active Osservaprezzi entries")

        entry_id, primary_coordinator = coordinators[0]
        try:
            success = await primary_coordinator.async_force_csv_update()
        except Exception as err:
            _LOGGER.exception("CSV update failed for entry %s", entry_id)
            raise HomeAssistantError("Unable to update the station cache") from err
        if not success:
            _LOGGER.warning("CSV update failed for entry %s", entry_id)
            raise HomeAssistantError("Unable to update the station cache")

        await _async_refresh_coordinators(coordinators, "CSV update")

    async def _handle_clear_cache(call: ServiceCall) -> None:
        _LOGGER.info("Service clear_cache triggered")
        coordinators = _iter_coordinators()
        if not coordinators:
            raise HomeAssistantError("No active Osservaprezzi entries")

        _, primary_coordinator = coordinators[0]
        try:
            cleared = await primary_coordinator.csv_manager.async_clear_cache()
            initialized = cleared and await primary_coordinator.csv_manager.async_initialize()
        except Exception as err:
            _LOGGER.exception("CSV cache reset failed")
            raise HomeAssistantError("Unable to reset the station cache") from err
        if not cleared:
            _LOGGER.warning("CSV cache clear failed; skipping station refresh")
            raise HomeAssistantError("Unable to reset the station cache")
        if not initialized:
            _LOGGER.warning("Cache cleared but CSV re-initialization failed; skipping station refresh")
            raise HomeAssistantError("Unable to reset the station cache")

        await _async_refresh_coordinators(coordinators, "Cache reset")

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

    async def _handle_refresh_prices(call: ServiceCall) -> ServiceResponse:
        """Refresh all active stations or a requested station subset."""
        requested_ids = {
            str(station_id).strip()
            for station_id in call.data.get("station_ids", [])
            if str(station_id).strip()
        }
        coordinators = _iter_coordinators()
        if requested_ids:
            coordinators = [
                (entry_id, coordinator)
                for entry_id, coordinator in coordinators
                if str(coordinator.config_entry.data.get(CONF_STATION_ID)) in requested_ids
            ]
        if not coordinators:
            raise HomeAssistantError("No matching active Osservaprezzi entries")

        await _async_refresh_coordinators(coordinators, "Price refresh")
        refreshed_station_ids = [
            str(coordinator.config_entry.data.get(CONF_STATION_ID))
            for _, coordinator in coordinators
        ]
        return {
            "refreshed_station_ids": refreshed_station_ids,
            "refreshed_count": len(refreshed_station_ids),
        }

    async def _handle_search_registry(call: ServiceCall) -> ServiceResponse:
        """Search the shared official station registry without location data."""
        try:
            snapshot = await get_shared_csv_manager(hass).async_ensure_registry(
                allow_stale=True
            )
        except RegistryUnavailableError as err:
            raise HomeAssistantError("The station registry is unavailable") from err

        candidates = await hass.async_add_executor_job(
            partial(
                find_stations_by_area,
                snapshot.stations,
                municipality=str(call.data.get("municipality", "")),
                province=str(call.data.get("province", "")),
                text_filter=str(call.data.get("query", "")),
                station_type=str(call.data.get("station_type", "")),
                limit=int(call.data.get("limit", 20)),
            )
        )
        configured_station_ids = {
            str(coordinator.config_entry.data.get(CONF_STATION_ID))
            for _, coordinator in _iter_coordinators()
        }
        return {
            "results": [
                {
                    "station_id": candidate.station_id,
                    "name": candidate.name,
                    "brand": candidate.brand,
                    "address": candidate.address,
                    "municipality": candidate.municipality,
                    "province": candidate.province,
                    "station_type": candidate.station_type,
                    "configured": candidate.station_id in configured_station_ids,
                }
                for candidate in candidates
            ],
            "result_count": len(candidates),
            "registry_updated": (
                snapshot.updated_at.isoformat()
                if snapshot.updated_at is not None
                else None
            ),
            "registry_is_stale": snapshot.is_stale,
        }

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_PRICES,
        _handle_refresh_prices,
        schema=_REFRESH_PRICES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_REGISTRY,
        _handle_search_registry,
        schema=_SEARCH_REGISTRY_SCHEMA,
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
        await _async_cancel_initial_refresh(entry_data.get("initial_refresh_task"))
        await entry_data["coordinator"].async_shutdown()

        if _async_remove_csv_owner_if_unused(hass):
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_CSV_UPDATE)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CACHE)
            hass.services.async_remove(DOMAIN, SERVICE_COMPARE_STATIONS)
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_PRICES)
            hass.services.async_remove(DOMAIN, SERVICE_SEARCH_REGISTRY)
            hass.data.pop(_SERVICES_REGISTERED, None)

    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry when options change."""
    _LOGGER.info("Reloading entry %s to apply new cron schedule", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
