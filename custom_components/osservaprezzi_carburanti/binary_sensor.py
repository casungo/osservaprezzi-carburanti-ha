from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ADDITIONAL_SERVICES, DOMAIN
from .coordinator import CarburantiDataUpdateCoordinator
from .entity import (
    OsservaprezziBaseEntity,
    ScheduleAwareEntity,
    _find_schedule_for_day,
    _get_available_service_ids,
    _has_valid_opening_hours,
    _schedule_intervals_for_date,
)

SERVICE_ID_TO_NAME = {
    "1": "Food & Beverage",
    "2": "Officina",
    "3": "Sosta camper/tir",
    "4": "Scarico camper",
    "5": "Area bambini",
    "6": "Bancomat",
    "7": "Servizi per disabili",
    "8": "Wi-Fi",
    "9": "Gommista",
    "10": "Autolavaggio",
    "11": "Ricarica elettrica",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities for a station."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    known_unique_ids: set[str] = set()
    initial_discovery = True

    @callback
    def _async_discover_entities() -> None:
        data = coordinator.data or {}
        entities: list[BinarySensorEntity] = []
        if _has_valid_opening_hours(data):
            entities.append(StationOpenClosedBinarySensor(coordinator, entry))
        available_service_ids = _get_available_service_ids(data.get("services", []))
        for service_id, service_info in ADDITIONAL_SERVICES.items():
            if service_id in available_service_ids:
                entities.append(StationServiceBinarySensor(coordinator, entry, service_id, service_info))
        new_entities = [
            entity for entity in entities if entity._attr_unique_id not in known_unique_ids
        ]
        if not new_entities:
            return
        known_unique_ids.update(entity._attr_unique_id for entity in new_entities)
        async_add_entities(new_entities, update_before_add=initial_discovery)

    _async_discover_entities()
    initial_discovery = False
    entry.async_on_unload(coordinator.async_add_listener(_async_discover_entities))


class StationOpenClosedBinarySensor(ScheduleAwareEntity, BinarySensorEntity):
    """Binary sensor indicating if the station is currently open."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:storefront"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the open/closed binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Aperta"
        self._attr_unique_id = f"{self._station_id}_open_closed"

    def _is_currently_open(self) -> bool:
        """Check if the station is currently open."""
        opening_hours = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        if not opening_hours:
            return False

        now = dt_util.now()
        for day_offset in (-1, 0):
            local_date = now.date() + timedelta(days=day_offset)
            schedule = _find_schedule_for_day(
                opening_hours,
                local_date.weekday() + 1,
                local_date,
            )
            if any(
                opens_at <= now < closes_at
                for opens_at, closes_at in _schedule_intervals_for_date(
                    schedule, local_date, now.tzinfo
                )
            ):
                return True
        return False

    @property
    def is_on(self) -> bool:
        """Return True if the station is currently open."""
        return self._is_currently_open()

    @property
    def available(self) -> bool:
        """Return True if schedule data is available."""
        return _has_valid_opening_hours(self.coordinator.data)


class StationServiceBinarySensor(OsservaprezziBaseEntity, BinarySensorEntity):
    """Representation of a binary sensor for a specific station service."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        entry: ConfigEntry,
        service_id: str,
        service_info: dict[str, str],
    ) -> None:
        """Initialize the service binary sensor."""
        super().__init__(coordinator, entry)
        self._service_id = service_id
        self._service_info = service_info
        self._attr_unique_id = f"{self._station_id}_service_{service_id}"
        self._attr_name = SERVICE_ID_TO_NAME.get(service_id, service_info["name"])
        self._attr_icon = service_info["icon"]

    @property
    def is_on(self) -> bool:
        """Return True if the service is available at the station."""
        if not self.coordinator.data:
            return False
        return self._service_id in _get_available_service_ids(self.coordinator.data.get("services", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return service metadata."""
        return {
            "service_id": self._service_id,
            "service_name": self._service_info.get("name"),
            "service_description": self._service_info.get("description"),
            "service_icon": self._service_info.get("icon"),
            "service_image_url": self._service_info.get("image_url"),
        }
