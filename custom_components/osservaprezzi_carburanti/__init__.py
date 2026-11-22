from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform, issue_registry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRON_EXPRESSION,
    CONF_MIGRATED_TO_HUB,
    CONF_PRICE_STALE_HOURS,
    CONF_STATION_ID,
    CONF_TRACKED_FUELS,
    CONF_TRACKED_SERVICE_MODES,
    CSV_UPDATE_INTERVAL,
    DEFAULT_CRON_EXPRESSION,
    DOMAIN,
    HUB_UNIQUE_ID,
    SUBENTRY_TYPE_STATION,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .cron_helper import get_next_run_time
from .csv_manager import (
    CSV_MANAGER_DATA_KEY,
    get_shared_csv_manager,
)
from .runtime import OsservaprezziRuntimeData, StationRuntimeData
from .services import async_register_services as _async_register_services
from .services import async_unregister_services

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
        "nomeImpianto",
        "id",
        "brand",
        "opening_hours",
        "services",
    }
)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration-level services."""
    _async_register_services(hass)
    return True


def _station_not_found_issue_id(subentry_id: str) -> str:
    """Return the repair issue ID for a station."""
    return f"{STATION_NOT_FOUND_ISSUE}_{subentry_id}"


def _async_create_station_not_found_issue(
    hass: HomeAssistant, entry: ConfigEntry, station: StationRuntimeData
) -> None:
    """Create a repair issue identifying the hub and station subentry."""
    subentry = entry.subentries[station.subentry_id]
    issue_registry.async_create_issue(
        hass,
        DOMAIN,
        _station_not_found_issue_id(station.subentry_id),
        is_fixable=True,
        data={"entry_id": entry.entry_id, "subentry_id": station.subentry_id},
        is_persistent=True,
        severity=issue_registry.IssueSeverity.ERROR,
        translation_key=STATION_NOT_FOUND_ISSUE,
        translation_placeholders={"station": subentry.title, "station_id": station.station_id},
    )


def _async_delete_station_not_found_issue(hass: HomeAssistant, subentry_id: str) -> None:
    """Remove a station repair issue."""
    issue_registry.async_delete_issue(hass, DOMAIN, _station_not_found_issue_id(subentry_id))


def _async_create_entry_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coroutine: Any,
    name: str,
) -> asyncio.Task[None]:
    """Create a task owned by the config entry when that API is available."""
    create_task = getattr(entry, "async_create_background_task", None)
    if create_task is not None:
        return cast(asyncio.Task[None], create_task(hass, coroutine, name))
    return hass.async_create_task(coroutine, name)


def _async_start_initial_refresh(
    hass: HomeAssistant,
    station: StationRuntimeData,
    entry: ConfigEntry,
    stop_event: asyncio.Event,
) -> asyncio.Task[None]:
    """Start the initial refresh loop for a config entry."""
    return _async_create_entry_task(
        hass,
        entry,
        _async_initial_refresh(hass, station, entry, stop_event),
        f"{DOMAIN}_{station.subentry_id}_initial_refresh",
    )


async def _async_initial_refresh(
    hass: HomeAssistant,
    station: StationRuntimeData,
    entry: ConfigEntry,
    stop_event: asyncio.Event,
) -> None:
    """Load the first station payload without blocking Home Assistant startup."""
    coordinator = station.coordinator
    while not stop_event.is_set():
        try:
            await coordinator.async_refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Initial refresh failed for station %s", entry.title)

        if coordinator.station_not_found:
            _async_create_station_not_found_issue(hass, entry, station)
            stop_event.set()
            return
        if getattr(coordinator, "last_update_success", False) and not getattr(
            coordinator, "last_refresh_from_cache", False
        ):
            _async_delete_station_not_found_issue(hass, station.subentry_id)
            stop_event.set()
            return

        try:
            await asyncio.wait_for(
                stop_event.wait(), INITIAL_REFRESH_RETRY_INTERVAL.total_seconds()
            )
        except TimeoutError:
            continue


async def _async_cancel_initial_refresh(task: asyncio.Task[None] | None) -> None:
    """Cancel and drain the initial refresh task during unload."""
    if task is None or task.done():
        return

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _async_wait_for_task(task: asyncio.Task[None]) -> None:
    """Drain a task without letting repeated caller cancellation cancel it."""
    while True:
        try:
            await asyncio.shield(task)
            return
        except asyncio.CancelledError:
            if task.done():
                task.result()
                return


async def _async_release_stations(stations: dict[str, StationRuntimeData]) -> None:
    """Cancel callbacks and close each station coordinator."""
    released = list(stations.values())
    stations.clear()
    for station in released:
        if station.listener is not None:
            station.listener()
        if station.coordinator_listener is not None:
            station.coordinator_listener()
        await _async_cancel_initial_refresh(station.initial_refresh_task)
        await station.coordinator.async_shutdown()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a hub and its station coordinators."""
    if entry.data.get(CONF_MIGRATED_TO_HUB):
        return True
    _async_register_services(hass)
    domain_data = hass.data.setdefault(DOMAIN, {})
    csv_manager = get_shared_csv_manager(hass)
    if domain_data.get(_CSV_UPDATE_LISTENER) is None:

        async def _async_csv_update_callback(now: datetime) -> None:
            if not await csv_manager.async_periodic_update():
                _LOGGER.warning("Periodic CSV update failed")
                last_update = csv_manager.registry_status().get("last_update")
                updated = dt_util.parse_datetime(last_update) if last_update else None
                if updated is None or dt_util.now() - updated > timedelta(days=7):
                    issue_registry.async_create_issue(
                        hass,
                        DOMAIN,
                        "csv_registry_stale",
                        is_fixable=False,
                        severity=issue_registry.IssueSeverity.WARNING,
                        translation_key="csv_registry_stale",
                    )
            else:
                issue_registry.async_delete_issue(hass, DOMAIN, "csv_registry_stale")

        domain_data[_CSV_UPDATE_LISTENER] = async_track_time_interval(
            hass,
            _async_csv_update_callback,
            timedelta(hours=CSV_UPDATE_INTERVAL),
        )
    stations: dict[str, StationRuntimeData] = {}
    entry.runtime_data = OsservaprezziRuntimeData(stations=stations)
    domain_data[entry.entry_id] = {"stations": stations}

    async def _setup_station(subentry: ConfigSubentry) -> None:
        station_id = str(subentry.data[CONF_STATION_ID])
        # The coordinator still consumes the former entry interface. Each station
        # needs an independent storage key until it accepts subentries directly.
        station_entry = cast(
            ConfigEntry,
            SimpleNamespace(
                entry_id=subentry.subentry_id,
                unique_id=station_id,
                title=subentry.title,
                data=subentry.data,
                options=subentry.data,
            ),
        )
        coordinator = CarburantiDataUpdateCoordinator(hass, station_entry, csv_manager)
        station = StationRuntimeData(subentry.subentry_id, station_id, coordinator)
        stations[subentry.subentry_id] = station
        await coordinator.async_restore()
        cron = subentry.data.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
        stop_event = asyncio.Event()

        def schedule() -> None:
            if subentry.subentry_id not in stations:
                return
            next_run = get_next_run_time(cron)
            station.listener = async_track_point_in_utc_time(
                hass, refresh, dt_util.as_utc(next_run)
            )

        async def refresh(now: datetime) -> None:
            try:
                await coordinator.async_request_refresh()
            finally:
                schedule()

        @callback
        def refresh_result() -> None:
            if coordinator.station_not_found:
                _async_create_station_not_found_issue(hass, entry, station)
                stop_event.set()
            elif coordinator.last_update_success and not coordinator.last_refresh_from_cache:
                _async_delete_station_not_found_issue(hass, station.subentry_id)
                stop_event.set()

        schedule()
        station.coordinator_listener = coordinator.async_add_listener(refresh_result)
        station.initial_refresh_task = _async_start_initial_refresh(
            hass, station, entry, stop_event
        )

    platforms_loaded: list[Platform] = []
    try:
        for subentry in entry.subentries.values():
            if subentry.subentry_type == SUBENTRY_TYPE_STATION:
                await _setup_station(subentry)
        _async_cleanup_legacy_entity_registry(hass, entry)
        existing = {id(platform) for platform in entity_platform.async_get_platforms(hass, DOMAIN)}
        setup_task = hass.async_create_task(
            hass.config_entries.async_forward_entry_setups(entry, PLATFORMS),
            f"{DOMAIN}_{entry.entry_id}_platform_setup",
        )
        try:
            await asyncio.shield(setup_task)
        except BaseException:
            with contextlib.suppress(BaseException):
                await _async_wait_for_task(setup_task)
            platforms_loaded = [
                cast(Platform, platform.domain)
                for platform in entity_platform.async_get_platforms(hass, DOMAIN)
                if id(platform) not in existing and platform.config_entry is entry
            ]
            raise
    except BaseException:

        async def rollback() -> None:
            try:
                if platforms_loaded and not await hass.config_entries.async_unload_platforms(
                    entry, platforms_loaded
                ):
                    _LOGGER.error(
                        "Failed to unload platforms after setup failure for %s", entry.title
                    )
            finally:
                domain_data.pop(entry.entry_id, None)
                await _async_release_stations(stations)
                del entry.runtime_data
                _async_remove_csv_owner_if_unused(hass)

        cleanup_task = hass.async_create_task(rollback(), f"{DOMAIN}_{entry.entry_id}_rollback")
        try:
            await _async_wait_for_task(cleanup_task)
        except BaseException:
            _LOGGER.exception("Failed to roll back setup for %s", entry.title)
        raise
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


def _async_cleanup_legacy_entity_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean stale entity registry data left by previous releases."""
    station_ids = {
        str(subentry.data[CONF_STATION_ID])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_STATION and CONF_STATION_ID in subentry.data
    }
    entity_registry = er.async_get(hass)
    removed_unique_ids = {
        f"{station_id}_{suffix}"
        for station_id in station_ids
        for suffix in _LEGACY_REMOVED_ENTITY_UNIQUE_ID_SUFFIXES
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
        if unique_id.startswith(("market_cheapest_", "market_average_")):
            continue
        if not any(unique_id.startswith(f"{station_id}_") for station_id in station_ids):
            entity_registry.async_remove(entity_id)
            continue

        if entity_id.startswith("sensor.") and any(
            unique_id.startswith(f"{station_id}_service_") for station_id in station_ids
        ):
            entity_registry.async_remove(entity_id)
            continue

        if unique_id in removed_unique_ids:
            entity_registry.async_remove(entity_id)
            continue

        registry_name = getattr(entity_entry, "name", None)
        if isinstance(registry_name, str) and registry_name in _LEGACY_DEFAULT_ENTITY_NAMES:
            entity_registry.async_update_entity(entity_id, name=None)

    device_registry = dr.async_get(hass)
    expected_identifiers = {(DOMAIN, station_id) for station_id in station_ids}
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identifiers: set[tuple[str, str]] = getattr(device, "identifiers", set())
        if identifiers & expected_identifiers or (DOMAIN, "market_summary") in identifiers:
            continue
        if any(
            isinstance(item, tuple)
            and len(item) == 2
            and item[0] == DOMAIN
            and item[1] not in station_ids
            for item in identifiers
        ):
            device_registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def _async_remove_csv_owner_if_unused(hass: HomeAssistant) -> bool:
    """Remove registry-wide resources when no config entries remain."""
    domain_data = hass.data.get(DOMAIN, {})
    if any(isinstance(value, dict) and "stations" in value for value in domain_data.values()):
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

    if config_entry.version == 2:
        station_id = str(config_entry.data[CONF_STATION_ID])
        station_title = config_entry.title
        station_data = {CONF_STATION_ID: station_id}
        for key in (
            CONF_CRON_EXPRESSION,
            CONF_PRICE_STALE_HOURS,
            CONF_TRACKED_FUELS,
            CONF_TRACKED_SERVICE_MODES,
        ):
            if key in config_entry.options:
                station_data[key] = config_entry.options[key]
            elif key in config_entry.data:
                station_data[key] = config_entry.data[key]

        hub = next(
            (
                entry
                for entry in hass.config_entries.async_entries(DOMAIN)
                if entry.entry_id != config_entry.entry_id and entry.unique_id == HUB_UNIQUE_ID
            ),
            None,
        )
        first_entry = hub is None
        if hub is None:
            hub = config_entry
            hass.config_entries.async_update_entry(
                hub,
                version=3,
                unique_id=HUB_UNIQUE_ID,
                title="Osservaprezzi Carburanti",
                data={},
                options={},
            )
        subentry = next(
            (
                item
                for item in hub.subentries.values()
                if item.subentry_type == SUBENTRY_TYPE_STATION and item.unique_id == station_id
            ),
            None,
        )
        if subentry is None:
            subentry = ConfigSubentry(
                data=MappingProxyType(station_data),
                subentry_type=SUBENTRY_TYPE_STATION,
                title=station_title,
                unique_id=station_id,
            )
            hass.config_entries.async_add_subentry(hub, subentry)

        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(entity_registry, config_entry.entry_id):
            if entity.unique_id.startswith(("market_cheapest_", "market_average_")):
                if not first_entry:
                    entity_registry.async_update_entity(
                        entity.entity_id,
                        config_entry_id=hub.entry_id,
                        config_subentry_id=None,
                    )
                continue
            entity_registry.async_update_entity(
                entity.entity_id,
                config_entry_id=hub.entry_id,
                config_subentry_id=subentry.subentry_id,
            )
        device_registry = dr.async_get(hass)
        entry_devices = dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)
        device = next(
            (item for item in entry_devices if (DOMAIN, station_id) in item.identifiers),
            None,
        )
        if device is not None:
            if hasattr(device, "config_entry_id"):
                device_registry.async_update_device(
                    device.id,
                    new_config_entry_id=hub.entry_id,
                    new_config_subentry_id=subentry.subentry_id,
                )
            else:
                device_registry.async_update_device(
                    device.id,
                    add_config_entry_id=hub.entry_id,
                    add_config_subentry_id=subentry.subentry_id,
                    remove_config_entry_id=config_entry.entry_id,
                    remove_config_subentry_id=None,
                )
        if not first_entry:
            market_device = next(
                (item for item in entry_devices if (DOMAIN, "market_summary") in item.identifiers),
                None,
            )
            if market_device is not None:
                device_registry.async_update_device(
                    market_device.id,
                    add_config_entry_id=hub.entry_id,
                    remove_config_entry_id=config_entry.entry_id,
                )

        old_store: Store[Any] = Store(hass, 1, f"{DOMAIN}.{config_entry.entry_id}.data")
        station_store: Store[Any] = Store(hass, 1, f"{DOMAIN}.{subentry.subentry_id}.data")
        try:
            old_cache = await old_store.async_load()
            if old_cache is not None and await station_store.async_load() is None:
                await station_store.async_save(old_cache)
        except Exception:
            _LOGGER.exception("Could not migrate cached payload for station %s", station_id)

        if not first_entry:
            hass.config_entries.async_update_entry(
                config_entry,
                version=3,
                data={CONF_MIGRATED_TO_HUB: True},
                options={},
                unique_id=None,
            )
            if getattr(hub, "state", None) not in (None, ConfigEntryState.NOT_LOADED):
                hass.config_entries.async_schedule_reload(hub.entry_id)
            hass.async_create_task(hass.config_entries.async_remove(config_entry.entry_id))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the hub and its station resources."""
    if entry.data.get(CONF_MIGRATED_TO_HUB):
        return True
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    stations = entry.runtime_data.stations
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    await _async_release_stations(stations)
    del entry.runtime_data
    if _async_remove_csv_owner_if_unused(hass):
        async_unregister_services(hass)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove repair issues and persisted storage owned by a deleted config entry."""
    for subentry in entry.subentries.values():
        _async_delete_station_not_found_issue(hass, subentry.subentry_id)
        await Store(hass, 1, f"{DOMAIN}.{subentry.subentry_id}.data").async_remove()
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.data").async_remove()


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry when options change."""
    _LOGGER.info("Reloading entry %s to apply new cron schedule", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
