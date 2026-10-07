from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import CarburantiDataUpdateCoordinator
from .entity import OsservaprezziBaseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a refresh button for this station, including before its first refresh."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([StationRefreshButton(coordinator, entry)])


class StationRefreshButton(OsservaprezziBaseEntity, ButtonEntity):
    """Refresh one station through its existing coordinator."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_prices"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the station refresh action."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._station_id}_refresh_prices"

    @property
    def available(self) -> bool:
        """Allow retrying even when the last station refresh failed."""
        return True

    async def async_press(self) -> None:
        """Request a station refresh and report an unsuccessful update."""
        await self.coordinator.async_request_refresh()
        if (
            not self.coordinator.last_update_success
            or self.coordinator.last_refresh_from_cache
        ):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="refresh_failed")
