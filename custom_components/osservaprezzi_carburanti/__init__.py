"""L'integrazione Osservaprezzi Carburanti."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import CarburantiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Configura il componente Osservaprezzi Carburanti."""
    hass.data.setdefault(DOMAIN, {})
    # Registra un servizio per creare le card predefinite on-demand
    async def _handle_add_default_cards(call):
        """Handler del servizio per creare le card predefinite."""
        station_info = call.data.get("station_info")
        station_id = call.data.get("station_id")
        # Se è passato solo station_id, proviamo a recuperare info dal coordinator
        if not station_info and station_id:
            # cerca in hass.data per un coordinator con station_id
            for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
                try:
                    if getattr(coordinator, "station_id", None) == station_id:
                        station_info = coordinator.data.get("station_info") or {}
                        break
                except Exception:
                    continue

        if not station_info:
            # nulla da fare
            hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Card Carburanti - errore",
                    "message": "Nessuna informazione sulla stazione fornita al servizio add_default_cards. Specifica station_id o station_info.",
                },
            )
            return

        # Costruisci il contenuto della card come faceva il config flow
        card_config = f"""
# Card automatiche per Osservaprezzi Carburanti
# Stazione: {station_info.get('name')} (ID: {station_info.get('id')})

# Card 1: Prezzi Base
type: custom:carburanti-card
title: "Prezzi {station_info.get('name')}"
subtitle: "Stazione {station_info.get('id')} - Aggiornamento in tempo reale"

---
# Card 2: Analisi Avanzata  
type: custom:carburanti-advanced-card
title: "Analisi Prezzi {station_info.get('name')}"
subtitle: "Statistiche e confronti"

---
# Card 3: Sensori
type: entities
title: "Sensori {station_info.get('name')}"
entities:
  - entity: sensor.osservaprezzi_carburanti_last_update
    name: "Ultimo Aggiornamento"
show_header_toggle: false
"""

        config_dir = hass.config.config_dir
        cards_file = f"{config_dir}/carburanti_cards_{station_info.get('id')}.yaml"
        try:
            with open(cards_file, 'w', encoding='utf-8') as f:
                f.write(card_config)

            hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Card Carburanti Create!",
                    "message": (
                        f"Le card per la stazione {station_info.get('name')} sono state create in {cards_file}. "
                        "Apri <a href=\"/developer-tools/service?domain=osservaprezzi_carburanti&service=add_default_cards\">Developer Tools → Services</a> "
                        "per eseguire il servizio e rigenerarle."
                    ),
                },
            )
        except Exception as e:
            _LOGGER.error(f"Errore nella creazione delle card automatiche dal servizio: {e}")

    hass.services.async_register(DOMAIN, "add_default_cards", _handle_add_default_cards)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura Osservaprezzi Carburanti da una voce di configurazione."""
    station_id = entry.data.get("station_id")

    coordinator = CarburantiDataUpdateCoordinator(hass, station_id)
    
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        raise

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Rimuove una voce di configurazione."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: CarburantiDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ricarica la voce di configurazione."""
    await hass.config_entries.async_reload(entry.entry_id)
