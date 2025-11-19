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
    NOMINATIM_URL,
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

    async def _async_geocode_address(self, address: str) -> dict[str, Any] | None:
        """Convert address to coordinates using Nominatim."""
        if not address:
            _LOGGER.warning("No address provided for geocoding")
            return None
            
        # Sanitize address for logging to avoid exposing personal information
        sanitized_address = self._sanitize_address_for_logging(address)
            
        headers = {
            "user-agent": f"home-assistant-osservaprezzi-carburanti/1.0",
        }
        
        params = {
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "countrycodes": "it",  # Limit to Italy since this is for Italian gas stations
        }
        
        try:
            _LOGGER.info("NOMINATIM API: Geocoding address: %s", sanitized_address)
            _LOGGER.debug("NOMINATIM API: URL=%s, params=%s", NOMINATIM_URL, {k: v for k, v in params.items() if k != 'q'})
            
            async with self.session.get(NOMINATIM_URL, headers=headers, params=params, timeout=30) as response:
                _LOGGER.debug("NOMINATIM API: Response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("NOMINATIM API: Response received with %d results", len(data) if data else 0)
                    
                    if data and len(data) > 0:
                        result = data[0]
                        lat = float(result["lat"])
                        lon = float(result["lon"])
                        display_name = result.get("display_name", "Unknown")
                        address_type = result.get("type", "Unknown")
                        
                        _LOGGER.info("NOMINATIM API: SUCCESS - Found coordinates: lat=%.6f, lon=%.6f", lat, lon)
                        _LOGGER.info("NOMINATIM API: Location details - display_name='%s', type='%s'", display_name, address_type)
                        
                        return {
                            "latitude": lat,
                            "longitude": lon,
                            "display_name": display_name,
                            "type": address_type,
                        }
                    else:
                        _LOGGER.warning("NOMINATIM API: No results found for address: %s", sanitized_address)
                        # Try with a simplified address as a fallback
                        simplified_address = self._simplify_address(address)
                        if simplified_address != address:
                            _LOGGER.info("NOMINATIM API: Retrying with simplified address")
                            return await self._async_geocode_simplified(simplified_address, headers)
                        return None
                elif response.status == 429:
                    _LOGGER.error("NOMINATIM API: Rate limit exceeded")
                    return None
                else:
                    _LOGGER.error("NOMINATIM API: Error %s - %s", response.status, response.reason)
                    return None
        except aiohttp.ClientError as err:
            _LOGGER.error("NOMINATIM API: Error geocoding address: %s", err)
            return None

    def _simplify_address(self, address: str) -> str:
        """Simplify Italian address by expanding abbreviations and normalizing format."""
        if not address:
            return address
            
        # Common Italian road abbreviations and their expansions
        abbreviations = {
            "Tang.": "Tangenziale",
            "S.P.": "Strada Provinciale",
            "S.S.": "Strada Statale",
            "V.": "Via",
            "C.": "Corso",
            "P.": "Piazza",
            "L.": "Largo",
            "F.": "Frazione",
        }
        
        simplified = address
        
        # Replace abbreviations with full words
        for abbrev, full in abbreviations.items():
            simplified = simplified.replace(abbrev, full)
        
        # Normalize separators - replace multiple spaces/hyphens with single space
        import re
        simplified = re.sub(r'\s*-\s*', ' ', simplified)  # Replace " - " with space
        simplified = re.sub(r'\s+', ' ', simplified)  # Replace multiple spaces with single space
        
        # Remove province code in parentheses if present
        simplified = re.sub(r'\s*\([^)]*\)$', '', simplified)  # Remove province code at end
        
        return simplified.strip()

    def _sanitize_address_for_logging(self, address: str) -> str:
        """Sanitize address for logging to avoid exposing personal information."""
        if not address:
            return ""
        
        # Extract just the city and postal code for logging
        import re
        postal_city_match = re.search(r'(\d{5})\s+([A-Z]+(?:\s+[A-Z]+)*)', address.upper())
        if postal_city_match:
            postal_code = postal_city_match.group(1)
            city = postal_city_match.group(2)
            return f"{postal_code[:2]}*** {city[:3]}***"
        
        # Fallback: show first few characters and length
        if len(address) > 10:
            return f"{address[:5]}*** ({len(address)} chars)"
        elif len(address) > 0:
            return f"*** ({len(address)} chars)"
        else:
            return "***"

    def _extract_city_from_address(self, address: str) -> str | None:
        """Extract city name from address using pattern matching."""
        if not address:
            return None
            
        import re
        
        # Try to extract city from postal code and city pattern (e.g., "12345 CITY")
        postal_city_match = re.search(r'(\d{5})\s+([A-Z]+(?:\s+[A-Z]+)*)', address.upper())
        if postal_city_match:
            city = postal_city_match.group(2).strip()
            if len(city) >= 3:  # Minimum reasonable city name length
                return city.title()
        
        # Try to extract city from common patterns
        # Look for words that might be city names (usually after postal codes or at the end)
        patterns = [
            r'(\d{5})\s+([A-Za-z]+(?:\s+[A-Za-z]+)*)',  # Postal code + city
            r',\s*([A-Za-z]+(?:\s+[A-Za-z]+)*)\s*$',   # City at end after comma
            r'\s+([A-Za-z]+(?:\s+[A-Za-z]+)*)\s*$',    # City at end
        ]
        
        for pattern in patterns:
            match = re.search(pattern, address)
            if match:
                city = match.group(1).strip()
                # Filter out common non-city words
                if (len(city) >= 3 and
                    city.upper() not in ['VIA', 'CORSO', 'PIAZZA', 'LARGO', 'STRADA', 'FRAGZIONE']):
                    return city.title()
        
        return None

    async def _async_geocode_simplified(self, address: str, headers: dict[str, str]) -> dict[str, Any] | None:
        """Try geocoding with simplified address and different parameters."""
        # Try without country restriction first
        params = {
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
        }
        
        try:
            _LOGGER.info("Retrying geocoding without country restriction for: %s", self._sanitize_address_for_logging(address))
            async with self.session.get(NOMINATIM_URL, headers=headers, params=params, timeout=30) as response:
                _LOGGER.debug("Geocoding retry response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    # Don't log full response data to avoid exposing personal information
                    _LOGGER.debug("Geocoding retry response received with %d results", len(data) if data else 0)
                    
                    if data and len(data) > 0:
                        _LOGGER.info("Successfully geocoded simplified address: %s", self._sanitize_address_for_logging(address))
                        return {
                            "latitude": float(data[0]["lat"]),
                            "longitude": float(data[0]["lon"]),
                        }
                    
            # If still no results, try with just the city extracted from address
            city_name = self._extract_city_from_address(address)
            if city_name:
                city_params = {
                    "q": f"{city_name}, Italy",
                    "format": "jsonv2",
                    "limit": 1,
                }
                _LOGGER.info("Final fallback: geocoding city only for: %s", city_name)
                async with self.session.get(NOMINATIM_URL, headers=headers, params=city_params, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and len(data) > 0:
                            _LOGGER.warning("Using city center coordinates as fallback for address: %s", self._sanitize_address_for_logging(address))
                            return {
                                "latitude": float(data[0]["lat"]),
                                "longitude": float(data[0]["lon"]),
                            }
            
            _LOGGER.error("All geocoding attempts failed for address: %s", self._sanitize_address_for_logging(address))
            return None
            
        except aiohttp.ClientError as err:
            _LOGGER.error("Error in simplified geocoding: %s", err)
            return None

    async def _async_get_station_coordinates(self, station_id: str | None, address: str | None, station_name: str | None = None) -> dict[str, Any] | None:
        """Get station coordinates using hybrid approach: OSM ID lookup first, then address fallback, then zone search."""
        if not station_id:
            _LOGGER.warning("No station ID provided for geocoding")
            return None
            
        _LOGGER.info("Getting coordinates for station ID %s using hybrid approach", station_id)
        _LOGGER.debug("Station details - ID: %s, Name: %s, Address: %s", station_id, station_name, address)
        
        # Strategy 1: Try OSM lookup by ref:mise ID (most reliable when available)
        _LOGGER.info("Strategy 1: Attempting OSM ID lookup for ref:mise=%s", station_id)
        coordinates = await self._async_geocode_by_osm_id(station_id)
        if coordinates:
            _LOGGER.info("SUCCESS: Strategy 1 - Found coordinates via OSM ID lookup for station %s", station_id)
            return coordinates
        
        _LOGGER.info("FAILED: Strategy 1 - OSM ID lookup failed for station %s", station_id)
        
        # Strategy 2: Fall back to address-based geocoding
        if address:
            _LOGGER.info("Strategy 2: Attempting address geocoding for station %s", station_id)
            coordinates = await self._async_geocode_address_improved(address)
            if coordinates:
                _LOGGER.info("SUCCESS: Strategy 2 - Found coordinates via address geocoding for station %s", station_id)
                return coordinates
            else:
                _LOGGER.warning("FAILED: Strategy 2 - Address geocoding also failed for station %s", station_id)
        else:
            _LOGGER.warning("FAILED: Strategy 2 - No address available for fallback geocoding of station %s", station_id)
        
        # Strategy 3: Zone search fallback
        if address and station_name:
            _LOGGER.info("Strategy 3.0: Attempting zone search fallback for station %s with name '%s'", station_id, station_name)
            coordinates = await self._async_zone_search_fallback(address, station_name)
            if coordinates:
                _LOGGER.info("SUCCESS: Strategy 3.0 - Found coordinates via zone search for station %s", station_id)
                return coordinates
            else:
                _LOGGER.warning("FAILED: Strategy 3.0 - Zone search fallback failed for station %s", station_id)
        else:
            _LOGGER.warning("FAILED: Strategy 3.0 - Cannot attempt zone search - missing address '%s' or station name '%s'",
                          self._sanitize_address_for_logging(address) if address else "None", station_name)
        
        _LOGGER.error("ALL STRATEGIES FAILED: No coordinates found for station %s", station_id)
        return None

    async def _async_geocode_by_osm_id(self, station_id: str) -> dict[str, Any] | None:
        """Try to find station coordinates using OSM ref:mise ID via Overpass API."""
        headers = {
            "user-agent": f"home-assistant-osservaprezzi-carburanti/1.0",
        }
        
        # Overpass QL query to find nodes with ref:mise tag
        overpass_query = f"""
        [out:json][timeout:25];
        nwr["ref:mise"="{station_id}"];
        out geom;
        """
        
        _LOGGER.debug("Overpass query for station %s: %s", station_id, overpass_query.strip())
        
        try:
            _LOGGER.info("Strategy 1.1: Trying Overpass API lookup for ref:mise=%s", station_id)
            async with self.session.post(
                "https://overpass-api.de/api/interpreter",
                data=overpass_query,
                headers=headers,
                timeout=30
            ) as response:
                _LOGGER.debug("Overpass API response status: %s", response.status)
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Overpass API raw response for ref:mise=%s: %s", station_id, data)
                    
                    if data.get("elements") and len(data["elements"]) > 0:
                        element_count = len(data["elements"])
                        _LOGGER.info("Overpass found %d elements for ref:mise=%s", element_count, station_id)
                        
                        element = data["elements"][0]
                        element_type = element.get("type", "unknown")
                        _LOGGER.debug("Processing element type: %s, data: %s", element_type, element)
                        
                        if "lat" in element and "lon" in element:
                            _LOGGER.info("SUCCESS: Strategy 1.1 - Found Overpass node coordinates: lat=%s, lon=%s", element["lat"], element["lon"])
                            return {
                                "latitude": float(element["lat"]),
                                "longitude": float(element["lon"]),
                            }
                        elif "center" in element and "lat" in element["center"] and "lon" in element["center"]:
                            # For ways/relations that have a center point
                            _LOGGER.info("SUCCESS: Strategy 1.1 - Found Overpass center coordinates: lat=%s, lon=%s",
                                       element["center"]["lat"], element["center"]["lon"])
                            return {
                                "latitude": float(element["center"]["lat"]),
                                "longitude": float(element["center"]["lon"]),
                            }
                        else:
                            _LOGGER.warning("FAILED: Strategy 1.1 - Overpass element found but no coordinates: %s", element)
                            _LOGGER.debug("Element keys available: %s", list(element.keys()))
                    else:
                        _LOGGER.warning("FAILED: Strategy 1.1 - Overpass returned no results for ref:mise=%s", station_id)
                        _LOGGER.debug("Overpass response structure: %s", list(data.keys()) if data else "No data")
                elif response.status == 429:
                    _LOGGER.error("FAILED: Strategy 1.1 - Rate limit exceeded for Overpass API")
                else:
                    _LOGGER.error("FAILED: Strategy 1.1 - Overpass API error: %s - %s", response.status, await response.text())
        except aiohttp.ClientError as err:
            _LOGGER.error("FAILED: Strategy 1.1 - Error in Overpass API lookup: %s", err)
        except Exception as err:
            _LOGGER.error("FAILED: Strategy 1.1 - Unexpected error in Overpass lookup: %s", err)
        
        _LOGGER.info("Strategy 1.1 FAILED: No coordinates found via OSM ID lookup for station %s", station_id)
        return None

    async def _async_geocode_address_improved(self, address: str) -> dict[str, Any] | None:
        """Try geocoding with full address only - must be specific station location, not city center."""
        if not address:
            _LOGGER.warning("Strategy 2.0 FAILED: No address provided for geocoding")
            return None
            
        # Validate input
        if not self._validate_address_input(address):
            _LOGGER.warning("Strategy 2.0 FAILED: Invalid address format provided for geocoding")
            return None
        
        _LOGGER.info("Strategy 2.0: Trying full address geocoding: %s", self._sanitize_address_for_logging(address))
        
        # Try ONLY the full address - this is what Strategy 2 should do
        result = await self._async_geocode_address(address)
        if result:
            # Validate that this is a specific address, not just city center
            if await self._async_validate_specific_address(address, result):
                _LOGGER.info("SUCCESS: Strategy 2.0 - Found specific station coordinates via full address geocoding")
                return result
            else:
                _LOGGER.warning("FAILED: Strategy 2.0 - Geocoding returned city center instead of specific address - will proceed to Strategy 3 (zone search)")
                return None  # Return None to trigger Strategy 3
        else:
            _LOGGER.warning("FAILED: Strategy 2.0 - Full address geocoding failed - will proceed to Strategy 3 (zone search)")
            return None  # Return None to trigger Strategy 3

    def _validate_address_input(self, address: str) -> bool:
        """Validate address input to prevent injection and ensure basic format."""
        if not address or not isinstance(address, str):
            return False
        
        # Check for reasonable length
        if len(address) < 3 or len(address) > 200:
            return False
        
        # Check for potentially dangerous characters
        dangerous_patterns = [
            r'<script.*?>.*?</script>',  # Script tags
            r'javascript:',            # JavaScript protocol
            r'data:',                # Data protocol
            r'vbscript:',             # VBScript protocol
        ]
        
        import re
        for pattern in dangerous_patterns:
            if re.search(pattern, address, re.IGNORECASE):
                return False
        
        return True

    async def _async_geocode_city_fallback(self, address: str) -> dict[str, Any] | None:
        """Fallback to city center coordinates."""
        if not address:
            return None
            
        # Validate input
        if not self._validate_address_input(address):
            _LOGGER.warning("Invalid address provided for city fallback")
            return None
        
        import re
        city_match = re.search(r'(\d{5})\s+([A-Z]+)', address.upper() if address else "")
        if city_match:
            city = city_match.group(2)
            city_query = f"{city}, Italy"
        else:
            city_query = "Italy"
        
        headers = {
            "user-agent": f"home-assistant-osservaprezzi-carburanti/1.0",
        }
        
        params = {
            "q": city_query,
            "format": "jsonv2",
            "limit": 1,
        }
        
        try:
            _LOGGER.info("City fallback query: %s", city_query)
            async with self.session.get(NOMINATIM_URL, headers=headers, params=params, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        _LOGGER.warning("Using city center as fallback: %s", city_query)
                        return {
                            "latitude": float(data[0]["lat"]),
                            "longitude": float(data[0]["lon"]),
                        }
        except aiohttp.ClientError as err:
            _LOGGER.error("Error in city fallback: %s", err)
        
        return None

    async def _async_validate_specific_address(self, address: str, coordinates: dict[str, Any]) -> bool:
        """Validate that geocoding returned specific address, not just city center."""
        import re
        
        sanitized_address = self._sanitize_address_for_logging(address)
        display_name = coordinates.get("display_name", "Unknown")
        location_type = coordinates.get("type", "Unknown")
        
        _LOGGER.info("Strategy 2.0.1: VALIDATING ADDRESS SPECIFICITY")
        _LOGGER.info("Strategy 2.0.1: Input address: %s", sanitized_address)
        _LOGGER.info("Strategy 2.0.1: Geocoded display_name: '%s'", display_name)
        _LOGGER.info("Strategy 2.0.1: Geocoded type: '%s'", location_type)
        _LOGGER.info("Strategy 2.0.1: Coordinates: lat=%.6f, lon=%.6f",
                   coordinates.get("latitude", 0), coordinates.get("longitude", 0))
        
        # Extract street information from address
        # Italian addresses typically have: "VIA/STREET NAME NUMBER - CAP CITY (PROVINCE)"
        street_patterns = [
            r'\b(VIA|CORSO|PIAZZA|LARGO|STRADA)\b',  # Street indicators
            r'\d+\s*[A-Z]*/',  # Street numbers
            r'\b\d{5}\b',  # Postal codes
        ]
        
        has_street_info = any(re.search(pattern, address.upper()) for pattern in street_patterns)
        
        _LOGGER.debug("Strategy 2.0.1: Street pattern analysis - has_street_info=%s", has_street_info)
        
        # Check if geocoded result is too generic
        generic_indicators = ["roma", "rome", "italy", "italia"]
        is_generic_location = any(indicator in display_name.lower() for indicator in generic_indicators)
        
        _LOGGER.debug("Strategy 2.0.1: Generic location check - is_generic_location=%s", is_generic_location)
        
        # Additional check: if address only has city and postal code, it's too broad
        city_only_pattern = r'^\d{5}\s+[A-Z]+(?:\s*\([A-Z]{2}\))?$'
        is_city_only = re.match(city_only_pattern, address.strip())
        
        _LOGGER.debug("Strategy 2.0.1: City-only pattern check - is_city_only=%s", is_city_only)
        
        # Decision logic
        if not has_street_info:
            _LOGGER.warning("Strategy 2.0.1: REJECTED - Address appears to be city-level only: %s", sanitized_address)
            return False
        
        if is_city_only:
            _LOGGER.warning("Strategy 2.0.1: REJECTED - Address matches city-only pattern: %s", sanitized_address)
            return False
        
        if is_generic_location and location_type in ["city", "town", "village"]:
            _LOGGER.warning("Strategy 2.0.1: REJECTED - Geocoding returned generic location: '%s' (type: %s)", display_name, location_type)
            return False
        
        _LOGGER.info("Strategy 2.0.1: ACCEPTED - Address appears to be specific location: %s", sanitized_address)
        return True

    async def _async_zone_search_fallback(self, address: str, station_name: str) -> dict[str, Any] | None:
        """Strategy 3: Zone search fallback using city center coordinates and station name matching."""
        import re
        
        # Extract CAP (postal code) and city from address
        _LOGGER.info("Strategy 3.1: Extracting CAP and city from address: %s", self._sanitize_address_for_logging(address))
        
        postal_city_match = re.search(r'(\d{5})\s+([A-Z]+(?:\s+[A-Z]+)*)', address.upper())
        if not postal_city_match:
            _LOGGER.warning("FAILED: Strategy 3.1 - Could not extract CAP and city from address")
            return None
        
        postal_code = postal_city_match.group(1)
        city_name = postal_city_match.group(2).strip()
        
        _LOGGER.info("Strategy 3.1: Extracted CAP=%s, City=%s", postal_code, city_name)
        
        # Get city center coordinates using Overpass API
        _LOGGER.info("Strategy 3.2: Getting city center coordinates for %s", city_name)
        city_coordinates = await self._async_get_city_center_coordinates(city_name)
        if not city_coordinates:
            _LOGGER.warning("FAILED: Strategy 3.2 - Could not get city center coordinates for %s", city_name)
            return None
        
        _LOGGER.info("SUCCESS: Strategy 3.2 - City center coordinates: lat=%s, lon=%s",
                   city_coordinates["latitude"], city_coordinates["longitude"])
        
        # Search zone API for stations within 10km radius
        _LOGGER.info("Strategy 3.3: Searching zone API within 10km of city center")
        zone_stations = await self._async_search_zone_by_coordinates(
            city_coordinates["latitude"],
            city_coordinates["longitude"],
            radius=10
        )
        
        if not zone_stations:
            _LOGGER.warning("FAILED: Strategy 3.3 - No stations found in zone search")
            return None
        
        _LOGGER.info("SUCCESS: Strategy 3.3 - Found %d stations in zone search", len(zone_stations))
        
        # Match station by nomeImpianto
        _LOGGER.info("Strategy 3.4: Matching station by nomeImpianto='%s'", station_name)
        matched_station = await self._async_match_station_by_name(zone_stations, station_name)
        
        if matched_station and "location" in matched_station:
            location = matched_station["location"]
            if "lat" in location and "lng" in location:
                _LOGGER.info("SUCCESS: Strategy 3.4 - Found matching station: lat=%s, lng=%s",
                           location["lat"], location["lng"])
                return {
                    "latitude": float(location["lat"]),
                    "longitude": float(location["lng"]),
                }
        
        _LOGGER.warning("FAILED: Strategy 3.4 - No matching station found for nomeImpianto='%s'", station_name)
        return None

    async def _async_get_city_center_coordinates(self, city_name: str) -> dict[str, Any] | None:
        """Get city center coordinates using Overpass API."""
        headers = {
            "user-agent": f"home-assistant-osservaprezzi-carburanti/1.0",
        }
        
        # Overpass QL query to find city center
        overpass_query = f"""
        [out:json][timeout:25];
        area["name"="{city_name}"]["admin_level"~"8|6"]->.searchArea;
        node(area.searchArea)["place"="city"]["name"];
        out center;
        """
        
        _LOGGER.debug("Overpass city query: %s", overpass_query.strip())
        
        try:
            _LOGGER.info("Strategy 3.2.1: Querying Overpass for city center of %s", city_name)
            async with self.session.post(
                "https://overpass-api.de/api/interpreter",
                data=overpass_query,
                headers=headers,
                timeout=30
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Overpass city response: %s", data)
                    
                    if data.get("elements") and len(data["elements"]) > 0:
                        element = data["elements"][0]
                        if "lat" in element and "lon" in element:
                            _LOGGER.info("SUCCESS: Strategy 3.2.1 - Found city center: lat=%s, lon=%s",
                                       element["lat"], element["lon"])
                            return {
                                "latitude": float(element["lat"]),
                                "longitude": float(element["lon"]),
                            }
                        elif "center" in element:
                            _LOGGER.info("SUCCESS: Strategy 3.2.1 - Found city center: lat=%s, lon=%s",
                                       element["center"]["lat"], element["center"]["lon"])
                            return {
                                "latitude": float(element["center"]["lat"]),
                                "longitude": float(element["center"]["lon"]),
                            }
                    else:
                        _LOGGER.warning("FAILED: Strategy 3.2.1 - No city center found for %s", city_name)
                else:
                    _LOGGER.error("FAILED: Strategy 3.2.1 - Overpass API error: %s", response.status)
        except aiohttp.ClientError as err:
            _LOGGER.error("FAILED: Strategy 3.2.1 - Error getting city center: %s", err)
        
        return None

    async def _async_search_zone_by_coordinates(self, lat: float, lon: float, radius: int = 10) -> list[dict[str, Any]] | None:
        """Search for stations within a radius of given coordinates using zone API."""
        url = f"{BASE_URL}{ZONE_ENDPOINT}"
        
        payload = {
            "points": [
                {
                    "lat": lat,
                    "lng": lon,
                }
            ],
            "radius": radius,
        }
        
        try:
            _LOGGER.info("Strategy 3.3.1: Searching zone API with lat=%s, lon=%s, radius=%s", lat, lon, radius)
            async with self.session.post(url, headers=DEFAULT_HEADERS, json=payload, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Zone API response: %s", data)
                    
                    if data.get("success") and data.get("results"):
                        stations = data["results"]
                        _LOGGER.info("SUCCESS: Strategy 3.3.1 - Found %d stations in zone", len(stations))
                        return stations
                    else:
                        _LOGGER.warning("FAILED: Strategy 3.3.1 - Zone API returned no results")
                else:
                    _LOGGER.error("FAILED: Strategy 3.3.1 - Zone API error: %s", response.status)
        except aiohttp.ClientError as err:
            _LOGGER.error("FAILED: Strategy 3.3.1 - Error searching zone: %s", err)
        
        return None

    async def _async_match_station_by_name(self, stations: list[dict[str, Any]], target_name: str) -> dict[str, Any] | None:
        """Match station by nomeImpianto from zone search results."""
        import re
        
        _LOGGER.info("Strategy 3.4.1: Looking for station with nomeImpianto='%s' among %d stations", target_name, len(stations))
        
        # Normalize target name for comparison
        target_name_normalized = target_name.lower().strip()
        
        for station in stations:
            station_name = station.get("nomeImpianto", "")
            station_name_normalized = station_name.lower().strip()
            
            _LOGGER.debug("Comparing with station: '%s' -> '%s'", station_name, station_name_normalized)
            
            # Exact match
            if station_name_normalized == target_name_normalized:
                _LOGGER.info("SUCCESS: Strategy 3.4.1 - Found exact match: '%s'", station_name)
                return station
            
            # Partial match (remove common words and spaces)
            target_simple = re.sub(r'[^a-z0-9]', '', target_name_normalized)
            station_simple = re.sub(r'[^a-z0-9]', '', station_name_normalized)
            
            if target_simple == station_simple and len(target_simple) > 3:
                _LOGGER.info("SUCCESS: Strategy 3.4.1 - Found normalized match: '%s'", station_name)
                return station
        
        _LOGGER.warning("FAILED: Strategy 3.4.1 - No match found for '%s'", target_name)
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
        
        # Get coordinates using improved geocoding strategy
        coordinates = await self._async_get_station_coordinates(station_id, address, station_name)
        
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
                # Add coordinates from geocoding
                ATTR_LATITUDE: coordinates["latitude"] if coordinates else None,
                ATTR_LONGITUDE: coordinates["longitude"] if coordinates else None,
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
