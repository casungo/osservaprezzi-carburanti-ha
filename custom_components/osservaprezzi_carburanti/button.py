from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import CarburantiDataUpdateCoordinator
from .entity import OsservaprezziBaseEntity
from .runtime import OsservaprezziConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OsservaprezziConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a refresh button for this station, including before its first refresh."""
    for subentry_id, station in entry.runtime_data.stations.items():
        coordinator = station.coordinator
        async_add_entities(
            [StationRefreshButton(coordinator, coordinator.config_entry)],
            config_subentry_id=subentry_id,
        )


class StationRefreshButton(OsservaprezziBaseEntity, ButtonEntity):
    """Refresh one station through its existing coordinator."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_prices"
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
        await self.coordinator.async_request_manual_refresh()
        if not self.coordinator.last_update_success or self.coordinator.last_refresh_from_cache:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="refresh_failed")
