from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry

from .config_flow import CannotConnect, InvalidStation, RateLimited, _validate_station
from .const import CONF_STATION_ID, DOMAIN


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create a repair flow for a missing station."""
    return StationNotFoundRepairFlow(
        (data or {}).get("entry_id", issue_id.removeprefix("station_not_found_")), issue_id
    )


class StationNotFoundRepairFlow(RepairsFlow):
    """Replace or remove a missing station."""

    def __init__(self, entry_id: str, issue_id: str) -> None:
        self.entry_id = entry_id
        self.issue_id = issue_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        """Choose how to repair the station."""
        if self.hass.config_entries.async_get_entry(self.entry_id) is None:
            return self.async_create_entry(data={})
        return self.async_show_menu(
            step_id="init", menu_options=["replace_station", "remove_station"]
        )

    async def async_step_replace_station(self, user_input: dict[str, Any] | None = None) -> Any:
        """Validate and replace the station."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return self.async_create_entry(data={})
        errors: dict[str, str] = {}
        if user_input is not None:
            station_id = str(user_input.get(CONF_STATION_ID, "")).strip()
            unique_id = f"station_{station_id}"
            if not station_id:
                errors[CONF_STATION_ID] = "invalid_station"
            elif any(
                other.entry_id != entry.entry_id and other.unique_id == unique_id
                for other in self.hass.config_entries.async_entries(DOMAIN)
            ):
                errors[CONF_STATION_ID] = "already_configured"
            else:
                try:
                    info = await _validate_station(self.hass, station_id)
                except InvalidStation:
                    errors[CONF_STATION_ID] = "invalid_station"
                except RateLimited:
                    errors[CONF_STATION_ID] = "rate_limited"
                except CannotConnect:
                    errors[CONF_STATION_ID] = "cannot_connect"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        unique_id=unique_id,
                        title=info["name"],
                        data={**entry.data, CONF_STATION_ID: station_id},
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    issue_registry.async_delete_issue(self.hass, DOMAIN, self.issue_id)
                    return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="replace_station",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
            errors=errors,
        )

    async def async_step_remove_station(self, user_input: dict[str, Any] | None = None) -> Any:
        """Confirm removal of the station."""
        if user_input is not None:
            await self.hass.config_entries.async_remove(self.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="remove_station")
