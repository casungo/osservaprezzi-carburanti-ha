"""Integration-wide actions for station prices and registry maintenance."""
from __future__ import annotations

from datetime import timedelta
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

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
from .data_helpers import fuel_display_name, station_display_name
from .discovery import _haversine_distance_km, find_nearby_stations, find_stations_by_area
from .entity import _find_schedule_for_day, _schedule_intervals_for_date
from .price_metadata import price_metadata

_LOGGER = logging.getLogger(__name__)
_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"

_COMPARE_STATIONS_SCHEMA = vol.Schema(
    {
        vol.Optional("fuel_type", default=""): str,
        vol.Optional("service_mode", default="any"): vol.In(["any", "self", "served", "servito"]),
        vol.Optional("exclude_stale", default=False): bool,
        vol.Optional("only_open", default=False): bool,
        vol.Optional("liters", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("sort_by", default="price"): vol.In(["price", "distance"]),
        vol.Optional("latitude"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Optional("longitude"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
        vol.Optional("zone"): cv.entity_id,
    }
)
_REFRESH_PRICES_SCHEMA = vol.Schema(
    {
        vol.Optional("station_ids"): vol.All(
            cv.ensure_list,
            [cv.string],
        ),
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.string]),
    }
)
_SEARCH_REGISTRY_SCHEMA = vol.Schema(
    {
        vol.Optional("latitude"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Optional("longitude"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
        vol.Optional("radius_km", default=5.0): vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False)),
        vol.Optional("zone"): cv.entity_id,
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

def _is_station_open(opening_hours: list[dict[str, Any]]) -> bool:
    """Return True if a station is currently open according to its opening hours."""
    if not opening_hours:
        return False
    now = dt_util.now()
    for day_offset in (-1, 0):
        local_date = now.date() + timedelta(days=day_offset)
        schedule = _find_schedule_for_day(opening_hours, local_date.weekday() + 1, local_date)
        if any(
            opens_at <= now < closes_at
            for opens_at, closes_at in _schedule_intervals_for_date(schedule, local_date, now.tzinfo)
        ):
            return True
    return False


def _resolve_origin_coordinates(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[float, float] | None:
    """Resolve optional origin coordinates from zone or explicit latitude/longitude."""
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    zone = data.get("zone")
    if zone:
        state = hass.states.get(zone)
        if state is None:
            raise HomeAssistantError("Zone not found")
        latitude = state.attributes.get("latitude")
        longitude = state.attributes.get("longitude")
    if zone or latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            raise HomeAssistantError("Both latitude and longitude are required")
        return float(latitude), float(longitude)
    return None


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
                await coordinator.async_request_manual_refresh()
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
        entry_id, primary_coordinator = coordinators[0] if coordinators else ("shared", None)
        try:
            success = await primary_coordinator.async_force_csv_update() if primary_coordinator else await get_shared_csv_manager(hass).async_update_csv_data(force_update=True)
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
        manager = coordinators[0][1].csv_manager if coordinators else get_shared_csv_manager(hass)
        try:
            cleared = await manager.async_clear_cache()
            initialized = cleared and await manager.async_initialize()
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
        call_data = getattr(call, "data", None) or {}
        fuel_filter = str(call_data.get("fuel_type", "")).strip().lower()
        service_mode = str(call_data.get("service_mode", "any")).strip().lower()
        exclude_stale = bool(call_data.get("exclude_stale", False))
        only_open = bool(call_data.get("only_open", False))
        liters = float(call_data.get("liters", 0.0) or 0.0)
        sort_by = str(call_data.get("sort_by", "price"))
        origin = _resolve_origin_coordinates(hass, call_data)

        comparison: dict[str, Any] = {}
        ranking: list[dict[str, Any]] = []
        for entry_id, coordinator in _iter_coordinators():
            if not coordinator.data:
                continue
            station_info = coordinator.data.get("station_info", {})
            station_name = station_display_name(station_info, entry_id)
            stale_hours = getattr(coordinator, "config_entry", None)
            configured_stale_hours = (
                stale_hours.options.get(CONF_PRICE_STALE_HOURS, DEFAULT_PRICE_STALE_HOURS)
                if stale_hours is not None
                else DEFAULT_PRICE_STALE_HOURS
            )
            fuels: dict[str, Any] = {}
            for fuel_key, fuel_info in coordinator.data.get("fuels", {}).items():
                meta = price_metadata(
                    price=fuel_info.get("price"),
                    previous_price=fuel_info.get("previous_price"),
                    last_update=fuel_info.get("last_update"),
                    stale_hours=configured_stale_hours,
                )
                fuels[fuel_key] = {
                    "price": fuel_info.get("price"),
                    "previous_price": fuel_info.get("previous_price"),
                    "price_changed_at": fuel_info.get("price_changed_at"),
                    "is_self": fuel_info.get("is_self"),
                    "last_update": fuel_info.get("last_update"),
                    **meta,
                }
            comparison[entry_id] = {
                "station_name": station_name,
                "station_id": station_info.get("id"),
                "brand": station_info.get("brand"),
                "address": station_info.get("address"),
                "fuels": fuels,
            }

            if only_open and not _is_station_open(coordinator.data.get("opening_hours", [])):
                continue

            distance_km: float | None = None
            if origin is not None:
                st_lat = station_info.get("latitude")
                st_lon = station_info.get("longitude")
                if isinstance(st_lat, (int, float)) and isinstance(st_lon, (int, float)):
                    distance_km = round(
                        _haversine_distance_km(origin[0], origin[1], float(st_lat), float(st_lon)),
                        2,
                    )

            for fuel_key, fuel_entry in fuels.items():
                price = fuel_entry.get("price")
                if not isinstance(price, (int, float)):
                    continue
                is_self = bool(fuel_entry.get("is_self"))
                if service_mode == "self" and not is_self:
                    continue
                if service_mode in ("served", "servito") and is_self:
                    continue
                if exclude_stale and fuel_entry.get("price_is_stale") is True:
                    continue
                fuel_name_raw = fuel_key.rsplit("_", 1)[0]
                display_fuel = fuel_display_name(fuel_name_raw)
                if fuel_filter and fuel_filter not in fuel_key.lower() and fuel_filter not in display_fuel.lower():
                    continue
                row: dict[str, Any] = {
                    "entry_id": entry_id,
                    "station_id": station_info.get("id"),
                    "station_name": station_name,
                    "brand": station_info.get("brand"),
                    "address": station_info.get("address"),
                    "fuel_key": fuel_key,
                    "fuel_name": display_fuel,
                    "is_self": is_self,
                    "price": float(price),
                    "price_is_stale": fuel_entry.get("price_is_stale"),
                }
                if distance_km is not None:
                    row["distance_km"] = distance_km
                ranking.append(row)

        if sort_by == "distance" and origin is not None:
            ranking.sort(
                key=lambda item: (
                    item.get("distance_km") is None,
                    item.get("distance_km", float("inf")),
                    item["price"],
                )
            )
        else:
            ranking.sort(key=lambda item: item["price"])

        if liters > 0 and ranking:
            max_price = max(item["price"] for item in ranking)
            for item in ranking:
                item["estimated_total_eur"] = round(item["price"] * liters, 2)
                item["savings_eur"] = round((max_price - item["price"]) * liters, 2)

        return {"stations": comparison, "ranking": ranking}

    async def _handle_refresh_prices(call: ServiceCall) -> ServiceResponse:
        """Refresh all active stations or a requested station/device/entity subset."""
        raw_station_ids = call.data.get("station_ids")
        raw_device_ids = call.data.get("device_id")
        raw_entity_ids = call.data.get("entity_id")
        coordinators = _iter_coordinators()
        if raw_station_ids is not None or raw_device_ids is not None or raw_entity_ids is not None:
            requested_station_ids = {
                str(station_id).strip()
                for station_id in (raw_station_ids or [])
                if str(station_id).strip()
            }
            requested_entry_ids: set[str] = set()
            if raw_device_ids:
                dev_reg = dr.async_get(hass)
                for dev_id in raw_device_ids:
                    device = dev_reg.async_get(dev_id)
                    if device is not None:
                        requested_entry_ids.update(device.config_entries)
            if raw_entity_ids:
                ent_reg = er.async_get(hass)
                for ent_id in raw_entity_ids:
                    entity_entry = ent_reg.async_get(ent_id)
                    if entity_entry is not None and entity_entry.config_entry_id:
                        requested_entry_ids.add(entity_entry.config_entry_id)
            coordinators = [
                (entry_id, coordinator)
                for entry_id, coordinator in coordinators
                if str(coordinator.config_entry.data.get(CONF_STATION_ID)) in requested_station_ids
                or entry_id in requested_entry_ids
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

        origin = _resolve_origin_coordinates(hass, call.data)
        if origin is not None:
            search = partial(
                find_nearby_stations, snapshot.stations,
                latitude=origin[0], longitude=origin[1],
                radius_km=call.data.get("radius_km", 5.0),
                text_filter=str(call.data.get("query", "")),
                station_type=str(call.data.get("station_type", "")),
                limit=int(call.data.get("limit", 20)),
            )
        else:
            search = partial(
                find_stations_by_area, snapshot.stations,
                municipality=str(call.data.get("municipality", "")),
                province=str(call.data.get("province", "")),
                text_filter=str(call.data.get("query", "")),
                station_type=str(call.data.get("station_type", "")),
                limit=int(call.data.get("limit", 20)),
            )
        candidates = await hass.async_add_executor_job(search)
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
        (SERVICE_COMPARE_STATIONS, _handle_compare_stations, {"schema": _COMPARE_STATIONS_SCHEMA, "supports_response": SupportsResponse.ONLY}),
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
