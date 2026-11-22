from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry

from .config_flow import CannotConnect, InvalidStation, RateLimited, _validate_station
from .const import CONF_STATION_ID, DOMAIN, SUBENTRY_TYPE_STATION


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create a repair flow for a missing station."""
    details = data or {}
    return StationNotFoundRepairFlow(
        details.get("entry_id"),
        details.get("subentry_id"),
        issue_id,
        details.get("station_id"),
    )


class StationNotFoundRepairFlow(RepairsFlow):
    """Replace or remove a missing station subentry."""

    def __init__(
        self,
        entry_id: str | None,
        subentry_id: str | None,
        issue_id: str,
        station_id: str | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.subentry_id = subentry_id or issue_id.removeprefix("station_not_found_")
        self.station_id = station_id
        self.issue_id = issue_id

    def _find_station(self) -> tuple[ConfigEntry, ConfigSubentry] | None:
        """Resolve the hub and station from the issue metadata."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries:
            if self.entry_id and entry.entry_id != self.entry_id:
                continue
            for subentry in entry.subentries.values():
                if subentry.subentry_type != SUBENTRY_TYPE_STATION:
                    continue
                if subentry.subentry_id == self.subentry_id or (
                    self.station_id and str(subentry.data.get(CONF_STATION_ID)) == self.station_id
                ):
                    return entry, subentry
        return None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        """Choose how to repair the station."""
        if self._find_station() is None:
            return self.async_create_entry(data={})
        return self.async_show_menu(
            step_id="init", menu_options=["replace_station", "remove_station"]
        )

    async def async_step_replace_station(self, user_input: dict[str, Any] | None = None) -> Any:
        """Validate and replace the station."""
        found = self._find_station()
        if found is None:
            return self.async_create_entry(data={})
        entry, subentry = found
        errors: dict[str, str] = {}
        if user_input is not None:
            station_id = str(user_input.get(CONF_STATION_ID, "")).strip()
            if not station_id:
                errors[CONF_STATION_ID] = "invalid_station"
            elif any(
                other.subentry_id != subentry.subentry_id and other.unique_id == station_id
                for other in entry.subentries.values()
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
                    self.hass.config_entries.async_update_subentry(
                        entry,
                        subentry,
                        unique_id=station_id,
                        title=info["name"],
                        data={**subentry.data, CONF_STATION_ID: station_id},
                    )
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
            found = self._find_station()
            if found is not None:
                entry, subentry = found
                self.hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
            issue_registry.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="remove_station")
