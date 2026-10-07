from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers import issue_registry
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRON_EXPRESSION,
    CONF_STATION_ID,
    DEFAULT_CRON_EXPRESSION,
    CSV_UPDATE_INTERVAL,
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .cron_helper import get_next_run_time
from .csv_manager import (
    CSV_MANAGER_DATA_KEY,
    get_shared_csv_manager,
)
from .services import async_register_services as _async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

_CSV_MANAGER = CSV_MANAGER_DATA_KEY
_CSV_UPDATE_LISTENER = "csv_update_listener"
_INITIAL_REFRESH_TASK = "initial_refresh_task"
_INITIAL_REFRESH_STOP_EVENT = "initial_refresh_stop_event"
_REFRESH_RESULT_LISTENER = "refresh_result_listener"
INITIAL_REFRESH_RETRY_INTERVAL = timedelta(minutes=30)
STATION_NOT_FOUND_ISSUE = "station_not_found"

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


def _station_not_found_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue ID for one config entry."""
    return f"{STATION_NOT_FOUND_ISSUE}_{entry.entry_id}"


def _async_create_station_not_found_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create a translated repair issue for an invalid station ID."""
    issue_registry.async_create_issue(
        hass,
        DOMAIN,
        _station_not_found_issue_id(entry),
        is_fixable=False,
        is_persistent=True,
        severity=issue_registry.IssueSeverity.ERROR,
        translation_key=STATION_NOT_FOUND_ISSUE,
        translation_placeholders={
            "station": entry.title,
            "station_id": str(entry.data.get(CONF_STATION_ID, "")),
        },
    )


def _async_delete_station_not_found_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the repair issue after a successful refresh."""
    issue_registry.async_delete_issue(hass, DOMAIN, _station_not_found_issue_id(entry))


def _async_create_entry_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coroutine: Any,
    name: str,
) -> asyncio.Task[None]:
    """Create a task owned by the config entry when that API is available."""
    create_task = getattr(entry, "async_create_background_task", None)
    if create_task is not None:
        return create_task(hass, coroutine, name)
    return hass.async_create_task(coroutine, name)


def _async_start_initial_refresh(
    hass: HomeAssistant,
    coordinator: CarburantiDataUpdateCoordinator,
    entry: ConfigEntry,
    stop_event: asyncio.Event,
) -> asyncio.Task[None]:
    """Start the initial refresh loop for a config entry."""
    return _async_create_entry_task(
        hass,
        entry,
        _async_initial_refresh(hass, coordinator, entry, stop_event),
        f"{DOMAIN}_{entry.entry_id}_initial_refresh",
    )


async def _async_initial_refresh(
    hass: HomeAssistant,
    coordinator: CarburantiDataUpdateCoordinator,
    entry: ConfigEntry,
    stop_event: asyncio.Event,
) -> None:
    """Load the first station payload without blocking Home Assistant startup."""
    while not stop_event.is_set():
        try:
            await coordinator.async_refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Initial refresh failed for station %s", entry.title)

        if coordinator.station_not_found:
            _async_create_station_not_found_issue(hass, entry)
            stop_event.set()
            return
        if getattr(coordinator, "last_update_success", False):
            _async_delete_station_not_found_issue(hass, entry)
            stop_event.set()
            return

        try:
            await asyncio.wait_for(stop_event.wait(), INITIAL_REFRESH_RETRY_INTERVAL.total_seconds())
        except asyncio.TimeoutError:
            continue


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
    csv_manager = get_shared_csv_manager(hass)

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
    await coordinator.async_restore()

    stop_event = asyncio.Event()
    domain_data[entry.entry_id] = {
        "coordinator": coordinator,
        "listener": None,
        _INITIAL_REFRESH_TASK: None,
        _INITIAL_REFRESH_STOP_EVENT: stop_event,
        _REFRESH_RESULT_LISTENER: None,
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

    @callback
    def _async_handle_refresh_result() -> None:
        if coordinator.station_not_found:
            _async_create_station_not_found_issue(hass, entry)
            stop_event.set()
        elif coordinator.last_update_success:
            _async_delete_station_not_found_issue(hass, entry)
            stop_event.set()

    domain_data[entry.entry_id][_REFRESH_RESULT_LISTENER] = coordinator.async_add_listener(
        _async_handle_refresh_result
    )
    _async_cleanup_legacy_entity_registry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    domain_data[entry.entry_id][_INITIAL_REFRESH_TASK] = _async_start_initial_refresh(
        hass,
        coordinator,
        entry,
        stop_event,
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
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    initial_refresh_was_running = False
    if isinstance(entry_data, dict):
        task = entry_data.get(_INITIAL_REFRESH_TASK)
        initial_refresh_was_running = isinstance(task, asyncio.Task) and not task.done()
        stop_event = entry_data.get(_INITIAL_REFRESH_STOP_EVENT)
        if initial_refresh_was_running and isinstance(stop_event, asyncio.Event):
            stop_event.set()
            await _async_cancel_initial_refresh(task)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        if initial_refresh_was_running and isinstance(entry_data, dict):
            stop_event = entry_data[_INITIAL_REFRESH_STOP_EVENT]
            stop_event.clear()
            entry_data[_INITIAL_REFRESH_TASK] = _async_start_initial_refresh(
                hass,
                entry_data["coordinator"],
                entry,
                stop_event,
            )
        return False

    if isinstance(entry_data, dict):
        listener = entry_data.get("listener")
        if listener is not None:
            listener()
        refresh_result_listener = entry_data.get(_REFRESH_RESULT_LISTENER)
        if refresh_result_listener is not None:
            refresh_result_listener()
        stop_event = entry_data.get(_INITIAL_REFRESH_STOP_EVENT)
        if isinstance(stop_event, asyncio.Event):
            stop_event.set()
        await _async_cancel_initial_refresh(entry_data.get(_INITIAL_REFRESH_TASK))
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        await entry_data["coordinator"].async_shutdown()

        if _async_remove_csv_owner_if_unused(hass):
            async_unregister_services(hass)

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove repair issues owned by a deleted config entry."""
    _async_delete_station_not_found_issue(hass, entry)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry when options change."""
    _LOGGER.info("Reloading entry %s to apply new cron schedule", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
