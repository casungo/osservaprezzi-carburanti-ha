"""Privacy-safe diagnostics for Osservaprezzi Carburanti."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_STATION_ID, DOMAIN
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
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), {CONF_STATION_ID}),
            "options": dict(entry.options),
        },
        "loaded": True,
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "has_data": bool(coordinator.data),
            "fuel_count": len(fuels) if isinstance(fuels, dict) else 0,
            "service_count": len(services) if isinstance(services, list) else 0,
            "opening_hours_count": (
                len(opening_hours) if isinstance(opening_hours, list) else 0
            ),
        },
        "registry": coordinator.csv_manager.registry_status(),
    }
