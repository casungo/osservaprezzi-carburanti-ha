from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.typing import StateType
from .const import (
    ATTR_FUEL_TYPE,
    ATTR_IS_SELF,
    ATTR_LAST_UPDATE,
    ATTR_STATION_ADDRESS,
    ATTR_STATION_BRAND,
    ATTR_STATION_NAME,
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = []
    
    # Sensori per i prezzi dei carburanti
    if coordinator.data and "fuels" in coordinator.data:
        for fuel_key, fuel_data in coordinator.data["fuels"].items():
            sensors.append(CarburanteSensor(coordinator, fuel_key, fuel_data))

    # Sensori per le informazioni della stazione (categorizzati come diagnostici)
    if coordinator.data and "station_info" in coordinator.data:
        sensors.extend([
            StationInfoSensor(coordinator, "name", "Nome", "mdi:gas-station"),
            StationInfoSensor(coordinator, "id", "ID Osservaprezzi", "mdi:identifier"),
            StationInfoSensor(coordinator, "address", "Indirizzo", "mdi:map-marker"),
            StationInfoSensor(coordinator, "brand", "Marchio", "mdi:tag"),
            StationInfoSensor(coordinator, "company", "Compagnia", "mdi:office-building"),
        ])

    async_add_entities(sensors, update_before_add=True)


class CarburanteSensor(CoordinatorEntity, SensorEntity):
    """Representation of a fuel price sensor."""
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "€/L"

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        fuel_key: str,
        fuel_data: dict[str, Any]
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._fuel_key = fuel_key
        self._station_info = coordinator.data.get("station_info", {})
        fuel_name = fuel_data.get("name", "Sconosciuto")
        service_type = "Self" if fuel_data.get("is_self") else "Servito"
        
        # Nome semplificato: non include più il nome della stazione
        self._attr_name = f"{fuel_name} {service_type}"
        
        self._attr_unique_id = f"{coordinator.station_id}_{fuel_key}"
        self.entity_id = f"sensor.{coordinator.station_id}_{fuel_key}".lower()
        self._attr_icon = self._get_fuel_icon(fuel_data.get("name", ""))

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.station_id)},
            name=self._station_info.get("name"),
            manufacturer=self._station_info.get("brand"),
            model="Stazione di Servizio",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if not self.coordinator.data or "fuels" not in self.coordinator.data:
            return None
        fuel_data = self.coordinator.data["fuels"].get(self._fuel_key)
        return fuel_data.get("price") if fuel_data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "fuels" not in self.coordinator.data:
            return {}
        fuel_data = self.coordinator.data["fuels"].get(self._fuel_key, {})
        station_info = self.coordinator.data.get("station_info", {})
        return {
            ATTR_STATION_NAME: station_info.get("name"),
            ATTR_STATION_ADDRESS: station_info.get("address"),
            ATTR_STATION_BRAND: station_info.get("brand"),
            ATTR_FUEL_TYPE: fuel_data.get("name"),
            ATTR_IS_SELF: fuel_data.get("is_self"),
            ATTR_LAST_UPDATE: fuel_data.get("last_update"),
            "company": station_info.get("company"),
        }

    def _get_fuel_icon(self, fuel_name: str) -> str:
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
        elif "e85" in fuel_name_lower or "bio" in fuel_name_lower:
            return "mdi:leaf"
        elif "h2" in fuel_name_lower or "idrogeno" in fuel_name_lower:
            return "mdi:water"
        else:
            return "mdi:fuel"

class StationInfoSensor(CoordinatorEntity, SensorEntity):
    """Representation of a sensor for station information."""
    # Assegna questi sensori alla categoria diagnostica per separarli dai sensori principali
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: CarburantiDataUpdateCoordinator,
        info_key: str,
        name: str,
        icon: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._station_info = coordinator.data.get("station_info", {})
        self._info_key = info_key
        
        # Nome semplificato
        self._attr_name = name
        
        self._attr_unique_id = f"{coordinator.station_id}_{info_key}"
        self.entity_id = f"sensor.{coordinator.station_id}_{info_key}".lower()
        self._attr_icon = icon

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.station_id)},
            name=self._station_info.get("name"),
            manufacturer=self._station_info.get("brand"),
            model="Stazione di Servizio",
        )

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if not self.coordinator.data or "station_info" not in self.coordinator.data:
            return None
        return self.coordinator.data["station_info"].get(self._info_key)