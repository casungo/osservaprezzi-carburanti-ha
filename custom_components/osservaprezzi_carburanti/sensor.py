from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator
from .entity import (
    OsservaprezziBaseEntity,
    ScheduleAwareEntity,
    _find_schedule_for_day,
    _has_valid_opening_hours,
    _is_schedule_open,
    _parse_time,
    _schedule_intervals_for_date,
)

INFO_SENSOR_DESCRIPTORS: tuple[tuple[str, str, str], ...] = (
    ("name", "Nome", "mdi:gas-station"),
    ("nomeImpianto", "Nome impianto", "mdi:gas-station"),
    ("id", "ID Osservaprezzi", "mdi:identifier"),
    ("brand", "Marchio", "mdi:tag"),
    ("company", "Società", "mdi:office-building"),
    ("phoneNumber", "Telefono", "mdi:phone"),
    ("email", "Email", "mdi:email"),
    ("website", "Sito web", "mdi:web"),
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
    data = coordinator.data or {}

    entities: list[SensorEntity] = [
        OsservaprezziStationSensor(coordinator, entry, fuel_key)
        for fuel_key in data.get("fuels", {})
    ]

    station_info = data.get("station_info", {})
    for info_key, name, icon in INFO_SENSOR_DESCRIPTORS:
        if station_info.get(info_key):
            entities.append(StationInfoSensor(coordinator, entry, info_key, name, icon))

    entities.append(StationLocationSensor(coordinator, entry))

    if _has_valid_opening_hours(data):
        entities.append(StationNextChangeSensor(coordinator, entry))

    async_add_entities(entities, update_before_add=True)


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

        fuel_name, service_type = fuel_key.rsplit("_", 1)
        self._attr_name = f"{fuel_name.replace('_', ' ').title()} {service_type.title()}"
        self._attr_unique_id = f"{self._station_id}_{fuel_key}"
        self._attr_icon = _get_fuel_icon(fuel_name)

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

        fuel_name, service_type = self._fuel_key.rsplit("_", 1)
        return {
            ATTR_FUEL_TYPE_NAME: fuel_name.replace("_", " ").title(),
            ATTR_IS_SELF: service_type == "self",
            ATTR_LAST_UPDATE: fuel_info.get("last_update"),
            ATTR_VALIDITY_DATE: fuel_info.get("validity_date"),
            ATTR_STATION_NAME: self.station_info.get("nomeImpianto") or self.station_info.get("name"),
            ATTR_STATION_ADDRESS: self.station_info.get("address"),
            ATTR_STATION_BRAND: self.station_info.get("brand"),
            ATTR_PREVIOUS_PRICE: fuel_info.get("previous_price"),
            ATTR_PRICE_CHANGED_AT: fuel_info.get("price_changed_at"),
        }


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
        self._attr_name = name
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
        self._attr_name = "Posizione"
        self._attr_unique_id = f"{self._station_id}_location"

    @property
    def native_value(self) -> StateType:
        """Return the station address for map cards and diagnostics."""
        return self.station_info.get("address") or self.station_info.get("nomeImpianto") or self.station_info.get("name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes including coordinates for map cards."""
        return {
            ATTR_STATION_NAME: self.station_info.get("nomeImpianto") or self.station_info.get("name"),
            ATTR_STATION_ADDRESS: self.station_info.get("address"),
            ATTR_STATION_BRAND: self.station_info.get("brand"),
            ATTR_LATITUDE: self.station_info.get(ATTR_LATITUDE),
            ATTR_LONGITUDE: self.station_info.get(ATTR_LONGITUDE),
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
        self._attr_name = "Prossimo cambio orario"
        self._attr_unique_id = f"{self._station_id}_next_change"

    def _compute_next_change(self) -> tuple[str, datetime | None]:
        """Compute the next opening or closing time and type."""
        opening_hours = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        if not opening_hours:
            return "no_schedule", None

        now = dt_util.now()
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

    def _find_next_closing(
        self,
        schedule: dict[str, Any],
        now: datetime,
    ) -> tuple[str, datetime | None]:
        """Find the next closing time for the current schedule."""
        current_time = now.time()
        if schedule.get("flagOrarioContinuato"):
            close_time = _parse_time(schedule.get("oraChiusuraOrarioContinuato"))
            if close_time and close_time > current_time:
                return "closes_at", now.replace(
                    hour=close_time.hour,
                    minute=close_time.minute,
                    second=0,
                    microsecond=0,
                )
            return self._find_next_opening(self.coordinator.data.get("opening_hours", []), now)

        for key in ("oraChiusuraMattina", "oraChiusuraPomeriggio"):
            close_time = _parse_time(schedule.get(key))
            if close_time and close_time > current_time:
                return "closes_at", now.replace(
                    hour=close_time.hour,
                    minute=close_time.minute,
                    second=0,
                    microsecond=0,
                )

        return self._find_next_opening(self.coordinator.data.get("opening_hours", []), now)

    def _find_next_closing_after_h24(
        self,
        opening_hours: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[str, datetime | None]:
        """Find when a currently H24 schedule next stops being continuously open."""
        current_weekday = now.weekday() + 1
        for day_offset in range(1, 8):
            check_datetime = now + timedelta(days=day_offset)
            check_midnight = check_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
            check_weekday = (current_weekday + day_offset - 1) % 7 + 1
            day = _find_schedule_for_day(opening_hours, check_weekday, check_datetime.date())

            if day is None or day.get("flagChiusura") or day.get("flagNonComunicato"):
                return "closes_at", check_midnight
            if day.get("flagH24"):
                continue
            if _is_schedule_open(day, time(0, 0)):
                return self._find_next_closing(day, check_midnight)
            return "closes_at", check_midnight

        return "always_open", None

    @staticmethod
    def _make_open_datetime(
        open_time: time | None,
        day_offset: int,
        now: datetime,
        check_datetime: datetime,
    ) -> datetime | None:
        """Create a datetime for an opening time, handling same-day vs future-day logic."""
        if open_time is None:
            return None
        if day_offset == 0 and open_time <= now.time():
            return None
        return check_datetime.replace(
            hour=open_time.hour,
            minute=open_time.minute,
            second=0,
            microsecond=0,
        )

    def _find_next_opening(
        self,
        opening_hours: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[str, datetime | None]:
        """Find the next opening time."""
        current_weekday = now.weekday() + 1
        for day_offset in range(7):
            check_datetime = now + timedelta(days=day_offset)
            check_weekday = (current_weekday + day_offset - 1) % 7 + 1
            day = _find_schedule_for_day(opening_hours, check_weekday, check_datetime.date())
            if day is None or day.get("flagChiusura") or day.get("flagNonComunicato"):
                continue

            if day.get("flagOrarioContinuato"):
                open_datetime = self._make_open_datetime(
                    _parse_time(day.get("oraAperturaOrarioContinuato")),
                    day_offset,
                    now,
                    check_datetime,
                )
                if open_datetime:
                    return "opens_at", open_datetime
                continue

            for key in ("oraAperturaMattina", "oraAperturaPomeriggio"):
                open_datetime = self._make_open_datetime(
                    _parse_time(day.get(key)),
                    day_offset,
                    now,
                    check_datetime,
                )
                if open_datetime:
                    return "opens_at", open_datetime

        return "no_opening", None

    @property
    def native_value(self) -> StateType:
        """Return the next change description."""
        change_type, change_time = self._compute_next_change()
        if not change_time:
            return change_type

        time_str = change_time.strftime("%H:%M")
        if change_time.date() != dt_util.now().date():
            return f"{time_str} ({change_time.strftime('%d/%m')})"
        return time_str

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        change_type, change_time = self._compute_next_change()
        attributes: dict[str, Any] = {
            "change_type": change_type,
            "next_change_time": change_time.isoformat() if change_time else None,
        }
        if change_time:
            attributes["minutes_until_change"] = int((change_time - dt_util.now()).total_seconds() / 60)
        return attributes

    @property
    def available(self) -> bool:
        """Return True if schedule data is available."""
        return _has_valid_opening_hours(self.coordinator.data)
