"""Integration-wide actions for station prices and registry maintenance."""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_PRICE_STALE_HOURS,
    CONF_STATION_ID,
    DEFAULT_PRICE_STALE_HOURS,
    DOMAIN,
    SERVICE_COMPARE_STATIONS,
    SERVICE_CLEAR_CACHE,
    SERVICE_FORCE_CSV_UPDATE,
    SERVICE_REFRESH_PRICES,
    SERVICE_SEARCH_REGISTRY,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .csv_manager import RegistryUnavailableError, get_shared_csv_manager
from .data_helpers import station_display_name
from .discovery import find_stations_by_area
from .price_metadata import price_metadata

_LOGGER = logging.getLogger(__name__)
_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"

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

def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once per Home Assistant instance."""
    if hass.data.get(_SERVICES_REGISTERED):
        return

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
                if not coordinator.last_update_success or coordinator.last_refresh_from_cache:
                    failed_entry_ids.append(entry_id)
                    _LOGGER.warning("Station refresh did not retrieve prices for entry %s after %s", entry_id, action)
                else:
                    _LOGGER.info("%s completed for entry %s", action, entry_id)

        if failed_entry_ids:
            raise HomeAssistantError(
                f"{len(failed_entry_ids)} station refresh(es) failed after {action}",
                translation_domain=DOMAIN,
                translation_key="station_refresh_failed",
                translation_placeholders={"count": str(len(failed_entry_ids))},
            )

    async def _handle_force_csv_update(call: ServiceCall) -> None:
        _LOGGER.info("Service force_csv_update triggered")
        coordinators = _iter_coordinators()
        if not coordinators:
            raise HomeAssistantError("No active Osservaprezzi entries", translation_domain=DOMAIN, translation_key="no_active_entries")

        entry_id, primary_coordinator = coordinators[0]
        try:
            success = await primary_coordinator.async_force_csv_update()
        except Exception as err:
            _LOGGER.exception("CSV update failed for entry %s", entry_id)
            raise HomeAssistantError("Unable to update the station cache", translation_domain=DOMAIN, translation_key="cache_update_failed") from err
        if not success:
            _LOGGER.warning("CSV update failed for entry %s", entry_id)
            raise HomeAssistantError("Unable to update the station cache", translation_domain=DOMAIN, translation_key="cache_update_failed")

        await _async_refresh_coordinators(coordinators, "CSV update")

    async def _handle_clear_cache(call: ServiceCall) -> None:
        _LOGGER.info("Service clear_cache triggered")
        coordinators = _iter_coordinators()
        if not coordinators:
            raise HomeAssistantError("No active Osservaprezzi entries", translation_domain=DOMAIN, translation_key="no_active_entries")

        _, primary_coordinator = coordinators[0]
        try:
            cleared = await primary_coordinator.csv_manager.async_clear_cache()
            initialized = cleared and await primary_coordinator.csv_manager.async_initialize()
        except Exception as err:
            _LOGGER.exception("CSV cache reset failed")
            raise HomeAssistantError("Unable to reset the station cache", translation_domain=DOMAIN, translation_key="cache_reset_failed") from err
        if not cleared:
            _LOGGER.warning("CSV cache clear failed; skipping station refresh")
            raise HomeAssistantError("Unable to reset the station cache", translation_domain=DOMAIN, translation_key="cache_reset_failed")
        if not initialized:
            _LOGGER.warning("Cache cleared but CSV re-initialization failed; skipping station refresh")
            raise HomeAssistantError("Unable to reset the station cache", translation_domain=DOMAIN, translation_key="cache_reset_failed")

        await _async_refresh_coordinators(coordinators, "Cache reset")

    async def _handle_compare_stations(call: ServiceCall) -> ServiceResponse:
        _LOGGER.info("Service compare_stations triggered")
        comparison: dict[str, Any] = {}
        for entry_id, coordinator in _iter_coordinators():
            if not coordinator.data:
                continue
            station_info = coordinator.data.get("station_info", {})
            station_name = station_display_name(station_info, entry_id)
            fuels: dict[str, Any] = {}
            for fuel_key, fuel_info in coordinator.data.get("fuels", {}).items():
                fuels[fuel_key] = {
                    "price": fuel_info.get("price"),
                    "previous_price": fuel_info.get("previous_price"),
                    "price_changed_at": fuel_info.get("price_changed_at"),
                    "is_self": fuel_info.get("is_self"),
                    "last_update": fuel_info.get("last_update"),
                    **price_metadata(
                        price=fuel_info.get("price"),
                        previous_price=fuel_info.get("previous_price"),
                        last_update=fuel_info.get("last_update"),
                        stale_hours=coordinator.config_entry.options.get(
                            CONF_PRICE_STALE_HOURS, DEFAULT_PRICE_STALE_HOURS
                        ),
                    ),
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
            raise HomeAssistantError("No matching active Osservaprezzi entries", translation_domain=DOMAIN, translation_key="no_matching_entries")

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
            raise HomeAssistantError("The station registry is unavailable", translation_domain=DOMAIN, translation_key="registry_unavailable") from err

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

    registrations: tuple[tuple[str, Callable[..., Any], dict[str, Any]], ...] = (
        (SERVICE_FORCE_CSV_UPDATE, _handle_force_csv_update, {}),
        (SERVICE_CLEAR_CACHE, _handle_clear_cache, {}),
        (SERVICE_COMPARE_STATIONS, _handle_compare_stations, {"supports_response": SupportsResponse.ONLY}),
        (SERVICE_REFRESH_PRICES, _handle_refresh_prices, {"schema": _REFRESH_PRICES_SCHEMA, "supports_response": SupportsResponse.OPTIONAL}),
        (SERVICE_SEARCH_REGISTRY, _handle_search_registry, {"schema": _SEARCH_REGISTRY_SCHEMA, "supports_response": SupportsResponse.ONLY}),
    )
    for name, handler, options in registrations:
        hass.services.async_register(DOMAIN, name, handler, **options)
    hass.data[_SERVICES_REGISTERED] = tuple(name for name, _, _ in registrations)


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove actions after the last station unloads."""
    for name in hass.data.pop(_SERVICES_REGISTERED, ()):
        hass.services.async_remove(DOMAIN, name)

