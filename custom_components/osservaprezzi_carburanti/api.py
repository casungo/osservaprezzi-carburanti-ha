"""API helper functions for Osservaprezzi Carburanti."""
from __future__ import annotations
import logging
import aiohttp
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import BASE_URL, STATION_ENDPOINT, DEFAULT_HEADERS

_LOGGER = logging.getLogger(__name__)


async def fetch_station_data(
    hass: HomeAssistant,
    station_id: str,
    timeout: int = 30
) -> dict[str, Any]:
    """Fetch station data from the API.

    Args:
        hass: Home Assistant instance
        station_id: The station ID to fetch
        timeout: Request timeout in seconds

    Returns:
        The station data as a dictionary

    Raises:
        aiohttp.ClientResponseError: If the API returns an error status
        aiohttp.ClientError: If there's a connection error
    """
    session = async_get_clientsession(hass)
    url = f"{BASE_URL}{STATION_ENDPOINT.format(station_id=station_id)}"

    _LOGGER.debug("Fetching station data from: %s", url)

    try:
        async with session.get(url, headers=DEFAULT_HEADERS, timeout=timeout) as response:
            _LOGGER.debug("Station API response status: %s", response.status)

            if response.status == 200:
                data = await response.json()
                _LOGGER.debug("Station API response data: %s", data)
                return data
            elif response.status == 404:
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=f"Station with ID {station_id} not found"
                )
            elif response.status == 429:
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message="Rate limit exceeded. Please try again later."
                )
            else:
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=f"Service error: {response.status} - {response.reason}"
                )
    except aiohttp.ClientError:
        raise  # Re-raise aiohttp errors as-is (ClientResponseError, ClientError, etc.)
    except Exception as e:
        _LOGGER.error("Unexpected error fetching station data: %s", e)
        raise aiohttp.ClientError(f"Unexpected error: {e}")
