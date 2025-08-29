from __future__ import annotations
import logging
from datetime import time
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_change
from .const import DOMAIN, CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    station_id = entry.data.get("station_id")
    coordinator = CarburantiDataUpdateCoordinator(hass, station_id)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        raise
    
    update_time_str = entry.options.get(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME)
    try:
        update_time = time.fromisoformat(update_time_str)
    except ValueError:
        _LOGGER.warning(
            "Invalid time format '%s', falling back to default '%s'",
            update_time_str,
            DEFAULT_UPDATE_TIME,
        )
        update_time = time.fromisoformat(DEFAULT_UPDATE_TIME)

    @callback
    async def _request_refresh(now):
        _LOGGER.debug("Requesting refresh for station %s at scheduled time", station_id)
        await coordinator.async_request_refresh()

    listener = async_track_time_change(
        hass,
        _request_refresh,
        hour=update_time.hour,
        minute=update_time.minute,
        second=0,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "listener": listener,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data["listener"]()
        await entry_data["coordinator"].async_shutdown()
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)