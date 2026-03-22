from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Any, Callable
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .const import (
    DOMAIN,
    CONF_STATION_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CSV_UPDATE_INTERVAL,
)
from .api import fetch_station_data
from .csv_manager import CSVStationManager

_LOGGER = logging.getLogger(__name__)

class CarburantiDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        self.session = async_get_clientsession(hass)
        self.csv_manager = CSVStationManager(hass)
        self._csv_update_listener: Callable[[], None] | None = None

        unique_id = entry.unique_id
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{unique_id}",
            update_interval=None,  # Updates are triggered by a listener
        )
        self._schedule_csv_updates()

    async def _async_update_data(self) -> dict[str, Any]:
        # Ensure CSV data is available
        if not self.csv_manager.is_data_available():
            _LOGGER.info("Initializing CSV station data")
            await self.csv_manager.async_initialize()
        
        return await self._async_fetch_station_data()

    async def _async_fetch_station_data(self) -> dict[str, Any]:
        """Fetch data for a single station."""
        station_id = self.config_entry.data[CONF_STATION_ID]
        try:
            data = await fetch_station_data(self.hass, station_id)
            return await self._process_station_data(data)
        except aiohttp.ClientResponseError as err:
            if err.status == 404:
                _LOGGER.error("Station with ID %s not found", station_id)
                raise UpdateFailed(f"Station with ID {station_id} not found")
            else:
                _LOGGER.error("Service error for station API: %s", err.status)
                raise UpdateFailed(f"Service error: {err.status}")
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching station data: %s", err)
            raise UpdateFailed(f"Error fetching station data: {err}")
        except Exception as err:
            _LOGGER.error("Unexpected error fetching station data for station %s: %s", station_id, err)
            raise UpdateFailed(f"Unexpected error fetching station data: {err}")




    async def _async_get_station_coordinates(self, station_id: str | None) -> dict[str, Any] | None:
        """Get station coordinates using CSV data only."""
        if not station_id:
            _LOGGER.warning("No station ID provided for coordinate lookup")
            return None

        # Convert station_id to string to handle int/str type mismatch
        station_id_str = str(station_id)
        _LOGGER.debug("Looking for station ID: '%s' (original type: %s, as string: '%s', length: %d)",
                     station_id, type(station_id).__name__, station_id_str, len(station_id_str))

        # Try CSV data only
        csv_station = self.csv_manager.get_station_by_id(station_id_str)
        if csv_station:
            lat = csv_station.get('latitude')
            lon = csv_station.get('longitude')
            if lat is not None and lon is not None:
                _LOGGER.debug("Found coordinates in CSV data for station %s: %s, %s", station_id_str, lat, lon)
                return {
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "source": "csv"
                }
            else:
                _LOGGER.warning("Station %s found in CSV but missing coordinates. CSV data: %s", station_id_str, csv_station)
        else:
            _LOGGER.warning("Station %s not found in CSV data", station_id_str)

            # Debug: Let's see some sample station IDs from CSV to understand the format
            if _LOGGER.isEnabledFor(logging.DEBUG):
                sample_stations = self.csv_manager.get_sample_station_ids(5)
                _LOGGER.debug("Sample station IDs from CSV: %s", sample_stations)

                # Get detailed statistics about station IDs
                id_stats = self.csv_manager.get_station_id_stats()
                _LOGGER.debug("Station ID statistics: %s", id_stats)

        _LOGGER.error("No coordinates found for station %s in CSV data", station_id_str)
        return None


    def _parse_iso_datetime(self, datetime_str: str | None) -> str | None:
        """Parse ISO 8601 datetime string and return it in a consistent format.

        Note: Python 3.11+ handles 'Z' natively in fromisoformat, but we use
        the replace() approach for compatibility with older Python versions.
        """
        if not datetime_str:
            return None

        try:
            # Handle various ISO 8601 formats
            if datetime_str.endswith('Z'):
                # UTC format - replace Z with +00:00 for compatibility
                dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            else:
                # Already has timezone info
                dt = datetime.fromisoformat(datetime_str)

            # Return in ISO format without microseconds for consistency
            return dt.replace(microsecond=0).isoformat()
        except (ValueError, TypeError):
            _LOGGER.warning("Failed to parse datetime: %s", datetime_str)
            return datetime_str  # Return original if parsing fails

    async def _process_station_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process the raw data from a single station API call."""
        address = data.get("address")
        station_id = data.get("id")

        _LOGGER.debug("Processing station data - ID: %s, Name: %s, Address: %s", station_id, data.get("nomeImpianto"), address)

        # Get coordinates using CSV data
        coordinates = await self._async_get_station_coordinates(station_id)

        # Get additional station data from CSV if available
        # Convert station_id to string because CSV cache keys are strings
        csv_station = self.csv_manager.get_station_by_id(str(station_id)) if station_id else None
        
        processed_data = {
            "station_info": {
                "id": data.get("id"),
                "name": data.get("name"),
                "nomeImpianto": data.get("nomeImpianto"),
                "address": address,
                "brand": data.get("brand"),
                "company": data.get("company"),
                "phoneNumber": data.get("phoneNumber"),
                "email": data.get("email"),
                "website": data.get("website"),
                # Add coordinates from CSV data
                ATTR_LATITUDE: coordinates["latitude"] if coordinates else None,
                ATTR_LONGITUDE: coordinates["longitude"] if coordinates else None,
                # Add CSV data if available
                "operator": csv_station.get("operator") if csv_station else None,
                "station_type": csv_station.get("station_type") if csv_station else None,
                "municipality": csv_station.get("municipality") if csv_station else None,
                "province": csv_station.get("province") if csv_station else None,
                "coordinate_source": coordinates.get("source", "csv") if coordinates else None,
            },
            "fuels": {},
            "services": data.get("services", []),
            "opening_hours": data.get("orariapertura", []),
            "last_update": dt_util.now().isoformat(),
        }
        for fuel in data.get("fuels", []):
            fuel_id = fuel.get("fuelId")
            fuel_name = fuel.get("name", "Unknown")
            service_type = "self" if fuel.get("isSelf") else "servito"
            fuel_key = f"{fuel_name}_{service_type}"
            processed_data["fuels"][fuel_key] = {
                "price": fuel.get("price"),
                "last_update": self._parse_iso_datetime(fuel.get("insertDate")),
                "validity_date": self._parse_iso_datetime(fuel.get("validityDate")),
                "fuel_id": fuel_id,
                "is_self": fuel.get("isSelf"),
                "service_area_id": fuel.get("serviceAreaId"),
            }
        return processed_data

    def _schedule_csv_updates(self) -> None:
        """Schedule periodic CSV updates."""
        # Schedule CSV update every 24 hours
        self._csv_update_listener = async_track_time_interval(
            self.hass,
            self._async_csv_update_callback,
            timedelta(hours=CSV_UPDATE_INTERVAL)
        )

    async def _async_csv_update_callback(self, now: datetime) -> None:
        """Callback for periodic CSV data update."""
        _LOGGER.info("Performing periodic CSV data update")
        success = await self.csv_manager.async_periodic_update()
        if success:
            _LOGGER.info("Periodic CSV update completed successfully")
        else:
            _LOGGER.warning("Periodic CSV update failed")

    async def async_shutdown(self) -> None:
        """Clean up resources when shutting down."""
        # Cancel CSV update listener if active
        if self._csv_update_listener:
            self._csv_update_listener()
            self._csv_update_listener = None
        # Call parent shutdown
        await super().async_shutdown()

    async def async_force_csv_update(self) -> bool:
        """Force an immediate CSV update.

        This method can be called externally (e.g., via a service call)
        to trigger an immediate refresh of CSV station data.
        """
        _LOGGER.info("Forcing immediate CSV data update")
        return await self.csv_manager.async_update_csv_data(force_update=True)

