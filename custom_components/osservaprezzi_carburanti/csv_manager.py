"""CSV data manager for Osservaprezzi Carburanti station information."""
from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import CSV_UPDATE_INTERVAL, CSV_URL, DEFAULT_HEADERS, DOMAIN

_LOGGER = logging.getLogger(__name__)

CACHE_VERSION = "2.0"
CSV_COLUMNS = {
    "idImpianto": "id",
    "Gestore": "operator",
    "Bandiera": "brand",
    "Tipo Impianto": "station_type",
    "Nome Impianto": "name",
    "Indirizzo": "address",
    "Comune": "municipality",
    "Provincia": "province",
    "Latitudine": "latitude",
    "Longitudine": "longitude",
}


def _load_json_file_sync(path: str) -> dict[str, Any]:
    """Read and decode a JSON document synchronously."""
    with open(path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    if not isinstance(data, dict):
        raise ValueError("Cache root must be an object")
    return data


def _write_json_file_atomic_sync(path: str, data: dict[str, Any]) -> None:
    """Encode and atomically replace a JSON document synchronously."""
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=os.path.dirname(path),
            prefix=f"{DOMAIN}_cache_",
            suffix=".tmp",
        ) as file_handle:
            temp_path = file_handle.name
            json.dump(data, file_handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                os.remove(temp_path)


class CSVStationManager:
    """Manager for CSV station data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the CSV manager."""
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self._stations_cache: dict[str, dict[str, Any]] = {}
        self._last_update: datetime | None = None
        self._csv_etag: str | None = None
        self._csv_last_modified: str | None = None
        self._cache_path = hass.config.path(".storage", f"{DOMAIN}_cache.json")
        self._legacy_csv_paths = (
            hass.config.path("osservaprezzi_stations.csv"),
            hass.config.path(".storage", f"{DOMAIN}_stations.csv"),
        )
        self._detected_separator = "|"
        self._operation_lock = asyncio.Lock()
        self._cache_generation = 0
        self._initialized = False

    async def _async_migrate_legacy_files(self) -> None:
        """Migrate the legacy JSON cache and remove obsolete raw CSV files."""
        old_cache = self.hass.config.path("osservaprezzi_cache.json")
        old_exists = await self.hass.async_add_executor_job(os.path.exists, old_cache)
        if old_exists:
            new_exists = await self.hass.async_add_executor_job(os.path.exists, self._cache_path)
            if not new_exists:
                try:
                    await self.hass.async_add_executor_job(
                        os.replace, old_cache, self._cache_path
                    )
                    _LOGGER.info("Migrated legacy JSON station cache")
                except OSError as err:
                    _LOGGER.warning("Failed to migrate legacy JSON station cache: %s", err)

        await self._async_remove_legacy_csv_files()

    async def _async_remove_legacy_csv_files(self) -> None:
        """Best-effort remove raw CSV caches created by released versions."""
        for file_path in self._legacy_csv_paths:
            try:
                exists = await self.hass.async_add_executor_job(os.path.exists, file_path)
                if not exists:
                    continue
                await self.hass.async_add_executor_job(os.remove, file_path)
                _LOGGER.info("Removed legacy raw station cache")
            except OSError as err:
                _LOGGER.warning("Failed to remove legacy raw station cache: %s", err)

    async def async_update_csv_data(self, force_update: bool = False) -> bool:
        """Update CSV data from the remote source."""
        async with self._operation_lock:
            return await self._async_update_csv_data(force_update)

    async def _async_update_csv_data(self, force_update: bool = False) -> bool:
        """Update CSV data while the operation lock is held."""
        try:
            now = dt_util.now()
            if (
                not force_update
                and self._last_update
                and now - self._last_update < timedelta(hours=CSV_UPDATE_INTERVAL)
            ):
                _LOGGER.debug("CSV data is recent, skipping update")
                return True
            headers = self._build_csv_request_headers(force_update)
            cache_generation = self._cache_generation
            _LOGGER.info("Downloading station data from CSV: %s", CSV_URL)

            async with self.session.get(
                CSV_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status == 304:
                    if cache_generation != self._cache_generation:
                        _LOGGER.info("Ignoring CSV 304 response after cache was cleared")
                        return False
                    data = self._build_cache_data(
                        stations_cache=self._stations_cache,
                        last_update=now,
                        separator=self._detected_separator,
                        csv_etag=self._csv_etag,
                        csv_last_modified=self._csv_last_modified,
                    )
                    if not await self._async_save_cache_data(data):
                        _LOGGER.error("Failed to persist CSV 304 refresh metadata")
                        return False
                    self._last_update = now
                    _LOGGER.debug("CSV not modified, keeping cached station data")
                    return True

                if response.status != 200:
                    _LOGGER.error("Failed to download CSV: HTTP %s", response.status)
                    return False

                content = await response.text()
                csv_etag = response.headers.get("ETag")
                csv_last_modified = response.headers.get("Last-Modified")

            success, separator, stations_cache = await self.hass.async_add_executor_job(
                self._parse_csv_content_to_cache,
                content,
            )
            if not success:
                _LOGGER.error("Failed to parse CSV data")
                return False

            if cache_generation != self._cache_generation:
                _LOGGER.info("Discarding downloaded CSV because cache was cleared")
                return False

            data = self._build_cache_data(
                stations_cache=stations_cache,
                last_update=now,
                separator=separator,
                csv_etag=csv_etag,
                csv_last_modified=csv_last_modified,
            )
            if not await self._async_save_cache_data(data):
                _LOGGER.error("Failed to persist downloaded CSV station data")
                return False
            self._csv_etag = csv_etag
            self._csv_last_modified = csv_last_modified
            self._detected_separator = separator
            self._stations_cache = stations_cache
            self._last_update = now

            _LOGGER.info("Successfully updated CSV station data")
            return True

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.error("Error updating CSV data: %s", err)
            return False

    def _build_csv_request_headers(self, force_update: bool) -> dict[str, str]:
        """Build request headers for the CSV download."""
        headers = {
            **DEFAULT_HEADERS,
            "Accept": "text/csv,application/csv,text/plain,*/*",
        }
        if not force_update:
            if self._csv_etag:
                headers["If-None-Match"] = self._csv_etag
            if self._csv_last_modified:
                headers["If-Modified-Since"] = self._csv_last_modified
        return headers

    def _build_column_indices(self, header_line: str, separator: str) -> dict[str, int]:
        """Build a mapping of internal column names to CSV indexes."""
        headers = [header.strip().strip('"') for header in header_line.split(separator)]

        col_indices: dict[str, int] = {}
        for csv_col, internal_col in CSV_COLUMNS.items():
            try:
                col_indices[internal_col] = headers.index(csv_col)
            except ValueError:
                _LOGGER.warning("Column '%s' not found in CSV", csv_col)
                col_indices[internal_col] = -1
        return col_indices

    def _parse_station_values(
        self,
        values: list[str],
        col_indices: dict[str, int],
    ) -> tuple[str, dict[str, Any]] | None:
        """Parse CSV station fields into the station cache structure."""
        if not values:
            return None

        station_data: dict[str, Any] = {}
        for internal_col, idx in col_indices.items():
            if idx < 0 or idx >= len(values):
                continue

            value = values[idx].strip()
            if internal_col in ("latitude", "longitude"):
                station_data[internal_col] = self._parse_coordinate(value)
            else:
                station_data[internal_col] = value or None

        station_id = station_data.get("id")
        latitude = station_data.get("latitude")
        longitude = station_data.get("longitude")
        if station_id and latitude is not None and longitude is not None:
            return station_id, station_data
        return None

    def _parse_csv_content_to_cache(
        self,
        content: str,
    ) -> tuple[bool, str, dict[str, dict[str, Any]]]:
        """Parse CSV text into a station cache without mutating manager state."""
        text_stream = io.StringIO(content)
        first_line = text_stream.readline()
        header_line = text_stream.readline()
        if not first_line or not header_line:
            _LOGGER.error("CSV file has insufficient data")
            return False, self._detected_separator, {}

        header_line = header_line.rstrip("\r\n")
        separator = self._get_separator(header_line)
        col_indices = self._build_column_indices(header_line, separator)
        stations_cache: dict[str, dict[str, Any]] = {}

        reader = csv.reader(text_stream, delimiter=separator)
        for line_num, values in enumerate(reader, start=3):
            try:
                parsed_station = self._parse_station_values(values, col_indices)
                if parsed_station is None:
                    continue
                station_id, station_data = parsed_station
                stations_cache[station_id] = station_data
            except (IndexError, TypeError, ValueError) as err:
                _LOGGER.warning("Error parsing CSV line %d: %s", line_num, err)

        _LOGGER.info("Parsed %d stations from CSV", len(stations_cache))
        return True, separator, stations_cache

    @staticmethod
    def _get_separator(header_line: str) -> str:
        """Return the detected CSV separator without mutating manager state."""
        pipe_count = header_line.count("|")
        semicolon_count = header_line.count(";")

        if pipe_count > semicolon_count:
            separator = "|"
            _LOGGER.debug("Detected pipe (|) separator in CSV file")
        elif semicolon_count > pipe_count:
            separator = ";"
            _LOGGER.debug("Detected semicolon (;) separator in CSV file")
        else:
            separator = "|"
            _LOGGER.debug("Separator count equal or none detected, defaulting to pipe (|)")

        return separator

    @staticmethod
    def _parse_coordinate(value: str) -> float | None:
        """Parse a latitude/longitude value from the CSV."""
        if not value:
            return None

        try:
            return float(value.replace(",", "."))
        except (TypeError, ValueError):
            return None

    async def async_load_cached_data(self) -> bool:
        """Load cached station data from local file."""
        async with self._operation_lock:
            return await self._async_load_cached_data()

    async def _async_load_cached_data(self) -> bool:
        """Load cached station data while the operation lock is held."""
        try:
                _LOGGER.debug("Attempting to load cache from: %s", self._cache_path)
                data = await self.hass.async_add_executor_job(
                    _load_json_file_sync, self._cache_path
                )

                cache_version = data.get("version", "1.0")
                stations = data.get("stations", {})
                last_update = data.get("last_update")
                separator = data.get("csv_separator", "|")
                csv_etag = data.get("csv_etag")
                csv_last_modified = data.get("csv_last_modified")
                if not isinstance(cache_version, str):
                    raise ValueError("Cache version must be a string")
                if not isinstance(stations, dict) or not all(
                    isinstance(station_id, str) and isinstance(station, dict)
                    for station_id, station in stations.items()
                ):
                    raise ValueError("Cache stations must be an object of station objects")
                if last_update is not None and not isinstance(last_update, str):
                    raise ValueError("Cache last_update must be a string or null")
                if not isinstance(separator, str):
                    raise ValueError("Cache csv_separator must be a string")
                if csv_etag is not None and not isinstance(csv_etag, str):
                    raise ValueError("Cache csv_etag must be a string or null")
                if csv_last_modified is not None and not isinstance(csv_last_modified, str):
                    raise ValueError("Cache csv_last_modified must be a string or null")
                if cache_version != CACHE_VERSION:
                    _LOGGER.info(
                        "Cache version %s is outdated (expected %s), forcing update",
                        cache_version,
                        CACHE_VERSION,
                    )
                    return False

                parsed_last_update = self._parse_cached_datetime(last_update)
                self._stations_cache = stations
                self._last_update = parsed_last_update
                self._detected_separator = separator
                self._csv_etag = csv_etag
                self._csv_last_modified = csv_last_modified
                _LOGGER.info(
                    "Loaded %d stations from cache (version %s, separator: %s)",
                    len(self._stations_cache),
                    cache_version,
                    self._detected_separator,
                )
                return True

        except FileNotFoundError:
            _LOGGER.info("No cached data found, will download from CSV")
            return False
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as err:
            _LOGGER.error("Error loading cached data: %s", err)
            return False

    def _parse_cached_datetime(self, value: str | None) -> datetime | None:
        """Parse the cached last update datetime."""
        if not value:
            return None

        try:
            parsed_dt = dt_util.parse_datetime(value)
            if parsed_dt is None:
                parsed_dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Could not parse last_update from cache: %s", value)
            return None

        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=dt_util.now().tzinfo)

        return parsed_dt

    async def async_save_cached_data(self) -> bool:
        """Save station data to local cache file."""
        async with self._operation_lock:
            data = self._build_cache_data(
                stations_cache=self._stations_cache,
                last_update=self._last_update,
                separator=self._detected_separator,
                csv_etag=self._csv_etag,
                csv_last_modified=self._csv_last_modified,
            )
            return await self._async_save_cache_data(data)

    def _build_cache_data(
        self,
        *,
        stations_cache: dict[str, dict[str, Any]],
        last_update: datetime | None,
        separator: str,
        csv_etag: str | None,
        csv_last_modified: str | None,
    ) -> dict[str, Any]:
        """Build a complete cache document from staged values."""
        return {
            "stations": stations_cache,
            "last_update": last_update.isoformat() if last_update else None,
            "version": CACHE_VERSION,
            "csv_separator": separator,
            "csv_etag": csv_etag,
            "csv_last_modified": csv_last_modified,
        }

    async def _async_save_cache_data(self, data: dict[str, Any]) -> bool:
        """Persist a prepared cache document without mutating manager state."""
        try:
            await self.hass.async_add_executor_job(
                _write_json_file_atomic_sync, self._cache_path, data
            )
            _LOGGER.debug(
                "Saved station data to cache (version %s, separator: %s)",
                CACHE_VERSION,
                data["csv_separator"],
            )
            return True
        except (OSError, TypeError, ValueError) as err:
            _LOGGER.error("Error saving cached data: %s", err)
            return False

    def get_station_by_id(self, station_id: str) -> dict[str, Any] | None:
        """Get station data by ID."""
        return self._stations_cache.get(station_id)

    def is_data_available(self) -> bool:
        """Check if station data is available."""
        return bool(self._stations_cache)

    async def async_initialize(self) -> bool:
        """Initialize the CSV manager."""
        async with self._operation_lock:
            if self._initialized:
                return True
            self._initialized = await self._async_initialize()
            return self._initialized

    async def _async_initialize(self) -> bool:
        """Initialize the CSV manager while the operation lock is held."""
        _LOGGER.info("Initializing CSV station data")
        await self._async_migrate_legacy_files()

        cache_loaded = await self._async_load_cached_data()
        _LOGGER.info("Cache loaded: %s, Stations in cache: %d", cache_loaded, len(self._stations_cache))

        if cache_loaded:
            if self._last_update:
                age_hours = (dt_util.now() - self._last_update).total_seconds() / 3600
                _LOGGER.info("Cache age: %.1f hours, Last update: %s", age_hours, self._last_update)
                if not self._stations_cache:
                    _LOGGER.warning("Cache contains 0 stations, forcing update regardless of age")
                elif age_hours < CSV_UPDATE_INTERVAL:
                    _LOGGER.info("Using recent cached station data")
                    return True
                else:
                    _LOGGER.info("Cache is stale (%.1f hours old), forcing update", age_hours)
            else:
                _LOGGER.info("No timestamp in cache, forcing update")
        else:
            _LOGGER.info("No cached data found, will download from CSV")

        _LOGGER.info("Forcing CSV data update")
        success = await self._async_update_csv_data(force_update=True)
        if success:
            _LOGGER.info("CSV update completed successfully")
        else:
            _LOGGER.error("CSV update failed")
        return success

    async def async_periodic_update(self) -> bool:
        """Perform periodic update of CSV data."""
        success = await self.async_update_csv_data()
        return success

    async def async_clear_cache(self) -> bool:
        """Clear the CSV cache files and in-memory data."""
        async with self._operation_lock:
            self._cache_generation += 1
            self._initialized = False
            self._stations_cache.clear()
            self._last_update = None
            self._csv_etag = None
            self._csv_last_modified = None

            success = True
            try:
                exists = await self.hass.async_add_executor_job(os.path.exists, self._cache_path)
                if exists:
                    await self.hass.async_add_executor_job(os.remove, self._cache_path)
                    _LOGGER.info("Removed JSON station cache")
            except OSError as err:
                _LOGGER.error("Error clearing CSV cache: %s", err)
                success = False

            await self._async_remove_legacy_csv_files()
            if success:
                _LOGGER.info("CSV cache cleared successfully")
            return success
