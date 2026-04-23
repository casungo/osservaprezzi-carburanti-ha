"""API helper functions for Osservaprezzi Carburanti."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_REQUEST_INTERVAL_SECONDS,
    BASE_URL,
    DEFAULT_HEADERS,
    STATION_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)
_REQUEST_LOCK = asyncio.Lock()
_NEXT_ALLOWED_REQUEST_AT = 0.0


async def fetch_station_data(
    hass: HomeAssistant,
    station_id: str,
    timeout: int = 30,
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
        async with _REQUEST_LOCK:
            await _wait_for_request_slot(station_id)
            async with session.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                _LOGGER.debug("Station API response status: %s", response.status)

                if response.status == 200:
                    data = await response.json()
                    _LOGGER.debug("Station API response data: %s", data)
                    return data
                if response.status == 404:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=f"Station with ID {station_id} not found",
                        headers=response.headers,
                    )
                if response.status == 429:
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message="Rate limit exceeded. Please try again later.",
                        headers=response.headers,
                    )
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=f"Service error: {response.status} - {response.reason}",
                    headers=response.headers,
                )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise


async def _wait_for_request_slot(station_id: str) -> None:
    """Serialize outbound station requests to avoid API bursts."""
    global _NEXT_ALLOWED_REQUEST_AT

    wait_time = _NEXT_ALLOWED_REQUEST_AT - time.monotonic()
    if wait_time > 0:
        _LOGGER.debug(
            "Waiting %.2fs before requesting station %s to avoid API bursts",
            wait_time,
            station_id,
        )
        await asyncio.sleep(wait_time)

    _NEXT_ALLOWED_REQUEST_AT = time.monotonic() + API_REQUEST_INTERVAL_SECONDS
