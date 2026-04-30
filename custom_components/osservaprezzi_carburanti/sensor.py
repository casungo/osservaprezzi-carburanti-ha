from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ADDITIONAL_SERVICES,
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
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

HOLIDAY_SCHEDULE_ID = 8
SCHEDULE_REFRESH_INTERVAL = timedelta(minutes=1)

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


def _is_italian_holiday(check_date: date) -> bool:
    """Check if a date is an Italian national public holiday."""
    fixed_holidays = {
        (1, 1),
        (1, 6),
        (4, 25),
        (5, 1),
        (6, 2),
        (8, 15),
        (11, 1),
        (12, 8),
        (12, 25),
        (12, 26),
    }
    if (check_date.month, check_date.day) in fixed_holidays:
        return True

    easter = _compute_easter(check_date.year)
    return check_date in (easter, easter + timedelta(days=1))


def _compute_easter(year: int) -> date:
    """Compute Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _parse_time(time_str: str | None) -> time | None:
    """Parse time strings used by the opening-hours API payload."""
    if not time_str:
        return None

    try:
        time_str_clean = str(time_str).strip()
        if ":" in time_str_clean:
            return time.fromisoformat(time_str_clean)
        if "." in time_str_clean:
            hour_str, minute_str = time_str_clean.split(".")
            hour = int(hour_str)
            minute = int(minute_str) if minute_str else 0
            return time(0 if hour == 24 else hour, minute)

        hour = int(time_str_clean)
        return time(0 if hour == 24 else hour, 0)
    except (TypeError, ValueError) as err:
        _LOGGER.warning("Failed to parse time string '%s': %s", time_str, err)
        return None


def _has_valid_opening_hours(data: dict[str, Any] | None) -> bool:
    """Check if opening hours data contains valid schedule information."""
    if not data:
        return False

    for day in data.get("opening_hours", []):
        if day.get("flagChiusura") or day.get("flagNonComunicato"):
            continue
        if day.get("flagH24"):
            return True
        if day.get("flagOrarioContinuato"):
            if day.get("oraAperturaOrarioContinuato") and day.get("oraChiusuraOrarioContinuato"):
                return True
            continue
        if (
            day.get("oraAperturaMattina") and day.get("oraChiusuraMattina")
        ) or (
            day.get("oraAperturaPomeriggio") and day.get("oraChiusuraPomeriggio")
        ):
            return True
    return False


def _get_available_service_ids(services: list[Any]) -> set[str]:
    """Normalize available service ids from the API payload."""
    available_ids: set[str] = set()
    for service in services:
        if isinstance(service, dict) and service.get("id") is not None:
            available_ids.add(str(service["id"]))
        elif isinstance(service, (int, str)):
            available_ids.add(str(service))
    return available_ids


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


def _find_schedule_for_day(
    opening_hours: list[dict[str, Any]],
    weekday: int,
    check_date: date,
) -> dict[str, Any] | None:
    """Find the schedule entry for a given weekday, considering holidays."""
    if _is_italian_holiday(check_date):
        for day in opening_hours:
            if day.get("giornoSettimanaId") == HOLIDAY_SCHEDULE_ID:
                return day

    for day in opening_hours:
        if day.get("giornoSettimanaId") == weekday:
            return day
    return None


def _is_schedule_open(schedule: dict[str, Any], current_time: time) -> bool:
    """Check if a station is open based on a schedule entry and current time."""
    if schedule.get("flagOrarioContinuato"):
        open_time = _parse_time(schedule.get("oraAperturaOrarioContinuato"))
        close_time = _parse_time(schedule.get("oraChiusuraOrarioContinuato"))
        if open_time and close_time:
            if open_time <= close_time:
                return open_time <= current_time <= close_time
            return current_time >= open_time or current_time <= close_time
        return False

    morning_open = _parse_time(schedule.get("oraAperturaMattina"))
    morning_close = _parse_time(schedule.get("oraChiusuraMattina"))
    afternoon_open = _parse_time(schedule.get("oraAperturaPomeriggio"))
    afternoon_close = _parse_time(schedule.get("oraChiusuraPomeriggio"))
    if morning_open and morning_close and morning_open <= current_time <= morning_close:
        return True
    if afternoon_open and afternoon_close and afternoon_open <= current_time <= afternoon_close:
        return True
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor and binary_sensor entities for a station."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    data = coordinator.data or {}

    entities: list[SensorEntity | BinarySensorEntity] = [
        OsservaprezziStationSensor(coordinator, entry, fuel_key)
        for fuel_key in data.get("fuels", {})
    ]

    station_info = data.get("station_info", {})
    for info_key, translation_key, icon in INFO_SENSOR_DESCRIPTORS:
        if station_info.get(info_key):
            entities.append(StationInfoSensor(coordinator, entry, info_key, translation_key, icon))

    entities.append(StationLocationSensor(coordinator, entry))

    if _has_valid_opening_hours(data):
        entities.append(StationOpenClosedBinarySensor(coordinator, entry))
        entities.append(StationNextChangeSensor(coordinator, entry))

    available_service_ids = _get_available_service_ids(data.get("services", []))
    for service_id, service_info in ADDITIONAL_SERVICES.items():
        if service_id in available_service_ids:
            entities.append(StationServiceBinarySensor(coordinator, entry, service_id, service_info))

    async_add_entities(entities, update_before_add=True)


class OsservaprezziBaseEntity(CoordinatorEntity):
    """Shared entity helpers for this integration."""

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the shared entity base."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]

    @property
    def station_info(self) -> dict[str, Any]:
        """Return cached station info."""
        return self.coordinator.data.get("station_info", {}) if self.coordinator.data else {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info shared by all station entities."""
        station_info = self.station_info
        station_name = station_info.get("nomeImpianto") or station_info.get("name") or self._station_id
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_name,
            manufacturer=station_info.get("brand"),
            model=station_info.get("station_type") or "Fuel Station",
        )


class ScheduleAwareEntity(OsservaprezziBaseEntity):
    """Entity mixin for time-sensitive opening-hours sensors."""

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the schedule-aware entity."""
        super().__init__(coordinator, entry)
        self._time_listener: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Start a lightweight timer so schedule entities stay fresh."""
        await super().async_added_to_hass()
        self._time_listener = async_track_time_interval(
            self.hass,
            self._handle_time_tick,
            SCHEDULE_REFRESH_INTERVAL,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the periodic timer."""
        if self._time_listener:
            self._time_listener()
            self._time_listener = None
        await super().async_will_remove_from_hass()

    @staticmethod
    def _handle_time_tick(_: datetime) -> None:
        """Placeholder overridden by subclasses."""


class OsservaprezziStationSensor(OsservaprezziBaseEntity, SensorEntity):
    """Representation of a single fuel price for a specific station."""

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
        translation_key: str,
        icon: str,
    ) -> None:
        """Initialize the info sensor."""
        super().__init__(coordinator, entry)
        self._info_key = info_key
        self._attr_translation_key = translation_key
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
    _attr_translation_key = "location"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize location sensor."""
        super().__init__(coordinator, entry)
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


class StationOpenClosedBinarySensor(ScheduleAwareEntity, BinarySensorEntity):
    """Binary sensor indicating if the station is currently open."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:storefront"
    _attr_translation_key = "station_open_closed"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the open/closed binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._station_id}_open_closed"

    def _is_currently_open(self) -> bool:
        """Check if the station is currently open."""
        opening_hours = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        if not opening_hours:
            return False

        now = dt_util.now()
        today_schedule = _find_schedule_for_day(opening_hours, now.weekday() + 1, now.date())
        if not today_schedule or today_schedule.get("flagChiusura"):
            return False
        if today_schedule.get("flagH24"):
            return True
        return _is_schedule_open(today_schedule, now.time())

    @property
    def is_on(self) -> bool:
        """Return True if the station is currently open."""
        return self._is_currently_open()

    @property
    def available(self) -> bool:
        """Return True if schedule data is available."""
        return _has_valid_opening_hours(self.coordinator.data)

    def _handle_time_tick(self, _: datetime) -> None:
        """Refresh state as time passes even when prices do not change."""
        self.async_write_ha_state()


class StationNextChangeSensor(ScheduleAwareEntity, SensorEntity):
    """Sensor indicating when the station will next open or close."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-eight"
    _attr_translation_key = "next_change"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the next-change sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._station_id}_next_change"

    def _compute_next_change(self) -> tuple[str, datetime | None]:
        """Compute the next opening or closing time and type."""
        opening_hours = self.coordinator.data.get("opening_hours", []) if self.coordinator.data else []
        if not opening_hours:
            return "no_schedule", None

        now = dt_util.now()
        today_schedule = _find_schedule_for_day(opening_hours, now.weekday() + 1, now.date())
        if not today_schedule or today_schedule.get("flagChiusura"):
            return self._find_next_opening(opening_hours, now)
        if today_schedule.get("flagH24"):
            return self._find_next_closing_after_h24(opening_hours, now)
        if _is_schedule_open(today_schedule, now.time()):
            return self._find_next_closing(today_schedule, now)
        return self._find_next_opening(opening_hours, now)

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

    def _handle_time_tick(self, _: datetime) -> None:
        """Refresh state as time passes even when prices do not change."""
        self.async_write_ha_state()


class StationServiceBinarySensor(OsservaprezziBaseEntity, BinarySensorEntity):
    """Representation of a binary sensor for a specific station service."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = False

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
