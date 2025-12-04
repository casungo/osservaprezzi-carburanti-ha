"""CSV data manager for Osservaprezzi Carburanti station information."""
from __future__ import annotations
import asyncio
import csv
import logging
import aiofiles
import aiohttp
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DEFAULT_HEADERS, CSV_URL, CSV_UPDATE_INTERVAL

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

class CSVStationManager:
    """Manager for CSV station data."""
    
    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the CSV manager."""
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self._stations_cache: Dict[str, Dict[str, Any]] = {}
        self._last_update: Optional[datetime] = None
        self._csv_path = hass.config.path("osservaprezzi_stations.csv")
        self._cache_path = hass.config.path("osservaprezzi_cache.json")
        
    async def async_update_csv_data(self, force_update: bool = False) -> bool:
        """Update CSV data from the remote source."""
        now = datetime.now()
        
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
            
            async with self.session.get(CSV_URL, headers=headers, timeout=60) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to download CSV: HTTP %s", response.status)
                    return False
                    
                # Save CSV to file
                content = await response.text()
                async with aiofiles.open(self._csv_path, 'w', encoding='utf-8') as f:
                    await f.write(content)
                    
                # Parse and cache the data
                success = await self._parse_csv_data(content)
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
    
    async def _parse_csv_data(self, csv_content: str) -> bool:
        """Parse CSV content and populate station cache."""
        try:
            # Parse CSV content
            lines = csv_content.strip().split('\n')
            if len(lines) < 3:  # Need at least extraction date, headers, and one data row
                _LOGGER.error("CSV file has insufficient data")
                return False
                
            # Skip extraction date line (line 0) and use headers from line 1
            header_line = lines[1]
            headers = [h.strip().strip('"') for h in header_line.split(';')]
            
            # Find column indices
            col_indices = {}
            for csv_col, internal_col in CSV_COLUMNS.items():
                try:
                    col_indices[internal_col] = headers.index(csv_col)
                    _LOGGER.debug("Found column '%s' -> '%s' at index %d", csv_col, internal_col, col_indices[internal_col])
                except ValueError:
                    _LOGGER.warning("Column '%s' not found in CSV", csv_col)
                    col_indices[internal_col] = -1
            
            # Parse station data
            stations_cache = {}
            for line_num, line in enumerate(lines[2:], 3):  # Start from line 3 (after extraction date and headers)
                try:
                    values = [v.strip().strip('"') for v in line.split(';')]
                    if len(values) < len(headers):
                        continue
                        
                    station_data = {}
                    
                    # Extract data using column mapping
                    for csv_col, internal_col in CSV_COLUMNS.items():
                        idx = col_indices.get(internal_col, -1)
                        if idx >= 0 and idx < len(values):
                            value = values[idx]
                            
                            # Convert coordinates to float
                            if internal_col in ['latitude', 'longitude']:
                                try:
                                    # Handle Italian decimal format (comma as decimal separator)
                                    if ',' in value:
                                        value = value.replace(',', '.')
                                    station_data[internal_col] = float(value)
                                except (ValueError, TypeError):
                                    station_data[internal_col] = None
                            else:
                                station_data[internal_col] = value if value else None
                    
                    # Only include stations with valid coordinates
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
    
    async def async_load_cached_data(self) -> bool:
        """Load cached station data from local file."""
        try:
            import json
            _LOGGER.debug("Attempting to load cache from: %s", self._cache_path)
            async with aiofiles.open(self._cache_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                _LOGGER.debug("Cache file content length: %d characters", len(content))
                data = json.loads(content)
                
                self._stations_cache = data.get('stations', {})
                last_update_str = data.get('last_update')
                if last_update_str:
                    self._last_update = datetime.fromisoformat(last_update_str)
                    
                _LOGGER.info("Loaded %d stations from cache", len(self._stations_cache))
                _LOGGER.debug("Cache metadata: last_update=%s, version=%s",
                            data.get('last_update'), data.get('version'))
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
            import json
            data = {
                'stations': self._stations_cache,
                'last_update': self._last_update.isoformat() if self._last_update else None,
                'version': '1.0'
            }
            
            async with aiofiles.open(self._cache_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                
            _LOGGER.debug("Saved station data to cache")
            return True
            
        except Exception as err:
            _LOGGER.error("Error saving cached data: %s", err)
            return False
    
    def get_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Get station data by ID."""
        return self._stations_cache.get(station_id)
    
    def get_sample_station_ids(self, count: int = 5) -> List[str]:
        """Get a sample of station IDs for debugging."""
        station_ids = list(self._stations_cache.keys())
        return station_ids[:count]
    
    def get_station_id_stats(self) -> Dict[str, Any]:
        """Get statistics about station ID formats."""
        if not self._stations_cache:
            return {"total": 0, "formats": {}}
        
        id_lengths = {}
        id_types = {}
        sample_ids = []
        
        for station_id in list(self._stations_cache.keys())[:100]:  # Sample first 100
            length = len(station_id)
            id_lengths[length] = id_lengths.get(length, 0) + 1
            
            # Check if ID is numeric
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
    
    def get_all_stations(self) -> Dict[str, Dict[str, Any]]:
        """Get all cached stations."""
        return self._stations_cache.copy()
    
    def is_data_available(self) -> bool:
        """Check if station data is available."""
        return len(self._stations_cache) > 0
    
    def get_last_update(self) -> Optional[datetime]:
        """Get last update timestamp."""
        return self._last_update
    
    async def async_initialize(self) -> bool:
        """Initialize the CSV manager."""
        _LOGGER.info("Initializing CSV station data")
        
        # Try to load cached data first
        cache_loaded = await self.async_load_cached_data()
        _LOGGER.info("Cache loaded: %s, Stations in cache: %d", cache_loaded, len(self._stations_cache))
        
        if cache_loaded:
            # Check if data is recent enough
            if self._last_update:
                age_hours = (datetime.now() - self._last_update).total_seconds() / 3600
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
            import os
            
            # Clear in-memory cache
            self._stations_cache.clear()
            self._last_update = None
            
            # Remove cache files if they exist
            for file_path in [self._cache_path, self._csv_path]:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    _LOGGER.info("Removed cache file: %s", file_path)
            
            _LOGGER.info("CSV cache cleared successfully")
            return True
            
        except Exception as err:
            _LOGGER.error("Error clearing CSV cache: %s", err)
            return False