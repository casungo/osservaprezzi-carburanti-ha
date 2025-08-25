"""Piattaforma sensore per Osservaprezzi Carburanti."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION, UnitOfVolume
from homeassistant.core import HomeAssistant
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
    ATTR_VALIDITY_DATE,
    DOMAIN,
)
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Configura la piattaforma sensore Osservaprezzi Carburanti."""
    coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = []

    # Crea sensori per ogni tipo di carburante
    if coordinator.data and "fuels" in coordinator.data:
        for fuel_key, fuel_data in coordinator.data["fuels"].items():
            sensors.append(CarburanteSensor(coordinator, fuel_key, fuel_data))

    async_add_entities(sensors, update_before_add=True)


class CarburanteSensor(CoordinatorEntity, SensorEntity):
    """Rappresentazione di un sensore Osservaprezzi Carburanti."""

    def __init__(
        self, 
        coordinator: CarburantiDataUpdateCoordinator, 
        fuel_key: str, 
        fuel_data: dict[str, Any]
    ) -> None:
        """Inizializza il sensore."""
        super().__init__(coordinator)
        self._fuel_key = fuel_key
        self._fuel_data = fuel_data
        self._station_info = coordinator.data.get("station_info", {})
        
        # Imposta ID univoco
        self._attr_unique_id = f"{coordinator.station_id}_{fuel_key}"
        
        # Imposta nome
        station_name = self._station_info.get("name", f"Stazione {coordinator.station_id}")
        fuel_name = fuel_data.get("name", "Unknown")
        service_type = "Self" if fuel_data.get("is_self") else "Servito"
        self._attr_name = f"{station_name} {fuel_name} {service_type}"
        
        # Imposta classe dispositivo e classe stato
        self._attr_device_class = "monetary"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "€/L"
        
        # Imposta icona in base al tipo di carburante
        self._attr_icon = self._get_fuel_icon(fuel_data.get("name", ""))

    @property
    def native_value(self) -> StateType:
        """Restituisce lo stato del sensore."""
        if not self.coordinator.data or "fuels" not in self.coordinator.data:
            return None
            
        fuel_data = self.coordinator.data["fuels"].get(self._fuel_key)
        if not fuel_data:
            return None
            
        return fuel_data.get("price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Restituisce gli attributi di stato specifici dell'entità."""
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
            ATTR_VALIDITY_DATE: fuel_data.get("validity_date"),
            "fuel_id": fuel_data.get("fuel_id"),
            "service_area_id": fuel_data.get("service_area_id"),
            "company": station_info.get("company"),
            "phone": station_info.get("phone"),
            "email": station_info.get("email"),
            "website": station_info.get("website"),
        }

    def _get_fuel_icon(self, fuel_name: str) -> str:
        """Ottiene l'icona appropriata per il tipo di carburante."""
        fuel_name_lower = fuel_name.lower()
        
        if "benzina" in fuel_name_lower:
            return "mdi:gas-station"
        elif "gasolio" in fuel_name_lower:
            return "mdi:fuel"
        elif "gpl" in fuel_name_lower:
            return "mdi:gas-cylinder"
        elif "metano" in fuel_name_lower:
            return "mdi:gas-station"
        elif "e85" in fuel_name_lower:
            return "mdi:leaf"
        elif "h2" in fuel_name_lower or "idrogeno" in fuel_name_lower:
            return "mdi:water"
        else:
            return "mdi:fuel"
