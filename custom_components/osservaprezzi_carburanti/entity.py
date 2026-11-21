from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_STATION_ID, DOMAIN
from .coordinator import CarburantiDataUpdateCoordinator
from .data_helpers import station_display_name

_LOGGER = logging.getLogger(__name__)

HOLIDAY_SCHEDULE_ID = 8
FIXED_HOLIDAYS = frozenset({(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)})


def _is_italian_holiday(check_date: date) -> bool:
    """Check if a date is an Italian national public holiday."""
    if (check_date.month, check_date.day) in FIXED_HOLIDAYS:
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
            parts = time_str_clean.split(":")
            if len(parts) not in (2, 3):
                raise ValueError(f"Invalid time format: {time_str_clean}")
            hour = int(parts[0])
            minute = int(parts[1])
            second = int(parts[2]) if len(parts) == 3 else 0
            if hour == 24 and minute == 0 and second == 0:
                return time(0, 0)
            return time(hour, minute, second)
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


def _has_valid_opening_hours(data: Mapping[str, Any] | None) -> bool:
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
        self._entry = entry
        self._station_id = entry.data[CONF_STATION_ID]

    @property
    def station_info(self) -> dict[str, Any]:
        """Return cached station info."""
        return self.coordinator.data.get("station_info", {}) if self.coordinator.data else {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info shared by all station entities."""
        station_info = self.station_info
        station_name = station_display_name(station_info, self._station_id)
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_name,
            manufacturer=station_info.get("brand"),
            model=station_info.get("station_type") or "Fuel Station",
            configuration_url=(
                "https://carburanti.mise.gov.it/ospzSearch/dettaglio/"
                f"{self._station_id}"
            ),
        )


class ScheduleAwareEntity(OsservaprezziBaseEntity):
    """Entity mixin for time-sensitive opening-hours sensors."""

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the schedule-aware entity."""
        super().__init__(coordinator, entry)
        self._time_listener: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Schedule the next opening-hours transition."""
        await super().async_added_to_hass()
        self._schedule_transition()

    @callback
    def _next_transition_time(self, now: datetime | None = None) -> datetime | None:
        """Return the next transition datetime used to arm the one-shot timer."""
        _, change_time = self._compute_next_change(now)
        return change_time

    @callback
    def _schedule_transition(self, now: datetime | None = None) -> None:
        """Replace the timer with the next transition, if one exists."""
        if self._time_listener:
            self._time_listener()
            self._time_listener = None
        change_time = self._next_transition_time(now)
        if change_time is not None:
            self._time_listener = async_track_point_in_utc_time(
                self.hass, self._handle_time_tick, dt_util.as_utc(change_time)
            )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the transition timer."""
        if self._time_listener:
            self._time_listener()
            self._time_listener = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reschedule when opening hours change."""
        self._schedule_transition()
        super()._handle_coordinator_update()

    @callback
    def _handle_time_tick(self, now: datetime) -> None:
        """Update the state and schedule the following transition."""
        self._time_listener = None
        self._schedule_transition(dt_util.as_local(now))
        self.async_write_ha_state()

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

        horizon_end = datetime.combine(
            now.date() + timedelta(days=8),
            time.min,
            now.tzinfo,
        )
        for opens_at, closes_at in merged:
            if opens_at <= now < closes_at:
                if closes_at >= horizon_end:
                    return "always_open", None
                return "closes_at", closes_at
            if opens_at > now:
                return "opens_at", opens_at
        return "no_opening", None

