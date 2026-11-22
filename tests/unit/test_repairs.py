from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.osservaprezzi_carburanti import repairs


def test_repair_flow(monkeypatch):
    entry = SimpleNamespace(entry_id="one", unique_id="station_1", data={"station_id": "1"})
    entries = SimpleNamespace(
        async_get_entry=MagicMock(return_value=entry),
        async_entries=MagicMock(return_value=[entry]),
        async_update_entry=MagicMock(),
        async_reload=AsyncMock(),
        async_remove=AsyncMock(),
    )
    hass = SimpleNamespace(config_entries=entries)
    flow = asyncio.run(repairs.async_create_fix_flow(hass, "station_not_found_one", None))
    flow.hass = hass
    flow.async_create_entry = MagicMock(return_value="done")
    flow.async_show_menu = MagicMock(return_value="menu")
    flow.async_show_form = MagicMock(return_value="form")
    assert asyncio.run(flow.async_step_init()) == "menu"
    assert asyncio.run(flow.async_step_replace_station()) == "form"
    assert asyncio.run(flow.async_step_replace_station({"station_id": " "})) == "form"
    entries.async_entries.return_value.append(
        SimpleNamespace(entry_id="two", unique_id="station_2")
    )
    assert asyncio.run(flow.async_step_replace_station({"station_id": "2"})) == "form"
    for exc in (repairs.InvalidStation, repairs.RateLimited, repairs.CannotConnect):
        monkeypatch.setattr(repairs, "_validate_station", AsyncMock(side_effect=exc))
        assert asyncio.run(flow.async_step_replace_station({"station_id": "3"})) == "form"
    monkeypatch.setattr(repairs, "_validate_station", AsyncMock(return_value={"name": "new"}))
    assert asyncio.run(flow.async_step_replace_station({"station_id": "3"})) == "done"
    assert asyncio.run(flow.async_step_remove_station()) == "form"
    assert asyncio.run(flow.async_step_remove_station({})) == "done"
    entries.async_get_entry.return_value = None
    assert asyncio.run(flow.async_step_init()) == "done"
    assert asyncio.run(flow.async_step_replace_station()) == "done"
    assert (
        asyncio.run(repairs.async_create_fix_flow(hass, "x", {"entry_id": "one"})).entry_id == "one"
    )
