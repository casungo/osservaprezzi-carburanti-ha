from __future__ import annotations
import logging
from datetime import datetime
from typing import Any
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import (
    BASE_URL,
    DEFAULT_HEADERS,
    STATION_ENDPOINT,
    ZONE_ENDPOINT,
    FUEL_TYPES,
    DOMAIN,
    CONF_CONFIG_TYPE,
    CONF_TYPE_STATION,
    CONF_TYPE_ZONE,
    CONF_STATION_ID,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_FUEL_TYPE,
    CONF_IS_SELF,
    ATTR_DISTANCE,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
)
from .csv_manager import CSVStationManager

_LOGGER = logging.getLogger(__name__)

class CarburantiDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        self.session = async_get_clientsession(hass)
        self.csv_manager = CSVStationManager(hass)

        unique_id = entry.unique_id
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{unique_id}",
            update_interval=None,  # Updates are triggered by a listener
        )

    async def _async_update_data(self) -> dict[str, Any]:
        # Ensure CSV data is available
        if not self.csv_manager.is_data_available():
            _LOGGER.info("Initializing CSV station data")
            await self.csv_manager.async_initialize()
        
        config_type = self.config_entry.data.get(CONF_CONFIG_TYPE, CONF_TYPE_STATION)

        if config_type == CONF_TYPE_ZONE:
            return await self._async_fetch_zone_data()
        else: # Default to station type for backward compatibility
            return await self._async_fetch_station_data()

    async def _async_fetch_station_data(self) -> dict[str, Any]:
        """Fetch data for a single station."""
        station_id = self.config_entry.data[CONF_STATION_ID]
        url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=station_id)}"
        try:
            _LOGGER.info("Fetching station data from: %s", url)
            async with self.session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                _LOGGER.debug("Station API response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Station API response data: %s", data)
                    # Debug: Log the station ID format from API response
                    api_station_id = data.get("id")
                    if api_station_id:
                        _LOGGER.debug("API returned station ID: '%s' (type: %s, length: %d)",
                                    api_station_id, type(api_station_id).__name__, len(str(api_station_id)))
                    return await self._process_station_data(data)
                elif response.status == 404:
                    _LOGGER.error("Station with ID %s not found", station_id)
                    raise UpdateFailed(f"Station with ID {station_id} not found")
                elif response.status == 429:
                    _LOGGER.error("Rate limit exceeded for station API")
                    raise UpdateFailed("Rate limit exceeded. Please try again later.")
                elif response.status >= 500:
                    _LOGGER.error("Server error for station API: %s - %s", response.status, response.reason)
                    raise UpdateFailed(f"Server error: {response.status} - {response.reason}")
                else:
                    _LOGGER.error("Service error for station API: %s - %s", response.status, response.reason)
                    raise UpdateFailed(f"Service error: {response.status} - {response.reason}")
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching station data: %s", err)
            raise UpdateFailed(f"Error fetching station data: {err}")

    async def _async_fetch_zone_data(self) -> dict[str, Any]:
        """Fetch data for a zone and find the cheapest station."""
        url = f"{BASE_URL}{ZONE_ENDPOINT}"
        # Support both single point (backward compatibility) and multiple points
        points_data = self.config_entry.data.get("points", [
            {
                "lat": self.config_entry.data[CONF_LATITUDE],
                "lng": self.config_entry.data[CONF_LONGITUDE],
            }
        ])
        
        payload = {
            "points": points_data,
            "radius": self.config_entry.data[CONF_RADIUS],
        }
        target_fuel_id = self.config_entry.data[CONF_FUEL_TYPE]
        target_is_self = self.config_entry.data[CONF_IS_SELF]

        try:
            async with self.session.post(url, headers=DEFAULT_HEADERS, json=payload, timeout=30) as response:
                _LOGGER.debug("Zone API response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Zone API response data: %s", data)
                    if not data.get("success"):
                        _LOGGER.error("API reported failure for zone search: %s", data.get('message', 'Unknown error'))
                        raise UpdateFailed(f"API reported failure: {data.get('message', 'Unknown error')}")
                    if not data.get("results"):
                        _LOGGER.warning("No results found for the specified zone.")
                        raise UpdateFailed("No results found for the zone.")
                elif response.status == 429:
                    _LOGGER.error("Rate limit exceeded for zone API")
                    raise UpdateFailed("Rate limit exceeded. Please try again later.")
                elif response.status >= 500:
                    _LOGGER.error("Server error for zone API: %s - %s", response.status, response.reason)
                    raise UpdateFailed(f"Server error: {response.status} - {response.reason}")
                else:
                    _LOGGER.error("Service error for zone API: %s - %s", response.status, response.reason)
                    raise UpdateFailed(f"Service error: {response.status} - {response.reason}")

                cheapest_station = None
                min_price = float('inf')

                for station in data["results"]:
                    for fuel in station.get("fuels", []):
                        if (
                            fuel.get("fuelId") == target_fuel_id
                            and fuel.get("isSelf") == target_is_self
                            and fuel.get("price") is not None
                            and fuel.get("price") < min_price
                        ):
                            min_price = fuel.get("price")
                            cheapest_station = {
                                **station,
                                "target_fuel": fuel, # Keep track of the specific fuel that was cheapest
                            }
                
                if cheapest_station is None:
                    raise UpdateFailed("No station found with the specified fuel type in the zone.")

                return self._process_zone_data(cheapest_station)

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error fetching zone data: {err}")

    def _get_fuel_types(self) -> dict[int, str]:
        """Get fuel types from static mapping."""
        return FUEL_TYPES


    async def _async_get_station_coordinates(self, station_id: str | None, address: str | None = None, station_name: str | None = None) -> dict[str, Any] | None:
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
            sample_stations = self.csv_manager.get_sample_station_ids(5)
            _LOGGER.debug("Sample station IDs from CSV: %s", sample_stations)
            
            # Get detailed statistics about station IDs
            id_stats = self.csv_manager.get_station_id_stats()
            _LOGGER.info("Station ID statistics: %s", id_stats)
        
        _LOGGER.error("No coordinates found for station %s in CSV data", station_id_str)
        return None


    def _parse_iso_datetime(self, datetime_str: str | None) -> str | None:
        """Parse ISO 8601 datetime string and return it in a consistent format."""
        if not datetime_str:
            return None
        
        try:
            # Handle various ISO 8601 formats
            if datetime_str.endswith('Z'):
                # UTC format
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
        station_name = data.get("nomeImpianto")  # Extract station name for potential zone search matching
        
        _LOGGER.debug("Processing station data - ID: %s, Name: %s, Address: %s", station_id, station_name, address)
        
        # Get coordinates using CSV data
        coordinates = await self._async_get_station_coordinates(station_id, address, station_name)
        
        # Get additional station data from CSV if available
        csv_station = self.csv_manager.get_station_by_id(station_id) if station_id else None
        
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
            "last_update": datetime.now().isoformat(),
        }
        for fuel in data.get("fuels", []):
            fuel_id = fuel.get("fuelId")
            fuel_name = FUEL_TYPES.get(fuel_id, "Unknown")
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
        self.hass.loop.create_task(self._periodic_csv_update_task())

    async def _periodic_csv_update_task(self) -> None:
        """Periodic task to update CSV data."""
        import asyncio
        
        while True:
            try:
                # Check if CSV updates are enabled
                csv_enabled = self.config_entry.options.get(CONF_CSV_UPDATE_ENABLED, DEFAULT_CSV_UPDATE_ENABLED)
                if not csv_enabled:
                    _LOGGER.debug("CSV updates are disabled, skipping periodic update")
                    await asyncio.sleep(60 * 60)  # Check again in 1 hour
                    continue
                
                # Get configured update interval
                csv_interval = self.config_entry.options.get(CONF_CSV_UPDATE_INTERVAL, DEFAULT_CSV_UPDATE_INTERVAL)
                
                # Wait configured hours between updates
                await asyncio.sleep(csv_interval * 60 * 60)
                
                _LOGGER.info("Performing periodic CSV data update")
                success = await self.csv_manager.async_periodic_update()
                if success:
                    _LOGGER.info("Periodic CSV update completed successfully")
                else:
                    _LOGGER.warning("Periodic CSV update failed")
                    
            except Exception as err:
                _LOGGER.error("Error in periodic CSV update task: %s", err)
                # Continue the loop even if there's an error

    async def async_force_csv_update(self) -> bool:
        """Force an immediate CSV update."""
        _LOGGER.info("Forcing immediate CSV data update")
        return await self.csv_manager.async_update_csv_data(force_update=True)

    def _process_zone_data(self, station_data: dict[str, Any]) -> dict[str, Any]:
        """Process the data for the cheapest station found in a zone search."""
        target_fuel = station_data["target_fuel"]
        fuel_id = target_fuel.get("fuelId")
        fuel_name = FUEL_TYPES.get(fuel_id, "Unknown")
        service_type = "self" if target_fuel["isSelf"] else "servito"
        fuel_key = f"{fuel_name}_{service_type}"

        processed_data = {
            "station_info": {
                "id": station_data.get("id"),
                "name": station_data.get("name"),
                "address": station_data.get("address", "N/A"), # Address might be null in zone search
                "brand": station_data.get("brand"),
                ATTR_DISTANCE: round(float(station_data.get("distance", 0)), 2),
                # Add geolocation data from zone search response
                ATTR_LATITUDE: station_data.get("location", {}).get("lat") if station_data.get("location") else None,
                ATTR_LONGITUDE: station_data.get("location", {}).get("lng") if station_data.get("location") else None,
            },
            "fuels": {
                fuel_key: {
                    "price": target_fuel.get("price"),
                    "last_update": self._parse_iso_datetime(station_data.get("insertDate")),
                    "fuel_id": fuel_id,
                    "is_self": target_fuel.get("isSelf"),
                }
            },
            "last_update": datetime.now().isoformat(),
        }
        return processed_data
