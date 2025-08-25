"""Coordinatore di aggiornamento dati per Osservaprezzi Carburanti."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BASE_URL,
    DEFAULT_HEADERS,
    DEFAULT_SCAN_INTERVAL,
    STATION_ENDPOINT,
    FUEL_TYPES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CarburantiDataUpdateCoordinator(DataUpdateCoordinator):
    """Classe per gestire il recupero dei dati di Osservaprezzi Carburanti."""

    def __init__(self, hass: HomeAssistant, station_id: str) -> None:
        """Inizializza."""
        self.station_id = station_id
        self.session = async_get_clientsession(hass)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{station_id}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Aggiorna i dati tramite il servizio."""
        url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=self.station_id)}"
        
        try:
            _LOGGER.debug("Recupero dati da: %s", url)
            
            async with self.session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Dati ricevuti: %s", data)
                    
                    return self._process_data(data)
                else:
                    raise UpdateFailed(f"Errore del servizio: {response.status} - {response.reason}")
                    
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Errore nel recupero dati: {err}")
        except Exception as err:
            raise UpdateFailed(f"Errore imprevisto: {err}")

    def _process_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Elabora i dati grezzi del servizio."""
        processed_data = {
            "station_info": {
                "id": data.get("id"),
                "name": data.get("name"),
                "nomeImpianto": data.get("nomeImpianto"),
                "address": data.get("address"),
                "brand": data.get("brand"),
                "company": data.get("company"),
                "phone": data.get("phoneNumber"),
                "email": data.get("email"),
                "website": data.get("website"),
            },
            "fuels": {},
            "services": data.get("services", []),
            "opening_hours": data.get("orariapertura", []),
            "last_update": datetime.now().isoformat(),
        }
        
        # Elabora i dati dei carburanti
        for fuel in data.get("fuels", []):
            fuel_name = fuel.get("name", FUEL_TYPES.get(fuel.get("fuelId"), "Unknown"))
            service_type = "self" if fuel.get("isSelf") else "servito"
            fuel_key = f"{fuel_name}_{service_type}"
            
            processed_data["fuels"][fuel_key] = {
                "id": fuel.get("id"),
                "fuel_id": fuel.get("fuelId"),
                "name": fuel_name,
                "price": fuel.get("price"),
                "is_self": fuel.get("isSelf", False),
                "last_update": fuel.get("insertDate"),
                "validity_date": fuel.get("validityDate"),
                "service_area_id": fuel.get("serviceAreaId"),
            }
        
        _LOGGER.debug("Dati elaborati: %s", processed_data)
        return processed_data
