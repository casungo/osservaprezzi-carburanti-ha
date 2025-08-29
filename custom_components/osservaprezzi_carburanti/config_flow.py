from __future__ import annotations
import logging
from typing import Any
from datetime import time
import asyncio
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import (
    DOMAIN,
    CONF_STATION_ID,
    CONF_UPDATE_TIME,
    DEFAULT_UPDATE_TIME,
    BASE_URL,
    STATION_ENDPOINT,
    DEFAULT_HEADERS
)

_LOGGER = logging.getLogger(__name__)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidStation(HomeAssistantError):
    """Error to indicate there is an invalid station."""

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
        errors = {}
        if user_input is not None:
            try:
                station_id = user_input[CONF_STATION_ID]
                await self.async_set_unique_id(station_id)
                self._abort_if_unique_id_configured()
                station_info = await self._validate_station(station_id)
                
                return self.async_create_entry(
                    title=station_info["name"],
                    data={CONF_STATION_ID: station_id},
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

    async def _validate_station(self, station_id: str) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=station_id)}"
        try:
            async with session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    if not data.get("id") or not data.get("name"):
                        raise InvalidStation("Invalid station data received")
                    return {
                        "id": data.get("id"),
                        "name": data.get("name"),
                    }
                elif response.status == 404:
                    raise InvalidStation("Station not found")
                else:
                    raise CannotConnect(f"Service error: {response.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(f"Connection error: {err}")

# Use the modern OptionsFlowWithConfigEntry base class
class OptionsFlowHandler(config_entries.OptionsFlowWithConfigEntry):
    """Handle an options flow for Osservaprezzi Carburanti."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors = {}
        if user_input is not None:
            update_time_str = user_input[CONF_UPDATE_TIME]
            try:
                time.fromisoformat(update_time_str)
                # No need to call self.async_create_entry for options flow
                return self.async_create_entry(title="", data=user_input)
            except ValueError:
                errors["base"] = "invalid_time_format"

        # `self.options` is automatically provided by OptionsFlowWithConfigEntry
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_TIME,
                    default=self.options.get(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )