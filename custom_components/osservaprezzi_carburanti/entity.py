from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

HOLIDAY_SCHEDULE_ID = 8
SCHEDULE_REFRESH_INTERVAL = timedelta(minutes=1)


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


def _schedule_intervals_for_date(
    schedule: dict[str, Any] | None,
    local_date: date,
    timezone: tzinfo | None,
) -> list[tuple[datetime, datetime]]:
    """Convert one schedule row into local, date-aware opening intervals."""
    if not schedule or schedule.get("flagChiusura") or schedule.get("flagNonComunicato"):
        return []

    day_start = datetime.combine(local_date, time.min, timezone)
    if schedule.get("flagH24"):
        return [(day_start, datetime.combine(local_date + timedelta(days=1), time.min, timezone))]

    pairs = (
        (("oraAperturaOrarioContinuato", "oraChiusuraOrarioContinuato"),)
        if schedule.get("flagOrarioContinuato")
        else (
            ("oraAperturaMattina", "oraChiusuraMattina"),
            ("oraAperturaPomeriggio", "oraChiusuraPomeriggio"),
        )
    )
    intervals: list[tuple[datetime, datetime]] = []
    for open_key, close_key in pairs:
        open_time = _parse_time(schedule.get(open_key))
        close_time = _parse_time(schedule.get(close_key))
        if open_time is None or close_time is None:
            continue
        opens_at = datetime.combine(local_date, open_time, timezone)
        close_date = local_date + timedelta(days=close_time <= open_time)
        closes_at = datetime.combine(close_date, close_time, timezone)
        intervals.append((opens_at, closes_at))
    return intervals


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

    def _handle_time_tick(self, _: datetime) -> None:
        """Refresh state as time passes even when prices do not change."""
        self.schedule_update_ha_state()
