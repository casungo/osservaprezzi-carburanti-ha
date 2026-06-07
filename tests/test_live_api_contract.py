"""Opt-in smoke tests against the live Osservaprezzi API."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock

import aiohttp
import pytest

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti import api  # noqa: E402
from custom_components.osservaprezzi_carburanti.api import fetch_station_data  # noqa: E402

LIVE_API_ENV = "OSSERVAPREZZI_LIVE_API"
KNOWN_STATION_ID = os.environ.get("OSSERVAPREZZI_LIVE_STATION_ID", "54233")

pytestmark = pytest.mark.skipif(
    os.environ.get(LIVE_API_ENV) != "1",
    reason=f"set {LIVE_API_ENV}=1 to run live upstream contract tests",
)


def test_live_station_api_shape_is_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        async with aiohttp.ClientSession() as session:
            monkeypatch.setattr(api, "async_get_clientsession", MagicMock(return_value=session))
            payload = await fetch_station_data(MagicMock(), KNOWN_STATION_ID, timeout=20)

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
