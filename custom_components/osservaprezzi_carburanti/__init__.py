from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval
from .const import DOMAIN, CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION
from .coordinator import CarburantiDataUpdateCoordinator
from .cron_helper import get_schedule_interval

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CarburantiDataUpdateCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        raise
    
    # Get cron expression from options
    cron_expression = entry.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
    _LOGGER.info("Setting up cron schedule for %s with expression: %s", entry.title, cron_expression)
    
    @callback
    async def _request_refresh(now):
        _LOGGER.info("Executing scheduled refresh for %s at %s", entry.title, now)
        await coordinator.async_request_refresh()
        
        # Reschedule for next run time
        try:
            interval = get_schedule_interval(cron_expression)
            next_run_time = datetime.now() + interval
            _LOGGER.info("Rescheduling next refresh for %s in %s (at %s)", entry.title, interval, next_run_time)
            # Create new listener for next run
            new_listener = async_track_time_interval(
                hass,
                _request_refresh,
                interval
            )
            # Replace the listener in hass.data
            hass.data[DOMAIN][entry.entry_id]["listener"]()
            hass.data[DOMAIN][entry.entry_id]["listener"] = new_listener
        except Exception as e:
            _LOGGER.error("Failed to reschedule: %s", e)

    # Set initial schedule
    try:
        interval = get_schedule_interval(cron_expression)
        next_run_time = datetime.now() + interval
        _LOGGER.info("Initial cron schedule for %s: next refresh in %s (at %s)", entry.title, interval, next_run_time)
        listener = async_track_time_interval(
            hass,
            _request_refresh,
            interval
        )
    except Exception as e:
        _LOGGER.error("Failed to set up cron schedule: %s", e)
        return False

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
    _LOGGER.info("Reloading entry %s to apply new cron schedule", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)