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
