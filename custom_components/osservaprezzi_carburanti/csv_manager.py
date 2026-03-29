"""CSV data manager for Osservaprezzi Carburanti station information."""
from __future__ import annotations
import json
import logging
import os
import shutil
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from .const import DOMAIN, DEFAULT_HEADERS, CSV_URL, CSV_UPDATE_INTERVAL


_LOGGER = logging.getLogger(__name__)

# CSV column mapping
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
    "Longitudine": "longitude"
}

def _write_file_sync(path: str, content: str) -> None:
    """Write content to file synchronously."""
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)

def _read_file_sync(path: str) -> str:
    """Read content from file synchronously."""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

class CSVStationManager:
    """Manager for CSV station data."""
    
    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the CSV manager."""
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self._stations_cache: dict[str, dict[str, Any]] = {}
        self._last_update: datetime | None = None
        self._csv_path = hass.config.path(".storage", f"{DOMAIN}_stations.csv")
        self._cache_path = hass.config.path(".storage", f"{DOMAIN}_cache.json")
        self._detected_separator = '|'  # Default to new format (pipe)

    async def _async_migrate_legacy_files(self) -> None:
        """Migrate old CSV/cache files from config root to .storage."""
        old_csv = self.hass.config.path("osservaprezzi_stations.csv")
        old_cache = self.hass.config.path("osservaprezzi_cache.json")

        for old_path, new_path in [(old_csv, self._csv_path), (old_cache, self._cache_path)]:
            old_exists = await self.hass.async_add_executor_job(os.path.exists, old_path)
            if old_exists:
                new_exists = await self.hass.async_add_executor_job(os.path.exists, new_path)
                if not new_exists:
                    try:
                        await self.hass.async_add_executor_job(shutil.move, old_path, new_path)
                        _LOGGER.info("Migrated legacy file: %s -> %s", old_path, new_path)
                    except Exception as err:
                        _LOGGER.warning("Failed to migrate %s: %s", old_path, err)

    async def async_update_csv_data(self, force_update: bool = False) -> bool:
        """Update CSV data from the remote source."""
        now = dt_util.now()

        # Check if we need to update (default: once per day)
        if not force_update and self._last_update:
            if now - self._last_update < timedelta(hours=CSV_UPDATE_INTERVAL):
                _LOGGER.debug("CSV data is recent, skipping update")
                return True

        try:
            _LOGGER.info("Downloading station data from CSV: %s", CSV_URL)

            headers = {
                **DEFAULT_HEADERS,
                "Accept": "text/csv,application/csv,text/plain,*/*",
            }

            async with self.session.get(CSV_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to download CSV: HTTP %s", response.status)
                    return False

                # Save CSV to file
                content = await response.text()
                await asyncio.to_thread(_write_file_sync, self._csv_path, content)

                # Parse from disk instead of from memory string to reduce memory usage
                success = await self._parse_csv_data_from_file()
                if success:
                    self._last_update = now
                    _LOGGER.info("Successfully updated CSV station data")
                    return True
                else:
                    _LOGGER.error("Failed to parse CSV data")
                    return False

        except aiohttp.ClientError as err:
            _LOGGER.error("Error downloading CSV data: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Unexpected error updating CSV data: %s", err)
            return False
    
    def _detect_separator(self, header_line: str) -> str:
        """Detect the CSV separator (pipe | or semicolon ;)."""
        pipe_count = header_line.count('|')
        semicolon_count = header_line.count(';')

        if pipe_count > semicolon_count:
            separator = '|'
            _LOGGER.debug("Detected pipe (|) separator in CSV file")
        elif semicolon_count > pipe_count:
            separator = ';'
            _LOGGER.debug("Detected semicolon (;) separator in CSV file")
        else:
            # Default to new format if equal or none found
            separator = '|'
            _LOGGER.debug("Separator count equal or none detected, defaulting to pipe (|)")

        self._detected_separator = separator
        return separator

    def _parse_csv_lines(self, lines: list[str]) -> bool:
        """Parse CSV lines and populate station cache (synchronous helper)."""
        try:
            if len(lines) < 3:
                _LOGGER.error("CSV file has insufficient data")
                return False

            header_line = lines[1]
            separator = self._detect_separator(header_line)
            headers = [h.strip().strip('"') for h in header_line.split(separator)]

            col_indices = {}
            for csv_col, internal_col in CSV_COLUMNS.items():
                try:
                    col_indices[internal_col] = headers.index(csv_col)
                except ValueError:
                    _LOGGER.warning("Column '%s' not found in CSV", csv_col)
                    col_indices[internal_col] = -1

            stations_cache: dict[str, dict[str, Any]] = {}
            for line_num, line in enumerate(lines[2:], 3):
                try:
                    values = [v.strip().strip('"') for v in line.split(separator)]
                    if len(values) < len(headers):
                        continue

                    station_data: dict[str, Any] = {}

                    for csv_col, internal_col in CSV_COLUMNS.items():
                        idx = col_indices.get(internal_col, -1)
                        if idx >= 0 and idx < len(values):
                            value = values[idx]

                            if internal_col in ['latitude', 'longitude']:
                                try:
                                    if ',' in value:
                                        value = value.replace(',', '.')
                                    station_data[internal_col] = float(value)
                                except (ValueError, TypeError):
                                    station_data[internal_col] = None
                            else:
                                station_data[internal_col] = value if value else None

                    station_id = station_data.get('id')
                    if station_id and station_data.get('latitude') and station_data.get('longitude'):
                        stations_cache[station_id] = station_data

                except Exception as err:
                    _LOGGER.warning("Error parsing CSV line %d: %s", line_num, err)
                    continue

            self._stations_cache = stations_cache
            _LOGGER.info("Parsed %d stations from CSV", len(stations_cache))
            return True

        except Exception as err:
            _LOGGER.error("Error parsing CSV data: %s", err)
            return False

    async def _parse_csv_data_from_file(self) -> bool:
        """Parse CSV from disk and populate station cache."""
        def _read_and_parse() -> bool:
            with open(self._csv_path, 'r', encoding='utf-8') as f:
                lines = [line.rstrip('\n') for line in f]
            return self._parse_csv_lines(lines)
        return await self.hass.async_add_executor_job(_read_and_parse)
    
    async def async_load_cached_data(self) -> bool:
        """Load cached station data from local file."""
        try:
            _LOGGER.debug("Attempting to load cache from: %s", self._cache_path)
            content = await asyncio.to_thread(_read_file_sync, self._cache_path)
            _LOGGER.debug("Cache file content length: %d characters", len(content))
            data = json.loads(content)
            
            # Check cache version - force update if version is outdated
            cache_version = data.get('version', '1.0')
            if cache_version != '2.0':
                _LOGGER.info("Cache version %s is outdated (expected 2.0), forcing update", cache_version)
                return False
            
            self._stations_cache = data.get('stations', {})
            last_update_str = data.get('last_update')
            if last_update_str:
                try:
                    parsed_dt = dt_util.parse_datetime(last_update_str)
                    if parsed_dt is None:
                        parsed_dt = datetime.fromisoformat(last_update_str)
                    
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=dt_util.now().tzinfo)
                        
                    self._last_update = parsed_dt
                except (ValueError, TypeError):
                    _LOGGER.warning("Could not parse last_update from cache: %s", last_update_str)
                    self._last_update = None
            
            # Load separator info if available
            self._detected_separator = data.get('csv_separator', '|')
            
            _LOGGER.info("Loaded %d stations from cache (version %s, separator: %s)", 
                       len(self._stations_cache), cache_version, self._detected_separator)
            _LOGGER.debug("Cache metadata: last_update=%s",
                        data.get('last_update'))
            return True
            
        except FileNotFoundError:
            _LOGGER.info("No cached data found, will download from CSV")
            return False
        except Exception as err:
            _LOGGER.error("Error loading cached data: %s", err)
            return False
    
    async def async_save_cached_data(self) -> bool:
        """Save station data to local cache file."""
        try:
            data = {
                'stations': self._stations_cache,
                'last_update': self._last_update.isoformat() if self._last_update else None,
                'version': '2.0',
                'csv_separator': self._detected_separator
            }
            
            content = json.dumps(data, ensure_ascii=False, indent=2)
            await asyncio.to_thread(_write_file_sync, self._cache_path, content)
                
            _LOGGER.debug("Saved station data to cache (version 2.0, separator: %s)", self._detected_separator)
            return True
            
        except Exception as err:
            _LOGGER.error("Error saving cached data: %s", err)
            return False
    
    def get_station_by_id(self, station_id: str) -> dict[str, Any] | None:
        """Get station data by ID."""
        return self._stations_cache.get(station_id)
    
    def get_sample_station_ids(self, count: int = 5) -> list[str]:
        """Get a sample of station IDs for debugging."""
        station_ids = list(self._stations_cache.keys())
        return station_ids[:count]
    
    def get_station_id_stats(self) -> dict[str, Any]:
        """Get statistics about station ID formats."""
        if not self._stations_cache:
            return {"total": 0, "formats": {}}
        
        id_lengths: dict[int, int] = {}
        id_types: dict[str, int] = {}
        sample_ids: list[str] = []
        
        for station_id in list(self._stations_cache.keys())[:100]:
            length = len(station_id)
            id_lengths[length] = id_lengths.get(length, 0) + 1
            
            if station_id.isdigit():
                id_types["numeric"] = id_types.get("numeric", 0) + 1
            else:
                id_types["alphanumeric"] = id_types.get("alphanumeric", 0) + 1
            
            if len(sample_ids) < 10:
                sample_ids.append(station_id)
        
        return {
            "total": len(self._stations_cache),
            "length_distribution": id_lengths,
            "type_distribution": id_types,
            "sample_ids": sample_ids
        }
    
    def _get_all_stations(self) -> dict[str, dict[str, Any]]:
        """Get all cached stations."""
        return self._stations_cache.copy()

    def is_data_available(self) -> bool:
        """Check if station data is available."""
        return len(self._stations_cache) > 0

    def _get_last_update(self) -> datetime | None:
        """Get last update timestamp."""
        return self._last_update
    
    async def async_initialize(self) -> bool:
        """Initialize the CSV manager."""
        _LOGGER.info("Initializing CSV station data")

        # Migrate legacy files from config root to .storage (one-time operation)
        await self._async_migrate_legacy_files()
        
        # Try to load cached data first
        cache_loaded = await self.async_load_cached_data()
        _LOGGER.info("Cache loaded: %s, Stations in cache: %d", cache_loaded, len(self._stations_cache))
        
        if cache_loaded:
            # Check if data is recent enough
            if self._last_update:
                age_hours = (dt_util.now() - self._last_update).total_seconds() / 3600
                _LOGGER.info("Cache age: %.1f hours, Last update: %s", age_hours, self._last_update)
                
                # Force update if cache is empty, regardless of age
                if len(self._stations_cache) == 0:
                    _LOGGER.warning("Cache contains 0 stations, forcing update regardless of age")
                elif age_hours < 24:
                    _LOGGER.info("Using recent cached station data")
                    return True
                else:
                    _LOGGER.info("Cache is stale (%.1f hours old), forcing update", age_hours)
            else:
                _LOGGER.info("No timestamp in cache, forcing update")
        else:
            _LOGGER.info("No cached data found, will download from CSV")
        
        # Download fresh data
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

            # Remove cache files if they exist
            for file_path in [self._cache_path, self._csv_path]:
                exists = await self.hass.async_add_executor_job(os.path.exists, file_path)
                if exists:
                    await self.hass.async_add_executor_job(os.remove, file_path)
                    _LOGGER.info("Removed cache file: %s", file_path)

            _LOGGER.info("CSV cache cleared successfully")
            return True

        except Exception as err:
            _LOGGER.error("Error clearing CSV cache: %s", err)
            return False