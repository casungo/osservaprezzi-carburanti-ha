"""Opt-in smoke tests against the live Osservaprezzi API."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from unittest.mock import MagicMock

import aiohttp
import pytest

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti import api  # noqa: E402
from custom_components.osservaprezzi_carburanti.api import fetch_station_data  # noqa: E402
from custom_components.osservaprezzi_carburanti.models import (  # noqa: E402
    StationPayload,
)

LIVE_API_ENV = "OSSERVAPREZZI_LIVE_API"
KNOWN_STATION_ID = os.environ.get("OSSERVAPREZZI_LIVE_STATION_ID", "54233")
_LOGGER = logging.getLogger(__name__)


async def _fetch_station_data_with_retry(
    hass: object, station_id: str
) -> StationPayload:
    for attempt in (1, 2):
        started_at = time.monotonic()
        try:
            return await fetch_station_data(hass, station_id, timeout=20)
        except (aiohttp.ClientConnectionError, TimeoutError) as err:
            elapsed = time.monotonic() - started_at
            _LOGGER.warning(
                "Live API request for station %s failed on attempt "
                "%s/2 after %.1fs (%s)",
                station_id,
                attempt,
                elapsed,
                type(err).__name__,
            )
            if attempt == 2:
                raise
            await asyncio.sleep(5)

    raise AssertionError("unreachable")


def test_live_api_retries_a_transient_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        calls = 0

        async def _fetch(*_args: object, **_kwargs: object) -> StationPayload:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError
            return {
                "id": 54233,
                "name": "Station",
                "fuels": [],
                "services": [],
                "orariapertura": [],
            }

        async def _skip_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr(sys.modules[__name__], "fetch_station_data", _fetch)
        monkeypatch.setattr(asyncio, "sleep", _skip_sleep)

        payload = await _fetch_station_data_with_retry(MagicMock(), "54233")

        assert payload["id"] == 54233
        assert calls == 2

    asyncio.run(_run())


@pytest.mark.skipif(
    os.environ.get(LIVE_API_ENV) != "1",
    reason=f"set {LIVE_API_ENV}=1 to run live upstream contract tests",
)
def test_live_station_api_shape_is_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async with aiohttp.ClientSession() as session:
            monkeypatch.setattr(api, "async_get_clientsession", MagicMock(return_value=session))
            payload = await _fetch_station_data_with_retry(
                MagicMock(), KNOWN_STATION_ID
            )

        assert payload["id"] == int(KNOWN_STATION_ID)
        assert isinstance(payload.get("name"), str)
        assert isinstance(payload.get("fuels"), list)
        assert payload["fuels"]
        first_fuel = payload["fuels"][0]
        for key in ("name", "price", "fuelId", "isSelf", "serviceAreaId"):
            assert key in first_fuel
        assert isinstance(payload.get("orariapertura"), list)
        assert isinstance(payload.get("services"), list)

    asyncio.run(_run())
