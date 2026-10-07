from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_FUEL_TYPE_NAME,
    ATTR_IS_SELF,
    ATTR_LAST_UPDATE,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_PREVIOUS_PRICE,
    ATTR_PRICE_CHANGED_AT,
    ATTR_STATION_ADDRESS,
    ATTR_STATION_BRAND,
    ATTR_STATION_NAME,
    ATTR_VALIDITY_DATE,
    CONF_STATION_ID,
    CONF_PRICE_STALE_HOURS,
    DEFAULT_PRICE_STALE_HOURS,
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .data_helpers import fuel_display_name, station_display_name
from .price_metadata import price_metadata as _price_metadata
from .entity import (
    OsservaprezziBaseEntity,
    ScheduleAwareEntity,
    _find_schedule_for_day,
    _has_valid_opening_hours,
    _schedule_intervals_for_date,
)

INFO_SENSOR_DESCRIPTORS: tuple[tuple[str, str, str], ...] = (
    ("name", "station_name", "mdi:gas-station"),
    ("nomeImpianto", "station_display_name", "mdi:gas-station"),
    ("id", "station_id", "mdi:identifier"),
    ("brand", "station_brand", "mdi:tag"),
    ("company", "station_company", "mdi:office-building"),
    ("phoneNumber", "station_phone", "mdi:phone"),
    ("email", "station_email", "mdi:email"),
    ("website", "station_website", "mdi:web"),
)


def _get_fuel_icon(fuel_name: str) -> str:
    """Return the icon for the fuel type."""
    fuel_name_lower = fuel_name.lower()
    if "benzina" in fuel_name_lower:
        return "mdi:gas-station"
    if "gasolio" in fuel_name_lower or "diesel" in fuel_name_lower:
        return "mdi:fuel"
    if "gpl" in fuel_name_lower:
        return "mdi:gas-cylinder"
    if "metano" in fuel_name_lower:
        return "mdi:molecule-co2"
    return "mdi:currency-eur"




async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for a station."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    station_id = entry.data[CONF_STATION_ID]
    known_unique_ids: set[str] = set()

    @callback
    def _async_discover_entities() -> None:
        data = coordinator.data or {}
        entities: list[SensorEntity] = []
        fuels = data.get("fuels", {})
        if isinstance(fuels, dict):
            for fuel_key in fuels:
                if not isinstance(fuel_key, str) or "_" not in fuel_key:
                    continue
                if f"{station_id}_{fuel_key}" in known_unique_ids:
                    continue
                entities.append(OsservaprezziStationSensor(coordinator, entry, fuel_key))
        station_info = data.get("station_info", {})
        if isinstance(station_info, dict):
            for info_key, translation_key, icon in INFO_SENSOR_DESCRIPTORS:
                if (
                    station_info.get(info_key)
                    and f"{station_id}_{info_key}" not in known_unique_ids
                ):
                    entities.append(
                        StationInfoSensor(coordinator, entry, info_key, translation_key, icon)
                    )
        if f"{station_id}_location" not in known_unique_ids:
            entities.append(StationLocationSensor(coordinator, entry))
        if (
            _has_valid_opening_hours(data)
            and f"{station_id}_next_change" not in known_unique_ids
        ):
            entities.append(StationNextChangeSensor(coordinator, entry))
        if not entities:
            return
        known_unique_ids.update(entity._attr_unique_id for entity in entities)
        async_add_entities(entities, update_before_add=False)

    _async_discover_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_discover_entities))


class OsservaprezziStationSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a single fuel price for a specific station."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "€/L"

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        entry: ConfigEntry,
        fuel_key: str,
    ) -> None:
        """Initialize the station fuel sensor."""
        super().__init__(coordinator, entry)
        self._fuel_key = fuel_key

        self._fuel_name, self._service_type = fuel_key.rsplit("_", 1)
        self._fuel_display_name = fuel_display_name(self._fuel_name)
        self._is_self_service = self._service_type == "self"
        self._attr_name = f"{self._fuel_display_name} {self._service_type.title()}"
        self._attr_unique_id = f"{self._station_id}_{fuel_key}"
        self._attr_icon = _get_fuel_icon(self._fuel_name)
        self._attr_suggested_display_precision = 3

    @property
    def native_value(self) -> StateType:
        """Return the sensor state."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("fuels", {}).get(self._fuel_key, {}).get("price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return fuel-specific state attributes."""
        if not self.coordinator.data:
            return {}

        fuel_info = self.coordinator.data.get("fuels", {}).get(self._fuel_key)
        if not fuel_info:
            return {}

        attributes = {
            ATTR_FUEL_TYPE_NAME: self._fuel_display_name,
            ATTR_IS_SELF: self._is_self_service,
            ATTR_LAST_UPDATE: fuel_info.get("last_update"),
            ATTR_VALIDITY_DATE: fuel_info.get("validity_date"),
            ATTR_STATION_NAME: station_display_name(self.station_info, self._station_id),
            ATTR_STATION_ADDRESS: self.station_info.get("address"),
            ATTR_STATION_BRAND: self.station_info.get("brand"),
            ATTR_PREVIOUS_PRICE: fuel_info.get("previous_price"),
            ATTR_PRICE_CHANGED_AT: fuel_info.get("price_changed_at"),
        }
        attributes.update(
            _price_metadata(
                price=fuel_info.get("price"),
                previous_price=fuel_info.get("previous_price"),
                last_update=fuel_info.get("last_update"),
                stale_hours=int(
                    getattr(self._entry, "options", {}).get(
                        CONF_PRICE_STALE_HOURS,
                        DEFAULT_PRICE_STALE_HOURS,
                    )
                ),
            )
        )
        return attributes


class StationInfoSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a sensor for station information."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        entry: ConfigEntry,
        info_key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize the info sensor."""
        super().__init__(coordinator, entry)
        self._info_key = info_key
        self._attr_translation_key = name
        self._attr_unique_id = f"{self._station_id}_{info_key}"
        self._attr_icon = icon

    @property
    def native_value(self) -> StateType:
        """Return the station info value."""
        return self.station_info.get(self._info_key)


class StationLocationSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a sensor for station location."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize location sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "location"
        self._attr_unique_id = f"{self._station_id}_location"

    @property
    def native_value(self) -> StateType:
        """Return the station address for map cards and diagnostics."""
        return self.station_info.get("address") or station_display_name(
            self.station_info, self._station_id
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes including coordinates for map cards."""
        return {
            ATTR_STATION_NAME: station_display_name(self.station_info, self._station_id),
            ATTR_STATION_ADDRESS: self.station_info.get("address"),
            ATTR_STATION_BRAND: self.station_info.get("brand"),
            ATTR_LATITUDE: self.station_info.get(ATTR_LATITUDE),
            ATTR_LONGITUDE: self.station_info.get(ATTR_LONGITUDE),
            "municipality": self.station_info.get("municipality"),
            "province": self.station_info.get("province"),
            "station_type": self.station_info.get("station_type"),
            "operator": self.station_info.get("operator"),
            "coordinate_source": self.station_info.get("coordinate_source"),
        }

    @property
    def available(self) -> bool:
        """Return True if coordinates are available."""
        return (
            self.station_info.get(ATTR_LATITUDE) is not None
            and self.station_info.get(ATTR_LONGITUDE) is not None
        )


class StationNextChangeSensor(ScheduleAwareEntity, SensorEntity):
    """Sensor indicating when the station will next open or close."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-eight"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the next-change sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "next_change"
        self._attr_unique_id = f"{self._station_id}_next_change"
        self._next_change: tuple[str, datetime | None] = ("no_schedule", None)
        self._next_change_now: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Prime the cached schedule result after Home Assistant attaches the entity."""
        await super().async_added_to_hass()
        self._refresh_next_change()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute the schedule result once for each coordinator update."""
        self._refresh_next_change()
        super()._handle_coordinator_update()

    @callback
    def _handle_time_tick(self, now: datetime) -> None:
        """Recompute the schedule result once for each clock tick."""
        self._refresh_next_change(dt_util.as_local(now))
        super()._handle_time_tick(now)

    def _refresh_next_change(self, now: datetime | None = None) -> None:
        """Cache the next schedule transition used by all entity properties."""
        self._next_change_now = now or dt_util.now()
        self._next_change = self._compute_next_change(self._next_change_now)

    def _compute_next_change(self, now: datetime | None = None) -> tuple[str, datetime | None]:
        """Compute the next opening or closing time and type."""
        opening_hours = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        if not opening_hours:
            return "no_schedule", None

        now = now or dt_util.now()
        intervals: list[tuple[datetime, datetime]] = []
        for day_offset in range(-1, 8):
            local_date = now.date() + timedelta(days=day_offset)
            schedule = _find_schedule_for_day(
                opening_hours,
                local_date.weekday() + 1,
                local_date,
            )
            intervals.extend(
                _schedule_intervals_for_date(schedule, local_date, now.tzinfo)
            )

        merged: list[list[datetime]] = []
        for opens_at, closes_at in sorted(intervals):
            if merged and opens_at <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], closes_at)
            else:
                merged.append([opens_at, closes_at])

        for opens_at, closes_at in merged:
            if opens_at <= now < closes_at:
                if closes_at > now + timedelta(days=7):
                    return "always_open", None
                return "closes_at", closes_at
            if opens_at > now:
                return "opens_at", opens_at
        return "no_opening", None

    @property
    def native_value(self) -> StateType:
        """Return the next change description."""
        change_type, change_time = self._next_change
        if not change_time:
            return change_type

        time_str = change_time.strftime("%H:%M")
        if self._next_change_now is not None and change_time.date() != self._next_change_now.date():
            return f"{time_str} ({change_time.strftime('%d/%m')})"
        return time_str

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        change_type, change_time = self._next_change
        attributes: dict[str, Any] = {
            "change_type": change_type,
            "next_change_time": change_time.isoformat() if change_time else None,
        }
        if change_time:
            now = self._next_change_now or dt_util.now()
            attributes["minutes_until_change"] = int((change_time - now).total_seconds() / 60)
        return attributes

    @property
    def available(self) -> bool:
        """Return True if schedule data is available."""
        return _has_valid_opening_hours(self.coordinator.data)
