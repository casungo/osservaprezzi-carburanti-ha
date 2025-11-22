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
    DEFAULT_HEADERS,
    CONF_CONFIG_TYPE,
    CONF_TYPE_STATION,
    FUEL_TYPES,
)
from .cron_helper import validate_cron_expression

_LOGGER = logging.getLogger(__name__)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidStation(HomeAssistantError):
    """Error to indicate there is an invalid station."""

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


def _get_fuel_types() -> dict[int, str]:
    """Get fuel types from static mapping."""
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
