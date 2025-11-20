from __future__ import annotations
import logging
from typing import Any
from datetime import time
import asyncio
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectOptionDict,
)
from .const import (
    DOMAIN,
    CONF_STATION_ID,
    CONF_CRON_EXPRESSION,
    DEFAULT_CRON_EXPRESSION,
    BASE_URL,
    STATION_ENDPOINT,
    ZONE_ENDPOINT,
    FUELS_ENDPOINT,
    DEFAULT_HEADERS,
    CONF_CONFIG_TYPE,
    CONF_TYPE_STATION,
    CONF_TYPE_ZONE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS,
    CONF_FUEL_TYPE,
    CONF_IS_SELF,
    CONF_POINTS,
    FUEL_TYPES,
)
from .cron_helper import validate_cron_expression

_LOGGER = logging.getLogger(__name__)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidStation(HomeAssistantError):
    """Error to indicate there is an invalid station."""

class InvalidZone(HomeAssistantError):
    """Error to indicate there is an invalid zone configuration."""

class InvalidCronExpression(HomeAssistantError):
    """Error to indicate there is an invalid cron expression."""

async def _validate_station(hass: HomeAssistant, station_id: str) -> dict[str, Any]:
    """Validate the station_id by making an API call."""
    session = async_get_clientsession(hass)
    url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=station_id)}"
    try:
        async with session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
            if response.status == 200:
                data = await response.json()
                if not data.get("id") or not data.get("name"):
                    raise InvalidStation("Invalid station data received")
                return {"name": data["name"]}
            elif response.status == 404:
                raise InvalidStation("Station not found")
            else:
                raise CannotConnect(f"Service error: {response.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise CannotConnect(f"Connection error: {err}")

async def _validate_zone(hass: HomeAssistant, zone_data: dict[str, Any]) -> None:
    """Validate the zone configuration by making a test API call."""
    session = async_get_clientsession(hass)
    url = f"{BASE_URL}{ZONE_ENDPOINT}"
    payload = {
        "points": [{"lat": zone_data[CONF_LATITUDE], "lng": zone_data[CONF_LONGITUDE]}],
        "radius": zone_data[CONF_RADIUS],
    }
    try:
        async with session.post(url, headers=DEFAULT_HEADERS, json=payload, timeout=30) as response:
            if response.status == 200:
                data = await response.json()
                if not data.get("success"):
                    raise InvalidZone("API reported failure for zone search")
            else:
                raise CannotConnect(f"Service error: {response.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise CannotConnect(f"Connection error: {err}")
async def _validate_zone_multi_points(hass: HomeAssistant, zone_data: dict[str, Any]) -> None:
    """Validate zone configuration with multiple points by making a test API call."""
    session = async_get_clientsession(hass)
    url = f"{BASE_URL}{ZONE_ENDPOINT}"
    
    # Support both single point (backward compatibility) and multiple points
    points_data = zone_data.get(CONF_POINTS, [
        {"lat": zone_data[CONF_LATITUDE], "lng": zone_data[CONF_LONGITUDE]}
    ])
    
    payload = {
        "points": points_data,
        "radius": zone_data[CONF_RADIUS],
    }
    try:
        async with session.post(url, headers=DEFAULT_HEADERS, json=payload, timeout=30) as response:
            if response.status == 200:
                data = await response.json()
                if not data.get("success"):
                    raise InvalidZone("API reported failure for zone search")
            else:
                raise CannotConnect(f"Service error: {response.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise CannotConnect(f"Connection error: {err}")


async def _fetch_fuel_types(hass: HomeAssistant) -> dict[int, str]:
    """Fetch fuel types from API or fallback to static mapping."""
    session = async_get_clientsession(hass)
    url = f"{BASE_URL}{FUELS_ENDPOINT}"
    try:
        _LOGGER.debug("Attempting to fetch fuel types from: %s", url)
        async with session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
            _LOGGER.debug("Fuel types API response status: %s", response.status)
            if response.status == 200:
                try:
                    data = await response.json()
                    _LOGGER.debug("Fuel types API response data: %s", data)
                    
                    # Validate response structure
                    if not isinstance(data, dict) or "results" not in data:
                        _LOGGER.warning("Invalid fuel types API response structure, using static mapping")
                        return FUEL_TYPES
                    
                    fuel_types = {}
                    results = data.get("results", [])
                    
                    if not isinstance(results, list):
                        _LOGGER.warning("Invalid results format in fuel types API response, using static mapping")
                        return FUEL_TYPES
                    
                    for fuel in results:
                        if not isinstance(fuel, dict):
                            _LOGGER.warning("Invalid fuel item format in API response, skipping")
                            continue
                            
                        fuel_id = fuel.get("id", "")
                        description = fuel.get("description", "")
                        
                        if not fuel_id or not description:
                            _LOGGER.warning("Missing fuel ID or description in API response, skipping")
                            continue
                            
                        if "-" in fuel_id:
                            # Parse format like "1-x", "1-1", "1-0"
                            try:
                                base_id, service_type = fuel_id.split("-", 1)
                                if service_type == "x":  # Only add generic fuel types for selection
                                    fuel_types[int(base_id)] = description
                            except ValueError as e:
                                _LOGGER.warning("Invalid fuel ID format '%s': %s", fuel_id, e)
                                continue
                    
                    # Fallback to static mapping if no results
                    if not fuel_types:
                        _LOGGER.warning("No valid fuel types parsed from API, using static mapping")
                        return FUEL_TYPES
                    
                    _LOGGER.debug("Successfully parsed %d fuel types from API", len(fuel_types))
                    return fuel_types
                    
                except ValueError as e:
                    _LOGGER.error("Failed to parse fuel types API response as JSON: %s", e)
                    return FUEL_TYPES
            else:
                _LOGGER.warning("Failed to fetch fuel types, status code: %s, using static mapping", response.status)
                return FUEL_TYPES
                
    except aiohttp.ClientError as err:
        _LOGGER.error("Network error fetching fuel types: %s", err)
        return FUEL_TYPES
    except asyncio.TimeoutError as err:
        _LOGGER.error("Timeout fetching fuel types: %s", err)
        return FUEL_TYPES
    except Exception as err:
        _LOGGER.exception("Unexpected error fetching fuel types: %s", err)
        return FUEL_TYPES


class OsservaprezziCarburantiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - directly ask for station ID."""
        errors = {}
        if user_input is not None:
            try:
                station_id = user_input[CONF_STATION_ID]
                await self.async_set_unique_id(f"station_{station_id}")
                self._abort_if_unique_id_configured()
                
                station_info = await _validate_station(self.hass, station_id)
                
                data = {
                    CONF_CONFIG_TYPE: CONF_TYPE_STATION,
                    CONF_STATION_ID: station_id,
                }
                return self.async_create_entry(
                    title=station_info["name"],
                    data=data,
                )
            except InvalidStation:
                errors["base"] = "invalid_station"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the setup of a single station."""
        errors = {}
        if user_input is not None:
            try:
                station_id = user_input[CONF_STATION_ID]
                await self.async_set_unique_id(f"station_{station_id}")
                self._abort_if_unique_id_configured()
                
                station_info = await _validate_station(self.hass, station_id)
                
                data = {
                    CONF_CONFIG_TYPE: CONF_TYPE_STATION,
                    CONF_STATION_ID: station_id,
                }
                return self.async_create_entry(
                    title=station_info["name"],
                    data=data,
                )
            except InvalidStation:
                errors["base"] = "invalid_station"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="station",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the setup of a zone search."""
        errors = {}
        if user_input is not None:
            try:
                await _validate_zone_multi_points(self.hass, user_input)

                unique_id = (
                    f"zone_{user_input[CONF_LATITUDE]}_{user_input[CONF_LONGITUDE]}_"
                    f"{user_input[CONF_RADIUS]}_{user_input[CONF_FUEL_TYPE]}_"
                    f"{user_input[CONF_IS_SELF]}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                # Get fuel types dynamically
                fuel_types = await _fetch_fuel_types(self.hass)
                fuel_name = fuel_types.get(user_input[CONF_FUEL_TYPE], "Unknown Fuel")
                is_self_str = "Self" if user_input[CONF_IS_SELF] else "Servito"
                title = f"Zona: {fuel_name} ({is_self_str})"

                data = {
                    CONF_CONFIG_TYPE: CONF_TYPE_ZONE,
                    **user_input,
                }
                return self.async_create_entry(title=title, data=data)

            except InvalidZone:
                errors["base"] = "invalid_zone"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
        
        # Default coordinates to home assistant instance
        lat = self.hass.config.latitude
        lon = self.hass.config.longitude

        # Get fuel types dynamically for the form
        try:
            fuel_types = await _fetch_fuel_types(self.hass)
        except Exception as e:
            _LOGGER.error("Failed to fetch fuel types for zone form: %s", e)
            fuel_types = FUEL_TYPES  # Use static fallback
        
        zone_schema = vol.Schema(
            {
                vol.Required(CONF_LATITUDE, default=lat): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=lon): vol.Coerce(float),
                vol.Required(CONF_RADIUS, default=2.0): vol.Coerce(float),
                vol.Required(CONF_FUEL_TYPE): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=key, label=name)
                            for key, name in fuel_types.items()
                        ]
                    )
                ),
                vol.Required(CONF_IS_SELF, default=True): bool,
            }
        )

        return self.async_show_form(
            step_id="zone",
            data_schema=zone_schema,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlowWithConfigEntry):
    """Handle an options flow for Osservaprezzi Carburanti."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors = {}
        if user_input is not None:
            cron_expr = user_input[CONF_CRON_EXPRESSION]
            old_cron_expr = self.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
            if validate_cron_expression(cron_expr):
                if cron_expr != old_cron_expr:
                    _LOGGER.info("Cron expression updated from '%s' to '%s' for %s", old_cron_expr, cron_expr, self.config_entry.title)
                return self.async_create_entry(title="", data=user_input)
            else:
                _LOGGER.warning("Invalid cron expression submitted: '%s' for %s", cron_expr, self.config_entry.title)
                errors["base"] = "invalid_cron_expression"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CRON_EXPRESSION,
                    default=self.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
