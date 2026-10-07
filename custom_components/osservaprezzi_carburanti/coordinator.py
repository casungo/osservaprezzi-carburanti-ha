from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import logging
from datetime import datetime, timezone, tzinfo
from math import ceil, isfinite
from typing import cast

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import fetch_station_data
from .const import (
    CONF_STATION_ID,
    DOMAIN,
)
from .csv_manager import CSVStationManager
from .data_helpers import parse_coordinate
from .models import ProcessedFuel, ProcessedPayload, ProcessedStationInfo, StationPayload

_LOGGER = logging.getLogger(__name__)

RETRY_DELAYS: tuple[int, ...] = (30, 60, 120)


class CarburantiDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinate API and CSV enrichment updates for a single station."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        csv_manager: CSVStationManager,
    ) -> None:
        """Initialize the coordinator."""
        self.station_not_found = False
        self.last_refresh_from_cache = False
        self._store: Store[ProcessedPayload] = Store(
            hass,
            1,
            f"{DOMAIN}.{entry.entry_id}.data",
            atomic_writes=True,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.unique_id or entry.entry_id}",
            update_interval=None,
        )
        self.config_entry = entry
        self.csv_manager = csv_manager

    async def async_restore(self) -> None:
        """Restore the last successful station payload before entity setup."""
        self.last_refresh_from_cache = False
        try:
            data = await self._store.async_load()
        except Exception as err:
            _LOGGER.warning(
                "Could not restore station %s data: %s",
                self.config_entry.data[CONF_STATION_ID],
                err,
            )
            return
        if self._is_valid_cached_data(data):
            self.data = cast(ProcessedPayload, data)
            _LOGGER.info(
                "Restored cached station payload for %s",
                self.config_entry.data[CONF_STATION_ID],
            )

    def _is_valid_cached_data(self, data: object) -> bool:
        """Return whether persisted data matches the configured station contract."""
        if not isinstance(data, dict):
            return False
        station_info = data.get("station_info")
        configured_id = self.config_entry.data.get(CONF_STATION_ID)
        if not isinstance(station_info, dict):
            return False
        response_id = station_info.get("id")
        if (
            isinstance(response_id, bool)
            or not isinstance(response_id, (int, str))
            or isinstance(configured_id, bool)
            or not isinstance(configured_id, (int, str))
            or str(response_id) != str(configured_id)
        ):
            return False
        fuels = data.get("fuels")
        opening_hours = data.get("opening_hours")
        services = data.get("services")
        return (
            isinstance(fuels, dict)
            and all(isinstance(key, str) and isinstance(value, dict) for key, value in fuels.items())
            and isinstance(services, list)
            and all(
                isinstance(item, (dict, int, str)) and not isinstance(item, bool)
                for item in services
            )
            and isinstance(opening_hours, list)
            and all(isinstance(item, dict) for item in opening_hours)
            and isinstance(data.get("last_update"), str)
        )

    async def _async_update_data(self) -> ProcessedPayload:
        """Fetch and enrich the latest station payload."""
        self.station_not_found = False
        if not self.csv_manager.is_data_available():
            _LOGGER.info("Initializing CSV station data")
            if not await self.csv_manager.async_initialize():
                _LOGGER.warning(
                    "CSV station data initialization failed; continuing without CSV enrichment"
                )

        data = await self._async_fetch_station_data()
        if data is not self.data:
            try:
                await self._store.async_save(data)
            except Exception as err:
                _LOGGER.warning(
                    "Could not persist station %s data: %s",
                    self.config_entry.data[CONF_STATION_ID],
                    err,
                )
        return data

    async def _async_fetch_station_data(self) -> ProcessedPayload:
        """Fetch station data with retry handling."""
        station_id = self.config_entry.data[CONF_STATION_ID]
        last_err: Exception | None = None
        self.station_not_found = False
        self.last_refresh_from_cache = False

        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                data = await fetch_station_data(self.hass, station_id)
                processed_data = self._process_station_data(data)
                self.last_refresh_from_cache = False
                return processed_data
            except aiohttp.ClientResponseError as err:
                if err.status == 404:
                    self.station_not_found = True
                    _LOGGER.error("Station with ID %s not found", station_id)
                    raise UpdateFailed(f"Station with ID {station_id} not found") from err
                last_err = err
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                last_err = err

            if attempt < len(RETRY_DELAYS):
                delay = self._get_retry_delay(last_err, RETRY_DELAYS[attempt])
                _LOGGER.warning(
                    "Attempt %d/%d failed for station %s, retrying in %ds: %s",
                    attempt + 1,
                    len(RETRY_DELAYS) + 1,
                    station_id,
                    delay,
                    last_err,
                )
                await asyncio.sleep(delay)

        if self.data and self._is_transient_error(last_err):
            _LOGGER.warning(
                "Keeping last known data for station %s after transient update failure: %s",
                station_id,
                last_err,
            )
            self.last_refresh_from_cache = True
            return self.data

        _LOGGER.error(
            "All %d attempts failed for station %s: %s",
            len(RETRY_DELAYS) + 1,
            station_id,
            last_err,
        )
        raise UpdateFailed(
            f"Error fetching station data after {len(RETRY_DELAYS) + 1} attempts: {last_err}"
        )

    @staticmethod
    def _get_retry_delay(err: Exception | None, default_delay: int) -> int:
        """Return the retry delay, preferring Retry-After when available."""
        if isinstance(err, aiohttp.ClientResponseError) and err.status == 429 and err.headers:
            retry_after = err.headers.get("Retry-After")
            if retry_after:
                try:
                    seconds = float(retry_after)
                except (TypeError, ValueError, OverflowError):
                    seconds = None
                if seconds is not None and isfinite(seconds):
                    parsed_delay = int(seconds)
                    if parsed_delay > 0:
                        return parsed_delay
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    now = dt_util.now()
                    if now.tzinfo is None:
                        now = now.replace(tzinfo=timezone.utc)
                    delay = (
                        retry_at.astimezone(timezone.utc)
                        - now.astimezone(timezone.utc)
                    ).total_seconds()
                except (TypeError, ValueError, OverflowError, IndexError):
                    return default_delay
                if isfinite(delay) and delay > 0:
                    return ceil(delay)
        return default_delay

    @staticmethod
    def _is_transient_error(err: Exception | None) -> bool:
        """Return True for recoverable request failures."""
        if isinstance(err, asyncio.TimeoutError):
            return True
        if isinstance(err, aiohttp.ClientResponseError):
            return err.status != 404
        return isinstance(err, aiohttp.ClientError)

    def _get_station_coordinates(self, station_id: str | None) -> dict[str, float | str] | None:
        """Get station coordinates using CSV data only."""
        if not station_id:
            _LOGGER.warning("No station ID provided for coordinate lookup")
            return None

        csv_station = self.csv_manager.get_station_by_id(str(station_id))
        if not csv_station:
            _LOGGER.warning("Station %s not found in CSV data", station_id)
            return None

        latitude = csv_station.get("latitude")
        longitude = csv_station.get("longitude")
        if latitude is None or longitude is None:
            _LOGGER.warning("Station %s found in CSV but missing coordinates", station_id)
            return None

        parsed_latitude = parse_coordinate(latitude, -90, 90)
        parsed_longitude = parse_coordinate(longitude, -180, 180)
        if parsed_latitude is None or parsed_longitude is None:
            _LOGGER.warning("Station %s has invalid CSV coordinates", station_id)
            return None

        _LOGGER.debug(
            "Found coordinates for station %s: %s, %s",
            station_id,
            parsed_latitude,
            parsed_longitude,
        )
        return {
            "latitude": parsed_latitude,
            "longitude": parsed_longitude,
            "source": "csv",
        }

    def _parse_iso_datetime(self, datetime_str: str | None) -> str | None:
        """Normalize an ISO datetime string for entity attributes."""
        if not isinstance(datetime_str, str) or not datetime_str:
            return None

        parsed_dt = dt_util.parse_datetime(datetime_str)
        if parsed_dt is None:
            try:
                parsed_dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                _LOGGER.warning("Failed to parse datetime: %s", datetime_str)
                return None

        if parsed_dt.tzinfo is None:
            local_timezone = dt_util.get_default_time_zone()
            if not isinstance(local_timezone, tzinfo):
                local_timezone = timezone.utc
            parsed_dt = parsed_dt.replace(tzinfo=local_timezone)
        return parsed_dt.replace(microsecond=0).isoformat()

    def _process_station_data(self, data: StationPayload) -> ProcessedPayload:
        """Process the raw data from a single station API call."""
        station_id = data.get("id")
        coordinates = self._get_station_coordinates(str(station_id) if station_id is not None else None)
        csv_station = self.csv_manager.get_station_by_id(str(station_id)) if station_id is not None else None
        now_iso = dt_util.now().replace(microsecond=0).isoformat()

        station_info: ProcessedStationInfo = {
            "id": station_id,
            "name": data.get("name"),
            "nomeImpianto": data.get("nomeImpianto"),
            "address": data.get("address"),
            "brand": data.get("brand"),
            "company": data.get("company"),
            "phoneNumber": data.get("phoneNumber"),
            "email": data.get("email"),
            "website": data.get("website"),
            "latitude": cast(float, coordinates["latitude"]) if coordinates else None,
            "longitude": cast(float, coordinates["longitude"]) if coordinates else None,
            "operator": csv_station.get("operator") if csv_station else None,
            "station_type": csv_station.get("station_type") if csv_station else None,
            "municipality": csv_station.get("municipality") if csv_station else None,
            "province": csv_station.get("province") if csv_station else None,
            "coordinate_source": cast(str, coordinates.get("source")) if coordinates else None,
        }
        processed_data = cast(
            ProcessedPayload,
            {
                "station_info": station_info,
                "fuels": {},
                "services": data.get("services", []),
                "opening_hours": data.get("orariapertura", []),
                "last_update": now_iso,
            },
        )

        for fuel in data.get("fuels", []):
            fuel_name = fuel.get("name", "Unknown")
            service_type = "self" if fuel.get("isSelf") else "servito"
            fuel_key = f"{fuel_name}_{service_type}"
            new_price = fuel.get("price")
            existing_fuel = (self.data or {}).get("fuels", {}).get(fuel_key)
            if existing_fuel and new_price == existing_fuel.get("price"):
                previous_price = existing_fuel.get("previous_price")
                price_changed_at = existing_fuel.get("price_changed_at")
            else:
                previous_price = existing_fuel.get("price") if existing_fuel else None
                price_changed_at = now_iso if previous_price is not None else None

            processed_fuel: ProcessedFuel = {
                "price": new_price,
                "last_update": self._parse_iso_datetime(fuel.get("insertDate")),
                "validity_date": self._parse_iso_datetime(fuel.get("validityDate")),
                "fuel_id": fuel.get("fuelId"),
                "is_self": fuel.get("isSelf"),
                "service_area_id": fuel.get("serviceAreaId"),
                "previous_price": previous_price,
                "price_changed_at": price_changed_at,
            }
            processed_data["fuels"][fuel_key] = processed_fuel

        return processed_data

    async def async_force_csv_update(self) -> bool:
        """Force an immediate CSV update."""
        _LOGGER.info("Forcing immediate CSV data update")
        return await self.csv_manager.async_update_csv_data(force_update=True)
