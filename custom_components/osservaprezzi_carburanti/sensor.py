from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
            sensors.extend([
                StationInfoSensor(coordinator, entry, "name", "Nome", "mdi:gas-station"),
                StationInfoSensor(coordinator, entry, "nomeImpianto", "Nome Impianto", "mdi:gas-station"),
                StationInfoSensor(coordinator, entry, "id", "ID Osservaprezzi", "mdi:identifier"),
                StationInfoSensor(coordinator, entry, "address", "Indirizzo", "mdi:map-marker"),
                StationInfoSensor(coordinator, entry, "brand", "Marchio", "mdi:tag"),
                StationInfoSensor(coordinator, entry, "company", "Società", "mdi:office-building"),
                StationInfoSensor(coordinator, entry, "phoneNumber", "Telefono", "mdi:phone"),
                StationInfoSensor(coordinator, entry, "email", "Email", "mdi:email"),
                StationInfoSensor(coordinator, entry, "website", "Sito Web", "mdi:web"),
                # Add a single location sensor for the map marker
                StationLocationSensor(coordinator, entry),
                # Add services sensor
                StationServicesSensor(coordinator, entry),
                # Add opening hours sensor
                StationOpeningHoursSensor(coordinator, entry),
            ])
    
        async_add_entities(sensors, update_before_add=True)


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
            model="Stazione di Servizio",
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
            # Coordinates are not available when retrieving a station by ID
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
            model="Stazione di Servizio",
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
            model="Stazione di Servizio",
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


class StationServicesSensor(CoordinatorEntity, SensorEntity):
    """Representation of a sensor for station services."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:services"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the services sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_name = "Servizi Disponibili"
        self._attr_unique_id = f"{self._station_id}_services"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Stazione di Servizio",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor (comma-separated service descriptions)."""
        if not self.coordinator.data or "services" not in self.coordinator.data:
            return None
        
        services = self.coordinator.data.get("services", [])
        if not services:
            return "Nessun servizio"
        
        # Validate services data structure
        if not isinstance(services, list):
            _LOGGER.warning(f"Services data is not a list: {type(services)}")
            return "Dati servizi non validi"
        
        # Extract service descriptions and join them with commas
        service_descriptions = []
        for service in services:
            if not isinstance(service, dict):
                _LOGGER.warning(f"Service item is not a dictionary: {type(service)}")
                continue
            
            description = service.get("description", "")
            if isinstance(description, str) and description.strip():
                service_descriptions.append(description.strip())
            else:
                _LOGGER.debug(f"Invalid or empty description in service: {service}")
        
        return ", ".join(service_descriptions) if service_descriptions else "Nessun servizio"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes with detailed service information."""
        if not self.coordinator.data or "services" not in self.coordinator.data:
            return {}
        
        services = self.coordinator.data.get("services", [])
        if not services:
            return {"services_count": 0}
        
        # Validate services data structure
        if not isinstance(services, list):
            _LOGGER.warning(f"Services data is not a list: {type(services)}")
            return {"services_count": 0, "data_error": "Services data is not a list"}
        
        # Create detailed attributes for each service
        service_attributes = {}
        valid_services_count = 0
        
        for i, service in enumerate(services):
            if not isinstance(service, dict):
                _LOGGER.warning(f"Service item {i} is not a dictionary: {type(service)}")
                continue
            
            # Safely extract service ID
            service_id = service.get("id")
            if service_id is None:
                service_id = f"service_{i}"
            
            # Safely extract and validate service description
            service_description = service.get("description", "")
            if not isinstance(service_description, str):
                service_description = str(service_description) if service_description is not None else ""
            
            # Add each service as a separate attribute
            service_attributes[f"service_{i+1}_id"] = service_id
            service_attributes[f"service_{i+1}_description"] = service_description
            
            # Count only valid services (those with descriptions)
            if service_description.strip():
                valid_services_count += 1
        
        # Add total count and valid count
        service_attributes["services_count"] = len(services)
        service_attributes["valid_services_count"] = valid_services_count
        
        return service_attributes


class StationOpeningHoursSensor(CoordinatorEntity, SensorEntity):
    """Representation of a sensor for station opening hours."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:store-clock"

    def __init__(self, coordinator: CarburantiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the opening hours sensor."""
        super().__init__(coordinator)
        self._station_id = entry.data[CONF_STATION_ID]
        self._attr_name = "Orari di Apertura"
        self._attr_unique_id = f"{self._station_id}_opening_hours"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        station_info = self.coordinator.data.get("station_info", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._station_id)},
            name=station_info.get("name"),
            manufacturer=station_info.get("brand"),
            model="Stazione di Servizio",
        )

    def _format_day_name(self, day_id: int) -> str:
        """Convert giornoSettimanaId to Italian day name."""
        # Map giornoSettimanaId to Italian day names
        day_mapping = {
            1: "Lun",  # Monday
            2: "Mar",  # Tuesday
            3: "Mer",  # Wednesday
            4: "Gio",  # Thursday
            5: "Ven",  # Friday
            6: "Sab",  # Saturday
            7: "Dom",  # Sunday
            8: "Fest", # Holiday
        }
        
        return day_mapping.get(day_id, f"Giorno{day_id}")

    def _format_time(self, time_str: str) -> str:
        """Format time string to HH:MM format with enhanced validation."""
        import re
        
        if not time_str:
            return ""
        
        # Strip whitespace and normalize
        time_str = str(time_str).strip()
        
        # Try to match various time formats with regex
        # HH:MM format (24-hour)
        match = re.match(r'^(\d{1,2}):(\d{1,2})$', time_str)
        if match:
            hour, minute = match.groups()
            try:
                # Validate hour and minute ranges
                h = int(hour)
                m = int(minute)
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return f"{h:02d}:{m:02d}"
            except ValueError:
                pass
        
        # HH.MM format
        match = re.match(r'^(\d{1,2})\.(\d{1,2})$', time_str)
        if match:
            hour, minute = match.groups()
            try:
                h = int(hour)
                m = int(minute)
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return f"{h:02d}:{m:02d}"
            except ValueError:
                pass
        
        # HH format (hour only, assume 00 minutes)
        match = re.match(r'^(\d{1,2})$', time_str)
        if match:
            hour = match.group(1)
            try:
                h = int(hour)
                if 0 <= h <= 23:
                    return f"{h:02d}:00"
            except ValueError:
                pass
        
        # Handle special cases
        if time_str.lower() in ["24:00", "24:00:00", "24.00"]:
            return "00:00"  # Midnight
        
        # If we can't parse the time, return the original string
        # This preserves any special formatting that might be intentional
        return time_str

    def _format_opening_hours(self) -> str | None:
        """Format opening hours into a readable string."""
        if not self.coordinator.data or "opening_hours" not in self.coordinator.data:
            return None
        
        opening_hours = self.coordinator.data.get("opening_hours", [])
        if not opening_hours:
            return None
        
        # Check if 24/7
        for day in opening_hours:
            if day.get("flagH24"):
                return "24/7"
        
        # Group consecutive days with same hours
        formatted_hours = []
        current_group = []
        current_hours = None
        
        for day in opening_hours:
            day_id = day.get("giornoSettimanaId", 0)
            day_name = self._format_day_name(day_id)
            
            # Skip days with uncommunicated hours
            if day.get("flagNonComunicato"):
                time_str = "Orario non comunicato"
            elif day.get("flagChiusura"):
                time_str = "Chiuso"
            else:
                # Format opening hours based on flagOrarioContinuato
                if day.get("flagOrarioContinuato"):
                    # Continuous hours
                    open_time = self._format_time(day.get("oraAperturaOrarioContinuato", ""))
                    close_time = self._format_time(day.get("oraChiusuraOrarioContinuato", ""))
                    if open_time and close_time:
                        time_str = f"{open_time}-{close_time}"
                    else:
                        time_str = "Orario non disponibile"
                else:
                    # Split hours (morning + afternoon)
                    morning_open = self._format_time(day.get("oraAperturaMattina", ""))
                    morning_close = self._format_time(day.get("oraChiusuraMattina", ""))
                    afternoon_open = self._format_time(day.get("oraAperturaPomeriggio", ""))
                    afternoon_close = self._format_time(day.get("oraChiusuraPomeriggio", ""))
                    
                    if morning_open and morning_close and afternoon_open and afternoon_close:
                        # Two time slots
                        time_str = f"{morning_open}-{morning_close}, {afternoon_open}-{afternoon_close}"
                    elif morning_open and morning_close:
                        # Single time slot (continuous hours)
                        time_str = f"{morning_open}-{morning_close}"
                    else:
                        time_str = "Orario non disponibile"
            
            # Check if this day has the same hours as the current group
            if current_hours == time_str:
                current_group.append(day_name)
            else:
                # Save the previous group if it exists
                if current_group:
                    if len(current_group) == 1:
                        formatted_hours.append(f"{current_group[0]}: {current_hours}")
                    else:
                        formatted_hours.append(f"{current_group[0]}-{current_group[-1]}: {current_hours}")
                
                # Start a new group
                current_group = [day_name]
                current_hours = time_str
        
        # Add the last group
        if current_group:
            if len(current_group) == 1:
                formatted_hours.append(f"{current_group[0]}: {current_hours}")
            else:
                formatted_hours.append(f"{current_group[0]}-{current_group[-1]}: {current_hours}")
        
        return "; ".join(formatted_hours) if formatted_hours else None

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor (formatted opening hours)."""
        return self._format_opening_hours()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes with raw opening hours data."""
        if not self.coordinator.data or "opening_hours" not in self.coordinator.data:
            return {}
        
        opening_hours = self.coordinator.data.get("opening_hours", [])
        if not opening_hours:
            return {"opening_hours_count": 0}
        
        # Create detailed attributes for each day
        opening_hours_attributes = {}
        for i, day in enumerate(opening_hours):
            day_id = day.get("giornoSettimanaId", f"day_{i}")
            day_name = self._format_day_name(day_id) if isinstance(day_id, int) else f"day_{i+1}"
            
            # Add each day's raw data as attributes using correct API field names
            opening_hours_attributes[f"day_{i+1}_id"] = day_id
            opening_hours_attributes[f"day_{i+1}_name"] = day_name
            opening_hours_attributes[f"day_{i+1}_morning_open"] = day.get("oraAperturaMattina")
            opening_hours_attributes[f"day_{i+1}_morning_close"] = day.get("oraChiusuraMattina")
            opening_hours_attributes[f"day_{i+1}_afternoon_open"] = day.get("oraAperturaPomeriggio")
            opening_hours_attributes[f"day_{i+1}_afternoon_close"] = day.get("oraChiusuraPomeriggio")
            opening_hours_attributes[f"day_{i+1}_continuous_open"] = day.get("oraAperturaOrarioContinuato")
            opening_hours_attributes[f"day_{i+1}_continuous_close"] = day.get("oraChiusuraOrarioContinuato")
            opening_hours_attributes[f"day_{i+1}_is_continuous"] = day.get("flagOrarioContinuato", False)
            opening_hours_attributes[f"day_{i+1}_is_24h"] = day.get("flagH24", False)
            opening_hours_attributes[f"day_{i+1}_is_closed"] = day.get("flagChiusura", False)
            opening_hours_attributes[f"day_{i+1}_is_uncommunicated"] = day.get("flagNonComunicato", False)
            opening_hours_attributes[f"day_{i+1}_self_service"] = day.get("flagSelf", False)
            opening_hours_attributes[f"day_{i+1}_served"] = day.get("flagServito", False)
        
        # Add total count
        opening_hours_attributes["opening_hours_count"] = len(opening_hours)
        
        return opening_hours_attributes
