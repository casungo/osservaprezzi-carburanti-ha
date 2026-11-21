"""Privacy-safe diagnostics for Osservaprezzi Carburanti."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (ATTR_PRICE_IS_STALE, CONF_CRON_EXPRESSION, CONF_PRICE_STALE_HOURS,
                    CONF_STATION_ID, DEFAULT_CRON_EXPRESSION, DEFAULT_PRICE_STALE_HOURS, DOMAIN)
from .cron_helper import get_next_run_time
from .price_metadata import price_metadata
from .coordinator import CarburantiDataUpdateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics without station identity or location data."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = (
        entry_data.get("coordinator") if isinstance(entry_data, dict) else None
    )
    if not isinstance(coordinator, CarburantiDataUpdateCoordinator):
        return {
            "entry": {
                "data": async_redact_data(dict(entry.data), {CONF_STATION_ID}),
                "options": dict(entry.options),
            },
            "loaded": False,
        }

    data = coordinator.data or {}
    fuels = data.get("fuels", {})
    services = data.get("services", [])
    opening_hours = data.get("opening_hours", [])
    try:
        next_refresh = get_next_run_time(
            entry.options.get(CONF_CRON_EXPRESSION, DEFAULT_CRON_EXPRESSION)
        ).isoformat()
    except (ImportError, TypeError, ValueError):
        next_refresh = None
    stale_hours = int(entry.options.get(CONF_PRICE_STALE_HOURS, DEFAULT_PRICE_STALE_HOURS))
    stale_count = sum(
        price_metadata(price=None, previous_price=None, last_update=fuel.get("last_update"),
                       stale_hours=stale_hours)[ATTR_PRICE_IS_STALE] is True
        for fuel in fuels.values() if isinstance(fuel, dict)
    ) if isinstance(fuels, dict) else 0
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), {CONF_STATION_ID}),
            "options": dict(entry.options),
        },
        "loaded": True,
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_refresh_from_cache": coordinator.last_refresh_from_cache,
            "station_not_found": coordinator.station_not_found,
            "data_last_update": data.get("last_update"),
            "stale_fuel_count": stale_count,
            "next_scheduled_refresh": next_refresh,
            "has_data": bool(coordinator.data),
            "fuel_count": len(fuels) if isinstance(fuels, dict) else 0,
            "service_count": len(services) if isinstance(services, list) else 0,
            "opening_hours_count": (
                len(opening_hours) if isinstance(opening_hours, list) else 0
            ),
        },
        "registry": coordinator.csv_manager.registry_status(),
    }
