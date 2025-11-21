from __future__ import annotations
import logging
from datetime import datetime, time, timedelta
from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.typing import StateType
from .const import (
    DOMAIN,
    CONF_CONFIG_TYPE,
    CONF_TYPE_STATION,
    CONF_TYPE_ZONE,
    CONF_STATION_ID,
    ATTR_STATION_NAME,
    ATTR_STATION_ADDRESS,
    ATTR_STATION_BRAND,
    ATTR_FUEL_TYPE_NAME,
    ATTR_IS_SELF,
    ATTR_LAST_UPDATE,
    ATTR_VALIDITY_DATE,
    ATTR_DISTANCE,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ADDITIONAL_SERVICES,
)
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform based on config type."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    
    config_type = entry.data.get(CONF_CONFIG_TYPE, CONF_TYPE_STATION)

    sensors = []
    if coordinator.data:
        if config_type == CONF_TYPE_ZONE:
            sensors.append(OsservaprezziZoneSensor(coordinator, entry))
        else:
            # Station-based sensors
            for fuel_key in coordinator.data.get("fuels", {}):
                sensors.append(OsservaprezziStationSensor(coordinator, entry, fuel_key))
            
            # Diagnostic sensors for the station
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

class OsservaprezziZoneSensor(CoordinatorEntity, SensorEntity):
    """Representation of the cheapest fuel price in a zone."""
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "€/L"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the zone sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = entry.title
        self._attr_unique_id = entry.unique_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info for the zone."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id)},
            name=self._entry.title,
            manufacturer="Osservaprezzi Carburanti",
            model="Ricerca in Zona",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor (the cheapest price)."""
        if not self.coordinator.data or "fuels" not in self.coordinator.data:
            return None
        # In zone mode, there's only one fuel in the coordinator data
        fuel_key = next(iter(self.coordinator.data["fuels"]))
        return self.coordinator.data["fuels"][fuel_key].get("price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return {}
        
        station_info = self.coordinator.data["station_info"]
        fuel_key = next(iter(self.coordinator.data["fuels"]))
        fuel_info = self.coordinator.data["fuels"][fuel_key]
        
        fuel_name, service_type = fuel_key.rsplit('_', 1)

        return {
            ATTR_STATION_NAME: station_info.get("name"),
            ATTR_STATION_ADDRESS: station_info.get("address"),
            ATTR_STATION_BRAND: station_info.get("brand"),
            ATTR_DISTANCE: station_info.get(ATTR_DISTANCE),
            ATTR_FUEL_TYPE_NAME: fuel_name.replace('_', ' ').title(),
            ATTR_IS_SELF: service_type == "self",
            ATTR_LAST_UPDATE: fuel_info.get("last_update"),
            ATTR_VALIDITY_DATE: fuel_info.get("validity_date"),
            ATTR_LATITUDE: station_info.get(ATTR_LATITUDE),
            ATTR_LONGITUDE: station_info.get(ATTR_LONGITUDE),
        }
    
    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        if self.coordinator.data and "fuels" in self.coordinator.data:
            fuel_key = next(iter(self.coordinator.data["fuels"]))
            fuel_name, _ = fuel_key.rsplit('_', 1)
            return _get_fuel_icon(fuel_name)
        return "mdi:currency-eur"

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
            # Remove lat/lon to prevent multiple map markers
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

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize location sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        # Set a default name, will be updated with actual station name in _attr_name property
        self._attr_name = "Posizione Stazione"
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
    _attr_icon = "mdi:storefront"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the open/closed binary sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_name = "Stazione Aperta"
        self._attr_unique_id = f"{self._station_id}_open_closed"

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

    def _parse_time(self, time_str: str | None) -> time | None:
        """Parse time string to time object."""
        if not time_str:
            return None
        
        try:
            # Handle HH:MM format
            if ":" in str(time_str):
                hour, minute = str(time_str).split(":")
                return time(int(hour), int(minute))
            # Handle HH.MM format
            elif "." in str(time_str):
                hour, minute = str(time_str).split(".")
                return time(int(hour), int(minute))
            # Handle HH format
            else:
                return time(int(time_str), 0)
        except (ValueError, TypeError):
            return None

    def _is_currently_open(self) -> bool:
        """Check if the station is currently open."""
        if not self.coordinator.data or "opening_hours" not in self.coordinator.data:
            return False
        
        opening_hours = self.coordinator.data.get("opening_hours", [])
        if not opening_hours:
            return False
        
        # Get current time in Italy timezone
        now = datetime.now()
        current_weekday = now.weekday() + 1  # Convert to 1-7 (Monday=1)
        current_time = now.time()
        
        # Find today's schedule
        today_schedule = None
        for day in opening_hours:
            if day.get("giornoSettimanaId") == current_weekday:
                today_schedule = day
                break
        
        if not today_schedule:
            return False
        
        # Check if closed today
        if today_schedule.get("flagChiusura"):
            return False
        
        # Check if 24/7
        if today_schedule.get("flagH24"):
            return True
        
        # Check continuous hours
        if today_schedule.get("flagOrarioContinuato"):
            open_time = self._parse_time(today_schedule.get("oraAperturaOrarioContinuato"))
            close_time = self._parse_time(today_schedule.get("oraChiusuraOrarioContinuato"))
            
            if open_time and close_time:
                if open_time <= close_time:
                    # Same day (e.g., 08:00-20:00)
                    return open_time <= current_time <= close_time
                else:
                    # Overnight (e.g., 22:00-06:00)
                    return current_time >= open_time or current_time <= close_time
        
        # Check split hours (morning + afternoon)
        else:
            morning_open = self._parse_time(today_schedule.get("oraAperturaMattina"))
            morning_close = self._parse_time(today_schedule.get("oraChiusuraMattina"))
            afternoon_open = self._parse_time(today_schedule.get("oraAperturaPomeriggio"))
            afternoon_close = self._parse_time(today_schedule.get("oraChiusuraPomeriggio"))
            
            # Check morning slot
            if morning_open and morning_close:
                if morning_open <= current_time <= morning_close:
                    return True
            
            # Check afternoon slot
            if afternoon_open and afternoon_close:
                if afternoon_open <= current_time <= afternoon_close:
                    return True
        
        return False

    @property
    def is_on(self) -> bool:
        """Return True if the station is currently open."""
        return self._is_currently_open()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "current_status": "Aperta" if self._is_currently_open() else "Chiusa",
            "last_updated": datetime.now().isoformat(),
        }


class StationNextChangeSensor(CoordinatorEntity, SensorEntity):
    """Sensor indicating when the station will next open or close."""
    _attr_icon = "mdi:clock-time-eight"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the next change sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_name = "Prossimo Cambio Orario"
        self._attr_unique_id = f"{self._station_id}_next_change"

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

    def _parse_time(self, time_str: str) -> time | None:
        """Parse time string to time object."""
        if not time_str:
            return None
        
        try:
            # Handle HH:MM format
            if ":" in time_str:
                hour, minute = time_str.split(":")
                return time(int(hour), int(minute))
            # Handle HH.MM format
            elif "." in time_str:
                hour, minute = time_str.split(".")
                return time(int(hour), int(minute))
            # Handle HH format
            else:
                return time(int(time_str), 0)
        except (ValueError, TypeError):
            return None

    def _get_next_change(self) -> tuple[str, datetime | None]:
        """Get the next opening or closing time and type."""
        if not self.coordinator.data or "opening_hours" not in self.coordinator.data:
            return "Orari non disponibili", None
        
        opening_hours = self.coordinator.data.get("opening_hours", [])
        if not opening_hours:
            return "Orari non disponibili", None
        
        # Check if 24/7
        for day in opening_hours:
            if day.get("flagH24"):
                return "Sempre aperto", None
        
        now = datetime.now()
        current_weekday = now.weekday() + 1  # Convert to 1-7 (Monday=1)
        current_time = now.time()
        
        # Find today's schedule
        today_schedule = None
        for day in opening_hours:
            if day.get("giornoSettimanaId") == current_weekday:
                today_schedule = day
                break
        
        if not today_schedule or today_schedule.get("flagChiusura"):
            # Station is closed today, find next opening
            return self._find_next_opening(opening_hours, now)
        
        # Check if currently open
        is_open = self._is_currently_open(today_schedule, current_time)
        
        if is_open:
            # Find next closing time
            return self._find_next_closing(today_schedule, now)
        else:
            # Find next opening time
            return self._find_next_opening(opening_hours, now)

    def _is_currently_open(self, schedule: dict, current_time: time) -> bool:
        """Check if station is currently open based on schedule."""
        # Check continuous hours
        if schedule.get("flagOrarioContinuato"):
            open_time = self._parse_time(schedule.get("oraAperturaOrarioContinuato"))
            close_time = self._parse_time(schedule.get("oraChiusuraOrarioContinuato"))
            
            if open_time and close_time:
                if open_time <= close_time:
                    return open_time <= current_time <= close_time
                else:
                    return current_time >= open_time or current_time <= close_time
        
        # Check split hours
        else:
            morning_open = self._parse_time(schedule.get("oraAperturaMattina"))
            morning_close = self._parse_time(schedule.get("oraChiusuraMattina"))
            afternoon_open = self._parse_time(schedule.get("oraAperturaPomeriggio"))
            afternoon_close = self._parse_time(schedule.get("oraChiusuraPomeriggio"))
            
            # Check morning slot
            if morning_open and morning_close:
                if morning_open <= current_time <= morning_close:
                    return True
            
            # Check afternoon slot
            if afternoon_open and afternoon_close:
                if afternoon_open <= current_time <= afternoon_close:
                    return True
        
        return False

    def _find_next_closing(self, schedule: dict, now: datetime) -> tuple[str, datetime | None]:
        """Find the next closing time for today."""
        current_time = now.time()
        
        # Check continuous hours
        if schedule.get("flagOrarioContinuato"):
            close_time = self._parse_time(schedule.get("oraChiusuraOrarioContinuato"))
            if close_time and close_time > current_time:
                close_datetime = now.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
                return "Chiude alle", close_datetime
        
        # Check split hours
        else:
            morning_close = self._parse_time(schedule.get("oraChiusuraMattina"))
            afternoon_close = self._parse_time(schedule.get("oraChiusuraPomeriggio"))
            
            # Check morning closing
            if morning_close and morning_close > current_time:
                close_datetime = now.replace(hour=morning_close.hour, minute=morning_close.minute, second=0, microsecond=0)
                return "Chiude alle", close_datetime
            
            # Check afternoon closing
            if afternoon_close and afternoon_close > current_time:
                close_datetime = now.replace(hour=afternoon_close.hour, minute=afternoon_close.minute, second=0, microsecond=0)
                return "Chiude alle", close_datetime
        
        # If no closing time found today, find next opening
        opening_hours = self.coordinator.data.get("opening_hours", [])
        return self._find_next_opening(opening_hours, now)

    def _find_next_opening(self, opening_hours: list, now: datetime) -> tuple[str, datetime | None]:
        """Find the next opening time."""
        current_weekday = now.weekday() + 1
        current_time = now.time()
        
        # Check remaining time today
        for day_offset in range(7):
            check_weekday = (current_weekday + day_offset - 1) % 7 + 1
            check_date = now.date() + timedelta(days=day_offset)
            
            for day in opening_hours:
                if day.get("giornoSettimanaId") == check_weekday:
                    if day.get("flagChiusura") or day.get("flagNonComunicato"):
                        continue
                    
                    # Check continuous hours
                    if day.get("flagOrarioContinuato"):
                        open_time = self._parse_time(day.get("oraAperturaOrarioContinuato"))
                        if open_time:
                            if day_offset == 0 and open_time > current_time:
                                open_datetime = now.replace(hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0)
                                return "Apre alle", open_datetime
                            elif day_offset > 0:
                                open_datetime = datetime.combine(check_date, open_time)
                                return "Apre alle", open_datetime
                    
                    # Check split hours
                    else:
                        morning_open = self._parse_time(day.get("oraAperturaMattina"))
                        afternoon_open = self._parse_time(day.get("oraAperturaPomeriggio"))
                        
                        # Check morning opening
                        if morning_open:
                            if day_offset == 0 and morning_open > current_time:
                                open_datetime = now.replace(hour=morning_open.hour, minute=morning_open.minute, second=0, microsecond=0)
                                return "Apre alle", open_datetime
                            elif day_offset > 0:
                                open_datetime = datetime.combine(check_date, morning_open)
                                return "Apre alle", open_datetime
                        
                        # Check afternoon opening
                        if afternoon_open:
                            if day_offset == 0 and afternoon_open > current_time:
                                open_datetime = now.replace(hour=afternoon_open.hour, minute=afternoon_open.minute, second=0, microsecond=0)
                                return "Apre alle", open_datetime
                            elif day_offset > 0:
                                open_datetime = datetime.combine(check_date, afternoon_open)
                                return "Apre alle", open_datetime
        
        return "Nessuna apertura prevista", None

    @property
    def native_value(self) -> StateType:
        """Return the next change description."""
        change_type, change_time = self._get_next_change()
        if change_time:
            time_str = change_time.strftime("%H:%M")
            if change_time.date() != datetime.now().date():
                date_str = change_time.strftime("%d/%m")
                return f"{change_type} {time_str} ({date_str})"
            else:
                return f"{change_type} {time_str}"
        return change_type

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        change_type, change_time = self._get_next_change()
        attributes = {
            "change_type": change_type,
            "next_change_time": change_time.isoformat() if change_time else None,
            "last_updated": datetime.now().isoformat(),
        }
        
        if change_time:
            # Calculate minutes until next change
            now = datetime.now()
            minutes_until = int((change_time - now).total_seconds() / 60)
            attributes["minutes_until_change"] = minutes_until
        
        return attributes


class StationServiceBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a binary sensor for a specific station service."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry, service_id: str, service_info: dict) -> None:
        """Initialize the service binary sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._service_id = service_id
        self._service_info = service_info
        
        # Set name, unique_id, and icon from service info
        self._attr_name = service_info["name"]
        self._attr_unique_id = f"{self._station_id}_service_{service_id}"
        self._attr_icon = service_info["icon"]

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
            if isinstance(service, dict) and str(service.get("id")) == self._service_id:
                return True
        
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes with service information."""
        return {
            "service_id": self._service_id,
            "service_name": self._service_info["name"],
            "service_description": self._service_info["description"],
            "service_icon": self._service_info["icon"],
            "service_image_url": self._service_info["image_url"],
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Service binary sensors are always available since we only create them for available services
        return True
