"""Flusso di configurazione per l'integrazione Osservaprezzi Carburanti."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_STATION_ID, BASE_URL, STATION_ENDPOINT, DEFAULT_HEADERS

_LOGGER = logging.getLogger(__name__)


class CannotConnect(HomeAssistantError):
    """Errore per indicare che non è possibile connettersi."""


class InvalidStation(HomeAssistantError):
    """Errore per indicare che la stazione non è valida."""


class OsservaprezziCarburantiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Gestisce un flusso di configurazione per Osservaprezzi Carburanti."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Gestisce il passaggio iniziale."""
        errors = {}

        if user_input is not None:
            try:
                station_id = user_input[CONF_STATION_ID]
                
                # Controlla se questa stazione è già configurata
                await self.async_set_unique_id(station_id)
                self._abort_if_unique_id_configured()
                
                # Valida la stazione e ottieni le informazioni
                station_info = await self._validate_station(station_id)
                
                return self.async_create_entry(
                    title=station_info["name"],
                    data={
                        CONF_STATION_ID: station_id,
                    },
                )
                
            except InvalidStation:
                errors["base"] = "invalid_station"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Eccezione imprevista")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION_ID): str,
                }
            ),
            errors=errors,
        )

    async def _validate_station(self, station_id: str) -> dict[str, Any]:
        """Valida l'ID della stazione e restituisce le informazioni."""
        session = async_get_clientsession(self.hass)
        url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=station_id)}"
        
        try:
            async with session.get(url, headers=DEFAULT_HEADERS, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Controlla se abbiamo dati validi della stazione
                    if not data.get("id") or not data.get("name"):
                        raise InvalidStation("Dati della stazione non validi ricevuti")
                    
                    return {
                        "id": data.get("id"),
                        "name": data.get("name"),
                        "nomeImpianto": data.get("nomeImpianto"),
                        "address": data.get("address"),
                        "brand": data.get("brand"),
                    }
                elif response.status == 404:
                    raise InvalidStation("Stazione non trovata")
                else:
                    raise CannotConnect(f"Errore del servizio: {response.status}")
                    
        except aiohttp.ClientError as err:
            raise CannotConnect(f"Errore di connessione: {err}")
        except Exception as err:
            raise CannotConnect(f"Errore imprevisto: {err}")

    async def async_step_import(self, import_info: dict[str, Any]) -> FlowResult:
        """Configura questa integrazione usando yaml."""
        station_id = import_info[CONF_STATION_ID]
        
        await self.async_set_unique_id(station_id)
        self._abort_if_unique_id_configured()
        
        # Valida la stazione e ottieni le informazioni
        station_info = await self._validate_station(station_id)
        
        return self.async_create_entry(
            title=station_info["name"],
            data={
                CONF_STATION_ID: station_id,
            },
        )
