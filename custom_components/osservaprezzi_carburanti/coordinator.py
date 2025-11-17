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
    SERVICE_TYPES,
    FUELS_ENDPOINT,
    LOGOS_ENDPOINT,
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

_LOGGER = logging.getLogger(__name__)

class CarburantiDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.config_entry = entry
        self.session = async_get_clientsession(hass)
        self._fuel_types_cache = None
        self._fuel_types_cache_time = None
        self._logos_cache = None
        self._logos_cache_time = None

        unique_id = entry.unique_id
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{unique_id}",
            update_interval=None,  # Updates are triggered by a listener
        )

    async def _async_update_data(self) -> dict[str, Any]:
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
                    return self._process_station_data(data)
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
            _LOGGER.info("Fetching zone data from: %s with payload: %s", url, payload)
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

    async def _async_fetch_fuel_types(self) -> dict[str, str]:
        """Fetch fuel types from the API with caching."""
        # Cache for 24 hours
        cache_duration = 86400
        now = datetime.now().timestamp()
        
        if (self._fuel_types_cache and self._fuel_types_cache_time and
            now - self._fuel_types_cache_time < cache_duration):
            return self._fuel_types_cache
            
        url = f"{BASE_URL}{FUELS_ENDPOINT}"
        try:
            _LOGGER.info("Fetching fuel types from: %s", url)
            async with self.session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                _LOGGER.debug("Fuel types API response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Fuel types API response data: %s", data)
                    fuel_types = {}
                    for fuel in data.get("results", []):
                        fuel_id = fuel.get("id", "")
                        if "-" in fuel_id:
                            # Parse format like "1-x", "1-1", "1-0"
                            base_id, service_type = fuel_id.split("-", 1)
                            fuel_types[fuel_id] = fuel.get("description", "")
                        else:
                            # Fallback for numeric IDs
                            fuel_types[fuel_id] = fuel.get("description", "")
                    
                    self._fuel_types_cache = fuel_types
                    self._fuel_types_cache_time = now
                    return fuel_types
                else:
                    _LOGGER.error("Service error for fuel types API: %s - %s", response.status, response.reason)
                    raise UpdateFailed(f"Service error: {response.status} - {response.reason}")
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching fuel types: %s", err)
            raise UpdateFailed(f"Error fetching fuel types: {err}")

    async def _async_fetch_logos(self) -> dict[str, Any]:
        """Fetch brand logos from the API with caching."""
        # Cache for 7 days
        cache_duration = 604800
        now = datetime.now().timestamp()
        
        if (self._logos_cache and self._logos_cache_time and
            now - self._logos_cache_time < cache_duration):
            return self._logos_cache
            
        url = f"{BASE_URL}{LOGOS_ENDPOINT}"
        try:
            _LOGGER.info("Fetching logos from: %s", url)
            async with self.session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                _LOGGER.debug("Logos API response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Logos API response data: %s", data)
                    logos = {}
                    for brand in data:
                        brand_id = brand.get("bandieraId")
                        if brand_id is not None:
                            logos[str(brand_id)] = {
                                "name": brand.get("bandiera"),
                                "logos": brand.get("logoMarkerList", [])
                            }
                    
                    self._logos_cache = logos
                    self._logos_cache_time = now
                    return logos
                else:
                    _LOGGER.error("Service error for logos API: %s - %s", response.status, response.reason)
                    raise UpdateFailed(f"Service error: {response.status} - {response.reason}")
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching logos: %s", err)
            raise UpdateFailed(f"Error fetching logos: {err}")

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

    def _process_station_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process the raw data from a single station API call."""
        processed_data = {
            "station_info": {
                "id": data.get("id"),
                "name": data.get("name"),
                "nomeImpianto": data.get("nomeImpianto"),
                "address": data.get("address"),
                "brand": data.get("brand"),
                "company": data.get("company"),
                "phoneNumber": data.get("phoneNumber"),
                "email": data.get("email"),
                "website": data.get("website"),
                # Coordinates are not available when retrieving a station by ID
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
