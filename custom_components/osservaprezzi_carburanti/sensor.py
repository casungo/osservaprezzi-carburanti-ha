from __future__ import annotations
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util
from .const import (
    DOMAIN,
    CONF_STATION_ID,
    ATTR_STATION_NAME,
    ATTR_STATION_ADDRESS,
    ATTR_STATION_BRAND,
    ATTR_FUEL_TYPE_NAME,
    ATTR_IS_SELF,
    ATTR_LAST_UPDATE,
    ATTR_VALIDITY_DATE,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_PREVIOUS_PRICE,
    ATTR_PRICE_CHANGED_AT,
    ADDITIONAL_SERVICES,
    SERVICE_ID_TO_TRANSLATION_KEY,
)
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

HOLIDAY_SCHEDULE_ID = 8


def _is_italian_holiday(check_date: date) -> bool:
    """Check if a date is an Italian national public holiday."""
    fixed_holidays = {
        (1, 1), (1, 6), (4, 25), (5, 1), (6, 2),
        (8, 15), (11, 1), (12, 8), (12, 25), (12, 26),
    }
    if (check_date.month, check_date.day) in fixed_holidays:
        return True
    easter = _compute_easter(check_date.year)
    easter_monday = easter + timedelta(days=1)
    return check_date in (easter, easter_monday)


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
    """Parse time string to time object.

    Supports formats:
    - HH:MM (e.g., "07:00")
    - HH.MM (e.g., "07.00")
    - HH (e.g., "7")

    Note: Handles "24.00" as midnight (00:00) for legacy system compatibility.
    """
    if not time_str:
        return None

    try:
        time_str_clean = str(time_str).strip()

        # Handle HH:MM format using fromisoformat (Python 3.7+)
        if ":" in time_str_clean:
            return time.fromisoformat(time_str_clean)

        # Handle HH.MM format
        elif "." in time_str_clean:
            hour_str, minute_str = time_str_clean.split(".")
            hour = int(hour_str)

            # Handle "24.00" as midnight for legacy systems
            if hour == 24:
                hour = 0

            minute = int(minute_str) if minute_str else 0
            return time(hour, minute)

        # Handle HH format
        else:
            hour = int(time_str_clean)

            # Handle "24" as midnight for legacy systems
            if hour == 24:
                hour = 0

            return time(hour, 0)

    except (ValueError, TypeError) as err:
        _LOGGER.warning("Failed to parse time string '%s': %s", time_str, err)
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform based on config type."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    
    sensors: list[SensorEntity | BinarySensorEntity] = []
    if coordinator.data:
        # Station-based sensors
        for fuel_key in coordinator.data.get("fuels", {}):
            sensors.append(OsservaprezziStationSensor(coordinator, entry, fuel_key))
        
        # Diagnostic sensors for the station (created only once)
        station_info = coordinator.data.get("station_info", {})
        
        # Always add basic info sensors
        sensors.extend([
            StationInfoSensor(coordinator, entry, "name", "Nome", "mdi:gas-station"),
            StationInfoSensor(coordinator, entry, "nomeImpianto", "Nome Impianto", "mdi:gas-station"),
            StationInfoSensor(coordinator, entry, "id", "ID Osservaprezzi", "mdi:identifier"),
            StationInfoSensor(coordinator, entry, "address", "Indirizzo", "mdi:map-marker"),
            StationInfoSensor(coordinator, entry, "brand", "Marchio", "mdi:tag"),
            StationInfoSensor(coordinator, entry, "company", "Società", "mdi:office-building"),
        ])
        
        # Only add contact sensors if data is available
        if station_info.get("phoneNumber") and station_info.get("phoneNumber").strip():
            sensors.append(StationInfoSensor(coordinator, entry, "phoneNumber", "Telefono", "mdi:phone"))
        
        if station_info.get("email") and station_info.get("email").strip():
            sensors.append(StationInfoSensor(coordinator, entry, "email", "Email", "mdi:email"))
        
        if station_info.get("website") and station_info.get("website").strip():
            sensors.append(StationInfoSensor(coordinator, entry, "website", "Sito Web", "mdi:web"))
        
        # Add a single location sensor for the map marker
        sensors.append(StationLocationSensor(coordinator, entry))
        
        # Only add opening hours related sensors if opening hours data is available and valid
        if _has_valid_opening_hours(coordinator.data):
            # Add open/closed status binary sensor
            sensors.append(StationOpenClosedBinarySensor(coordinator, entry))
            # Add next opening/closing time sensor
            sensors.append(StationNextChangeSensor(coordinator, entry))
        
        # Add binary sensors for available services
        if coordinator.data and "services" in coordinator.data:
            # Get the list of available services at this station
            available_services = coordinator.data.get("services", [])
            
            # Create a set of available service IDs for quick lookup
            available_service_ids = set()
            for service in available_services:
                if isinstance(service, dict) and "id" in service:
                    available_service_ids.add(str(service["id"]))
            
            # Only create binary sensors for services that are available at this station
            for service_id, service_info in ADDITIONAL_SERVICES.items():
                if service_id in available_service_ids:
                    sensors.append(
                        StationServiceBinarySensor(coordinator, entry, service_id, service_info)
                    )
    
        async_add_entities(sensors, update_before_add=True)


def _has_valid_opening_hours(data: dict) -> bool:
    """Check if opening hours data contains valid schedule information."""
    if not data or "opening_hours" not in data:
        return False
    
    opening_hours = data.get("opening_hours", [])
    if not opening_hours:
        return False
    
    # Check if at least one day has valid opening hours
    for day in opening_hours:
        # Skip if the day is marked as closed
        if day.get("flagChiusura"):
            continue
        
        # Check if it's 24/7
        if day.get("flagH24"):
            return True
        
        # Check for continuous hours
        if day.get("flagOrarioContinuato"):
            open_time = day.get("oraAperturaOrarioContinuato")
            close_time = day.get("oraChiusuraOrarioContinuato")
            if open_time and close_time:
                return True
        
        # Check for split hours
        else:
            morning_open = day.get("oraAperturaMattina")
            morning_close = day.get("oraChiusuraMattina")
            afternoon_open = day.get("oraAperturaPomeriggio")
            afternoon_close = day.get("oraChiusuraPomeriggio")
            
            # Check if we have valid morning or afternoon hours
            if (morning_open and morning_close) or (afternoon_open and afternoon_close):
                return True
    
    # If we get here, no valid opening hours were found
    return False


def _get_fuel_icon(fuel_name: str) -> str:
    """Return the icon for the fuel type."""
    fuel_name_lower = fuel_name.lower()
    if "benzina" in fuel_name_lower:
        return "mdi:gas-station"
    elif "gasolio" in fuel_name_lower or "diesel" in fuel_name_lower:
        return "mdi:fuel"
    elif "gpl" in fuel_name_lower:
        return "mdi:gas-cylinder"
    elif "metano" in fuel_name_lower:
        return "mdi:molecule-co2"
    else:
        return "mdi:currency-eur"


def _find_schedule_for_day(
    opening_hours: list[dict], weekday: int, check_date: date
) -> dict | None:
    """Find the schedule entry for a given weekday, considering holidays."""
    if _is_italian_holiday(check_date):
        for day in opening_hours:
            if day.get("giornoSettimanaId") == HOLIDAY_SCHEDULE_ID:
                return day
    for day in opening_hours:
        if day.get("giornoSettimanaId") == weekday:
            return day
    return None


def _is_schedule_open(schedule: dict, current_time: time) -> bool:
    """Check if a station is open based on a schedule entry and current time."""
    if schedule.get("flagOrarioContinuato"):
        open_time = _parse_time(schedule.get("oraAperturaOrarioContinuato"))
        close_time = _parse_time(schedule.get("oraChiusuraOrarioContinuato"))
        if open_time and close_time:
            if open_time <= close_time:
                return open_time <= current_time <= close_time
            else:
                return current_time >= open_time or current_time <= close_time
    else:
        morning_open = _parse_time(schedule.get("oraAperturaMattina"))
        morning_close = _parse_time(schedule.get("oraChiusuraMattina"))
        afternoon_open = _parse_time(schedule.get("oraAperturaPomeriggio"))
        afternoon_close = _parse_time(schedule.get("oraChiusuraPomeriggio"))
        if morning_open and morning_close:
            if morning_open <= current_time <= morning_close:
                return True
        if afternoon_open and afternoon_close:
            if afternoon_open <= current_time <= afternoon_close:
                return True
    return False


class OsservaprezziStationSensor(CoordinatorEntity, SensorEntity):
    """Representation of a single fuel price for a specific station."""
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "€/L"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry, fuel_key: str) -> None:
        """Initialize the station fuel sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._fuel_key = fuel_key
        
        fuel_name, service_type = fuel_key.rsplit('_', 1)
        self._attr_name = f"{fuel_name.replace('_', ' ').title()} {service_type.title()}"
        self._attr_unique_id = f"{self._station_id}_{fuel_key}"
        self._attr_icon = _get_fuel_icon(fuel_name)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if not self.coordinator.data or "fuels" not in self.coordinator.data:
            return None
        return self.coordinator.data["fuels"][self._fuel_key].get("price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "fuels" not in self.coordinator.data:
            return {}
        
        fuel_info = self.coordinator.data["fuels"][self._fuel_key]
        station_info = self.coordinator.data.get("station_info", {})
        fuel_name, service_type = self._fuel_key.rsplit('_', 1)

        return {
            ATTR_FUEL_TYPE_NAME: fuel_name.replace('_', ' ').title(),
            ATTR_IS_SELF: service_type == "self",
            ATTR_LAST_UPDATE: fuel_info.get("last_update"),
            ATTR_VALIDITY_DATE: fuel_info.get("validity_date"),
            ATTR_STATION_NAME: station_info.get("name"),
            ATTR_STATION_ADDRESS: station_info.get("address"),
            ATTR_STATION_BRAND: station_info.get("brand"),
            ATTR_PREVIOUS_PRICE: fuel_info.get("previous_price"),
            ATTR_PRICE_CHANGED_AT: fuel_info.get("price_changed_at"),
        }

class StationInfoSensor(CoordinatorEntity, SensorEntity):
    """Representation of a sensor for station information."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry, info_key: str, name: str, icon: str) -> None:
        """Initialize the info sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._info_key = info_key
        self._attr_name = name
        self._attr_unique_id = f"{self._station_id}_{info_key}"
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return None
        return self.coordinator.data["station_info"].get(self._info_key)



class StationLocationSensor(CoordinatorEntity, SensorEntity):
    """Representation of a sensor for station location (single map marker)."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:map-marker"
    _attr_translation_key = "location"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize location sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_unique_id = f"{self._station_id}_location"

    @property
    def name(self) -> str:
        """Return the name of the sensor (use station name for map marker)."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return "Posizione Stazione"
        station_info = self.coordinator.data.get("station_info", {})
        # Prefer nomeImpianto (station name), fallback to name, then default
        station_name = station_info.get("nomeImpianto") or station_info.get("name") or "Posizione Stazione"
        return station_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor (station name)."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return None
        station_info = self.coordinator.data.get("station_info", {})
        # Prefer nomeImpianto (station name), fallback to name, then default
        return station_info.get("nomeImpianto") or station_info.get("name") or "Stazione Servizio"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes including coordinates for map."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return {}
        
        station_info = self.coordinator.data.get("station_info", {})
        
        return {
            ATTR_STATION_NAME: station_info.get("name"),
            ATTR_STATION_ADDRESS: station_info.get("address"),
            ATTR_STATION_BRAND: station_info.get("brand"),
            ATTR_LATITUDE: station_info.get(ATTR_LATITUDE),
            ATTR_LONGITUDE: station_info.get(ATTR_LONGITUDE),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return False
        station_info = self.coordinator.data.get("station_info", {})
        # Only show as available if we have coordinates
        return (station_info.get(ATTR_LATITUDE) is not None and
                station_info.get(ATTR_LONGITUDE) is not None)


class StationOpenClosedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating if the station is currently open."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:storefront"
    _attr_translation_key = "station_open_closed"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the open/closed binary sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_unique_id = f"{self._station_id}_open_closed"
        self._cached_is_open: bool | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    def _is_currently_open(self) -> bool:
        """Check if the station is currently open."""
        if not self.coordinator.data or "opening_hours" not in self.coordinator.data:
            return False

        opening_hours = self.coordinator.data.get("opening_hours", [])
        if not opening_hours:
            return False

        now = dt_util.now()
        current_weekday = now.weekday() + 1
        current_time = now.time()

        today_schedule = _find_schedule_for_day(
            opening_hours, current_weekday, now.date()
        )

        if not today_schedule:
            return False

        if today_schedule.get("flagChiusura"):
            return False

        if today_schedule.get("flagH24"):
            return True

        return _is_schedule_open(today_schedule, current_time)

    @property
    def is_on(self) -> bool:
        """Return True if the station is currently open."""
        if self._cached_is_open is None:
            self._cached_is_open = self._is_currently_open()
        return self._cached_is_open

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "current_status": "on" if self.is_on else "off",
            "last_updated": dt_util.now().isoformat(),
        }

    def _handle_coordinator_update(self) -> None:
        """Clear cached state after coordinator updates."""
        self._cached_is_open = None
        super()._handle_coordinator_update()


class StationNextChangeSensor(CoordinatorEntity, SensorEntity):
    """Sensor indicating when the station will next open or close."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-eight"
    _attr_translation_key = "next_change"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the next change sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_unique_id = f"{self._station_id}_next_change"
        self._cached_next_change: tuple[str, datetime | None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    def _get_next_change(self) -> tuple[str, datetime | None]:
        """Get the next opening or closing time and type (cached per update)."""
        if self._cached_next_change is None:
            self._cached_next_change = self._compute_next_change()
        return self._cached_next_change

    def _compute_next_change(self) -> tuple[str, datetime | None]:
        """Compute the next opening or closing time and type."""
        if not self.coordinator.data or "opening_hours" not in self.coordinator.data:
            return "no_schedule", None

        opening_hours = self.coordinator.data.get("opening_hours", [])
        if not opening_hours:
            return "no_schedule", None

        for day in opening_hours:
            if day.get("flagH24"):
                return "always_open", None

        now = dt_util.now()
        current_weekday = now.weekday() + 1
        current_time = now.time()

        today_schedule = _find_schedule_for_day(
            opening_hours, current_weekday, now.date()
        )

        if not today_schedule or today_schedule.get("flagChiusura"):
            return self._find_next_opening(opening_hours, now)

        if _is_schedule_open(today_schedule, current_time):
            return self._find_next_closing(today_schedule, now)
        else:
            return self._find_next_opening(opening_hours, now)

    def _find_next_closing(self, schedule: dict, now: datetime) -> tuple[str, datetime | None]:
        """Find the next closing time for today."""
        current_time = now.time()

        # Check continuous hours
        if schedule.get("flagOrarioContinuato"):
            close_time = _parse_time(schedule.get("oraChiusuraOrarioContinuato"))
            if close_time and close_time > current_time:
                close_datetime = now.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
                return "closes_at", close_datetime

        # Check split hours
        else:
            morning_close = _parse_time(schedule.get("oraChiusuraMattina"))
            afternoon_close = _parse_time(schedule.get("oraChiusuraPomeriggio"))

            # Check morning closing
            if morning_close and morning_close > current_time:
                close_datetime = now.replace(hour=morning_close.hour, minute=morning_close.minute, second=0, microsecond=0)
                return "closes_at", close_datetime

            # Check afternoon closing
            if afternoon_close and afternoon_close > current_time:
                close_datetime = now.replace(hour=afternoon_close.hour, minute=afternoon_close.minute, second=0, microsecond=0)
                return "closes_at", close_datetime

        # If no closing time found today, find next opening
        opening_hours = self.coordinator.data.get("opening_hours", [])
        return self._find_next_opening(opening_hours, now)

    @staticmethod
    def _make_open_datetime(
        open_time: time | None,
        day_offset: int,
        now: datetime,
        check_date: datetime | None = None
    ) -> datetime | None:
        """Create a datetime for an opening time, handling same-day vs future-day logic."""
        if open_time is None:
            return None

        if day_offset == 0:
            # Same day: only return if time is in the future
            if open_time > now.time():
                return now.replace(hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0)
            return None
        else:
            # Future day: use provided date or calculate it
            if check_date is None:
                check_date = now + timedelta(days=day_offset)
            return datetime.combine(check_date.date(), open_time)

    def _find_next_opening(self, opening_hours: list, now: datetime) -> tuple[str, datetime | None]:
        """Find the next opening time."""
        current_weekday = now.weekday() + 1

        for day_offset in range(7):
            check_weekday = (current_weekday + day_offset - 1) % 7 + 1
            check_date = now + timedelta(days=day_offset)

            day = _find_schedule_for_day(opening_hours, check_weekday, check_date.date())
            if day is None:
                continue
            if day.get("flagChiusura") or day.get("flagNonComunicato"):
                continue

            if day.get("flagOrarioContinuato"):
                open_time = _parse_time(day.get("oraAperturaOrarioContinuato"))
                open_datetime = StationNextChangeSensor._make_open_datetime(open_time, day_offset, now, check_date)
                if open_datetime:
                    return "opens_at", open_datetime
            else:
                morning_open = _parse_time(day.get("oraAperturaMattina"))
                open_datetime = StationNextChangeSensor._make_open_datetime(morning_open, day_offset, now, check_date)
                if open_datetime:
                    return "opens_at", open_datetime

                afternoon_open = _parse_time(day.get("oraAperturaPomeriggio"))
                open_datetime = StationNextChangeSensor._make_open_datetime(afternoon_open, day_offset, now, check_date)
                if open_datetime:
                    return "opens_at", open_datetime

        return "no_opening", None

    @property
    def native_value(self) -> StateType:
        """Return the next change description."""
        change_type, change_time = self._get_next_change()
        if change_time:
            time_str = change_time.strftime("%H:%M")
            if change_time.date() != dt_util.now().date():
                date_str = change_time.strftime("%d/%m")
                return f"{time_str} ({date_str})"
            else:
                return time_str
        return change_type

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        change_type, change_time = self._get_next_change()
        attributes: dict[str, Any] = {
            "change_type": change_type,
            "next_change_time": change_time.isoformat() if change_time else None,
            "last_updated": dt_util.now().isoformat(),
        }

        if change_time:
            # Calculate minutes until next change
            now = dt_util.now()
            minutes_until = int((change_time - now).total_seconds() / 60)
            attributes["minutes_until_change"] = minutes_until

        return attributes

    def _handle_coordinator_update(self) -> None:
        """Clear cached state after coordinator updates."""
        self._cached_next_change = None
        super()._handle_coordinator_update()


class StationServiceBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a binary sensor for a specific station service."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry, service_id: str, service_info: dict) -> None:
        """Initialize the service binary sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._service_id = service_id
        self._service_info = service_info

        # Set unique_id, icon, and translation key from service info
        self._attr_unique_id = f"{self._station_id}_service_{service_id}"
        self._attr_icon = service_info["icon"]
        self._attr_name = service_info["name"]
        self._attr_translation_key = SERVICE_ID_TO_TRANSLATION_KEY.get(service_id, service_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Area di Servizio",
        )

    @property
    def is_on(self) -> bool:
        """Return True if the service is available at the station."""
        if not self.coordinator.data or "services" not in self.coordinator.data:
            return False
        
        services = self.coordinator.data.get("services", [])
        if not services:
            return False
        
        # Check if this service is available
        for service in services:
            # Handle list of dictionaries (detailed service info)
            if isinstance(service, dict) and str(service.get("id")) == self._service_id:
                return True
            # Handle list of strings (raw service IDs)
            elif isinstance(service, str) and service == self._service_id:
                return True
            # Handle list of integers
            elif isinstance(service, int) and str(service) == self._service_id:
                return True
        
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes with service information."""
        return {
            "service_id": self._service_id,
            "service_name": self._service_info.get("name"),
            "service_description": self._service_info.get("description"),
            "service_icon": self._service_info.get("icon"),
            "service_image_url": self._service_info.get("image_url"),
        }
