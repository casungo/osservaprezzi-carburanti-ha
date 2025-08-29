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
    STATION_ENDPOINT,
    FUEL_TYPES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

class CarburantiDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, station_id: str) -> None:
        self.station_id = station_id
        self.session = async_get_clientsession(hass)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{station_id}",
            update_interval=None,  # Updates are triggered by a listener in __init__.py
        )

    async def _async_update_data(self) -> dict[str, Any]:
        url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=self.station_id)}"
        try:
            _LOGGER.debug("Fetching data from: %s", url)
            async with self.session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Data received: %s", data)
                    return self._process_data(data)
                else:
                    raise UpdateFailed(f"Service error: {response.status} - {response.reason}")
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error fetching data: {err}")
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}")

    def _process_data(self, data: dict[str, Any]) -> dict[str, Any]:
        processed_data = {
            "station_info": {
                "id": data.get("id"),
                "name": data.get("name"),
                "address": data.get("address"),
                "brand": data.get("brand"),
                "company": data.get("company"),
            },
            "fuels": {},
            "last_update": datetime.now().isoformat(),
        }
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
            }
        _LOGGER.debug("Processed data: %s", processed_data)
        return processed_data