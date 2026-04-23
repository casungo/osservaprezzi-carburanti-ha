from __future__ import annotations
import logging
from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import (
    DOMAIN,
    CONF_STATION_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
)
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([StationGeoLocation(coordinator, entry)], update_before_add=True)


class StationGeoLocation(CoordinatorEntity, GeolocationEvent):
    _attr_icon = "mdi:gas-station"
    _attr_source = DOMAIN
    _attr_translation_key = "station_geolocation"
    _attr_distance = None

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_unique_id = f"{self._station_id}_geolocation"

    @property
    def device_info(self) -> DeviceInfo:
        station_info = self.coordinator.data.get("station_info", {}) if self.coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    @property
    def source(self) -> str:
        return DOMAIN

    @property
    def latitude(self) -> float | None:
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return None
        return self.coordinator.data["station_info"].get(ATTR_LATITUDE)

    @property
    def longitude(self) -> float | None:
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return None
        return self.coordinator.data["station_info"].get(ATTR_LONGITUDE)

    @property
    def name(self) -> str | None:
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return None
        station_info = self.coordinator.data["station_info"]
        return station_info.get("nomeImpianto") or station_info.get("name")

    @property
    def available(self) -> bool:
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return False
        station_info = self.coordinator.data["station_info"]
        return (station_info.get(ATTR_LATITUDE) is not None and
                station_info.get(ATTR_LONGITUDE) is not None)
