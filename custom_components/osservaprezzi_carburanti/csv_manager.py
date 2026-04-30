"""CSV data manager for Osservaprezzi Carburanti station information."""
from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import os
import shutil
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


def _write_file_sync(path: str, content: str) -> None:
    """Write content to file synchronously."""
    with open(path, "w", encoding="utf-8", newline="") as file_handle:
        file_handle.write(content)


def _read_file_sync(path: str) -> str:
    """Read content from file synchronously."""
    with open(path, "r", encoding="utf-8") as file_handle:
        return file_handle.read()


def _replace_file_sync(source_path: str, destination_path: str) -> None:
    """Atomically replace a file."""
    os.replace(source_path, destination_path)


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
        self._csv_path = hass.config.path(".storage", f"{DOMAIN}_stations.csv")
        self._cache_path = hass.config.path(".storage", f"{DOMAIN}_cache.json")
        self._detected_separator = "|"

    async def _async_migrate_legacy_files(self) -> None:
        """Migrate old CSV/cache files from config root to .storage."""
        old_csv = self.hass.config.path("osservaprezzi_stations.csv")
        old_cache = self.hass.config.path("osservaprezzi_cache.json")

        for old_path, new_path in ((old_csv, self._csv_path), (old_cache, self._cache_path)):
            old_exists = await self.hass.async_add_executor_job(os.path.exists, old_path)
            if not old_exists:
                continue

            new_exists = await self.hass.async_add_executor_job(os.path.exists, new_path)
            if new_exists:
                continue

            try:
                await self.hass.async_add_executor_job(shutil.move, old_path, new_path)
                _LOGGER.info("Migrated legacy file: %s -> %s", old_path, new_path)
            except OSError as err:
                _LOGGER.warning("Failed to migrate %s: %s", old_path, err)

    async def async_update_csv_data(self, force_update: bool = False) -> bool:
        """Update CSV data from the remote source."""
        now = dt_util.now()
        if (
            not force_update
            and self._last_update
            and now - self._last_update < timedelta(hours=CSV_UPDATE_INTERVAL)
        ):
            _LOGGER.debug("CSV data is recent, skipping update")
            return True

        try:
            _LOGGER.info("Downloading station data from CSV: %s", CSV_URL)
            headers = self._build_csv_request_headers(force_update)

            async with self.session.get(
                CSV_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status == 304:
                    self._last_update = now
                    _LOGGER.debug("CSV not modified, keeping cached station data")
                    return True

                if response.status != 200:
                    _LOGGER.error("Failed to download CSV: HTTP %s", response.status)
                    return False

                content = await response.text()
                await self._async_write_csv_file(content)
                self._csv_etag = response.headers.get("ETag")
                self._csv_last_modified = response.headers.get("Last-Modified")

            success = await self._parse_csv_data_from_file()
            if not success:
                _LOGGER.error("Failed to parse CSV data")
                return False

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

    async def _async_write_csv_file(self, content: str) -> None:
        """Write the downloaded CSV atomically to disk."""
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=os.path.dirname(self._csv_path),
            prefix=f"{DOMAIN}_stations_",
            suffix=".tmp",
        )
        temp_path = temp_file.name
        temp_file.close()

        try:
            await asyncio.to_thread(_write_file_sync, temp_path, content)
            await asyncio.to_thread(_replace_file_sync, temp_path, self._csv_path)
        except OSError:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(os.remove, temp_path)
            raise

    def _detect_separator(self, header_line: str) -> str:
        """Detect the CSV separator (pipe | or semicolon ;)."""
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

        self._detected_separator = separator
        return separator

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

    def _parse_station_row(
        self,
        line: str,
        separator: str,
        col_indices: dict[str, int],
    ) -> tuple[str, dict[str, Any]] | None:
        """Parse a single station row from the CSV."""
        values = next(csv.reader([line], delimiter=separator), [])
        return self._parse_station_values(values, col_indices)

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

    def _parse_csv_lines(self, lines: list[str]) -> bool:
        """Parse CSV lines and populate station cache."""
        if len(lines) < 3:
            _LOGGER.error("CSV file has insufficient data")
            return False

        separator = self._detect_separator(lines[1])
        col_indices = self._build_column_indices(lines[1], separator)
        stations_cache: dict[str, dict[str, Any]] = {}

        reader = csv.reader(lines[2:], delimiter=separator)
        for line_num, values in enumerate(reader, start=3):
            try:
                parsed_station = self._parse_station_values(values, col_indices)
                if parsed_station is None:
                    continue
                station_id, station_data = parsed_station
                stations_cache[station_id] = station_data
            except (IndexError, TypeError, ValueError) as err:
                _LOGGER.warning("Error parsing CSV line %d: %s", line_num, err)

        self._stations_cache = stations_cache
        _LOGGER.info("Parsed %d stations from CSV", len(stations_cache))
        return True

    @staticmethod
    def _parse_coordinate(value: str) -> float | None:
        """Parse a latitude/longitude value from the CSV."""
        if not value:
            return None

        try:
            return float(value.replace(",", "."))
        except (TypeError, ValueError):
            return None

    async def _parse_csv_data_from_file(self) -> bool:
        """Parse CSV from disk and populate station cache."""
        def _read_and_parse_streaming() -> bool:
            try:
                with open(self._csv_path, "r", encoding="utf-8") as file_handle:
                    next(file_handle, None)
                    header_line = next(file_handle, None)
                    if header_line is None:
                        _LOGGER.error("CSV file has insufficient data")
                        return False

                    separator = self._detect_separator(header_line.rstrip("\n"))
                    col_indices = self._build_column_indices(header_line.rstrip("\n"), separator)
                    stations_cache: dict[str, dict[str, Any]] = {}

                    reader = csv.reader(file_handle, delimiter=separator)
                    for line_num, values in enumerate(reader, start=3):
                        try:
                            parsed_station = self._parse_station_values(values, col_indices)
                            if parsed_station is None:
                                continue
                            station_id, station_data = parsed_station
                            stations_cache[station_id] = station_data
                        except (IndexError, TypeError, ValueError) as err:
                            _LOGGER.warning("Error parsing CSV line %d: %s", line_num, err)

                    self._stations_cache = stations_cache
                    _LOGGER.info("Parsed %d stations from CSV", len(stations_cache))
                    return True
            except OSError as err:
                _LOGGER.error("Error reading CSV data from disk: %s", err)
                return False

        return await self.hass.async_add_executor_job(_read_and_parse_streaming)

    async def async_load_cached_data(self) -> bool:
        """Load cached station data from local file."""
        try:
            _LOGGER.debug("Attempting to load cache from: %s", self._cache_path)
            content = await asyncio.to_thread(_read_file_sync, self._cache_path)
            data = json.loads(content)

            cache_version = data.get("version", "1.0")
            if cache_version != CACHE_VERSION:
                _LOGGER.info(
                    "Cache version %s is outdated (expected %s), forcing update",
                    cache_version,
                    CACHE_VERSION,
                )
                return False

            self._stations_cache = data.get("stations", {})
            self._last_update = self._parse_cached_datetime(data.get("last_update"))
            self._detected_separator = data.get("csv_separator", "|")
            self._csv_etag = data.get("csv_etag")
            self._csv_last_modified = data.get("csv_last_modified")
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
        try:
            data = {
                "stations": self._stations_cache,
                "last_update": self._last_update.isoformat() if self._last_update else None,
                "version": CACHE_VERSION,
                "csv_separator": self._detected_separator,
                "csv_etag": self._csv_etag,
                "csv_last_modified": self._csv_last_modified,
            }
            content = json.dumps(data, ensure_ascii=False, indent=2)
            await asyncio.to_thread(_write_file_sync, self._cache_path, content)
            _LOGGER.debug(
                "Saved station data to cache (version %s, separator: %s)",
                CACHE_VERSION,
                self._detected_separator,
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
        _LOGGER.info("Initializing CSV station data")
        await self._async_migrate_legacy_files()

        cache_loaded = await self.async_load_cached_data()
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
        success = await self.async_update_csv_data(force_update=True)
        if success:
            await self.async_save_cached_data()
            _LOGGER.info("CSV update completed successfully")
        else:
            _LOGGER.error("CSV update failed")
        return success

    async def async_periodic_update(self) -> bool:
        """Perform periodic update of CSV data."""
        success = await self.async_update_csv_data()
        if success:
            await self.async_save_cached_data()
        return success

    async def async_clear_cache(self) -> bool:
        """Clear the CSV cache files and in-memory data."""
        try:
            self._stations_cache.clear()
            self._last_update = None
            self._csv_etag = None
            self._csv_last_modified = None

            for file_path in (self._cache_path, self._csv_path):
                exists = await self.hass.async_add_executor_job(os.path.exists, file_path)
                if exists:
                    await self.hass.async_add_executor_job(os.remove, file_path)
                    _LOGGER.info("Removed cache file: %s", file_path)

            _LOGGER.info("CSV cache cleared successfully")
            return True

        except OSError as err:
            _LOGGER.error("Error clearing CSV cache: %s", err)
            return False
