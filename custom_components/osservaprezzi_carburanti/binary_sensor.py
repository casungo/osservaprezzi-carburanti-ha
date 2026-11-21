from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ADDITIONAL_SERVICES,
    CONF_STATION_ID,
    DOMAIN,
    SERVICE_ID_TO_TRANSLATION_KEY,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .entity import (
    OsservaprezziBaseEntity,
    ScheduleAwareEntity,
    _find_schedule_for_day,
    _get_available_service_ids,
    _has_valid_opening_hours,
    _schedule_intervals_for_date,
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities for a station."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    station_id = entry.data[CONF_STATION_ID]
    known_unique_ids: set[str] = set()

    @callback
    def _async_discover_entities() -> None:
        data = coordinator.data or {}
        entities: list[BinarySensorEntity] = []
        if (
            _has_valid_opening_hours(data)
            and f"{station_id}_open_closed" not in known_unique_ids
        ):
            entities.append(StationOpenClosedBinarySensor(coordinator, entry))
        available_service_ids = _get_available_service_ids(data.get("services", []))
        for service_id, service_info in ADDITIONAL_SERVICES.items():
            if (
                service_id in available_service_ids
                and f"{station_id}_service_{service_id}" not in known_unique_ids
            ):
                entities.append(StationServiceBinarySensor(coordinator, entry, service_id, service_info))
        if not entities:
            return
        known_unique_ids.update(entity._attr_unique_id for entity in entities)
        async_add_entities(entities, update_before_add=False)

    _async_discover_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_discover_entities))


class StationOpenClosedBinarySensor(ScheduleAwareEntity, BinarySensorEntity):
    """Binary sensor indicating if the station is currently open."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:storefront"
    _open: bool | None = None

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the open/closed binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "station_open_closed"
        self._attr_unique_id = f"{self._station_id}_open_closed"
        self._open = self._is_currently_open()

    def _is_currently_open(self, now: datetime | None = None) -> bool:
        """Check if the station is currently open."""
        opening_hours = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        if not opening_hours:
            return False

        now = now or dt_util.now()
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

    @callback
    def _handle_coordinator_update(self) -> None:
        self._open = self._is_currently_open()
        super()._handle_coordinator_update()

    @callback
    def _handle_time_tick(self, now: Any) -> None:
        self._open = self._is_currently_open(dt_util.as_local(now))
        super()._handle_time_tick(now)

    @property
    def is_on(self) -> bool:
        """Return True if the station is currently open."""
        if self._open is None:
            return self._is_currently_open()
        return self._open

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return today's schedule and service availability."""
        now = dt_util.now()
        rows = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        schedule = _find_schedule_for_day(rows, now.isoweekday(), now.date())
        result: dict[str, Any] = {"self_service_available": None, "served_available": None, "today_schedule": None}
        if schedule is None:
            return result
        for key, field in (("self_service_available", "flagSelf"), ("served_available", "flagServito")):
            if schedule.get(field) is not None:
                result[key] = bool(schedule[field])
        if schedule.get("flagChiusura"):
            result["today_schedule"] = "Closed"
        elif schedule.get("flagH24"):
            result["today_schedule"] = "24/7"
        else:
            intervals = _schedule_intervals_for_date(schedule, now.date(), now.tzinfo)
            result["today_schedule"] = ", ".join(f"{start:%H:%M}-{end:%H:%M}" for start, end in intervals) or None
        return result

    @property
    def available(self) -> bool:
        """Return True if schedule data is available."""
        return bool(super().available and _has_valid_opening_hours(self.coordinator.data))


class StationServiceBinarySensor(OsservaprezziBaseEntity, BinarySensorEntity):
    """Representation of a binary sensor for a specific station service."""

    _attr_entity_registry_enabled_default = False
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
        translation_key = SERVICE_ID_TO_TRANSLATION_KEY.get(service_id)
        if translation_key:
            self._attr_translation_key = translation_key
        else:
            self._attr_name = service_info["name"]
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
