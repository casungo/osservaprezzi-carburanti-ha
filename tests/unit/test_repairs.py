from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.osservaprezzi_carburanti import repairs


def test_repair_flow(monkeypatch):
    station = SimpleNamespace(
        subentry_id="one", unique_id="1", subentry_type="station", data={"station_id": "1"}
    )
    entry = SimpleNamespace(entry_id="hub", subentries={"one": station})
    entries = SimpleNamespace(
        async_get_entry=MagicMock(return_value=entry),
        async_entries=MagicMock(return_value=[entry]),
        async_update_subentry=MagicMock(),
        async_remove_subentry=MagicMock(),
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
    entry.subentries["two"] = SimpleNamespace(
        subentry_id="two", unique_id="2", subentry_type="station", data={"station_id": "2"}
    )
    assert asyncio.run(flow.async_step_replace_station({"station_id": "2"})) == "form"
    for exc in (repairs.InvalidStation, repairs.RateLimited, repairs.CannotConnect):
        monkeypatch.setattr(repairs, "_validate_station", AsyncMock(side_effect=exc))
        assert asyncio.run(flow.async_step_replace_station({"station_id": "3"})) == "form"
    monkeypatch.setattr(repairs, "_validate_station", AsyncMock(return_value={"name": "new"}))
    assert asyncio.run(flow.async_step_replace_station({"station_id": "3"})) == "done"
    assert asyncio.run(flow.async_step_remove_station()) == "form"
    assert asyncio.run(flow.async_step_remove_station({})) == "done"
    entries.async_entries.return_value = []
    assert asyncio.run(flow.async_step_init()) == "done"
    assert asyncio.run(flow.async_step_replace_station()) == "done"
    assert (
        asyncio.run(repairs.async_create_fix_flow(hass, "x", {"entry_id": "one"})).entry_id == "one"
    )


def test_repair_resolves_station_id_and_ignores_other_entries():
    unrelated = SimpleNamespace(entry_id="other", subentries={})
    other_type = SimpleNamespace(subentry_type="other", subentry_id="x", data={})
    wrong_station = SimpleNamespace(
        subentry_type="station", subentry_id="y", data={"station_id": "2"}
    )
    match = SimpleNamespace(subentry_type="station", subentry_id="z", data={"station_id": "3"})
    entry = SimpleNamespace(
        entry_id="hub", subentries={"x": other_type, "y": wrong_station, "z": match}
    )
    flow = repairs.StationNotFoundRepairFlow("hub", "missing", "issue", "3")
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [unrelated, entry])
    )
    assert flow._find_station() == (entry, match)
    flow.station_id = "4"
    assert flow._find_station() is None
    flow.async_create_entry = MagicMock(return_value="done")
    assert asyncio.run(flow.async_step_remove_station({})) == "done"
