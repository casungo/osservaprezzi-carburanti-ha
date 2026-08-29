"""Config-flow contracts against a real Home Assistant instance."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.osservaprezzi_carburanti import config_flow
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN
from custom_components.osservaprezzi_carburanti.csv_manager import RegistrySnapshot


async def test_manual_station_id_path(hass: HomeAssistant, monkeypatch) -> None:
    """Keep manual station ID setup available from the initial menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "home",
        "coordinates",
        "area",
        "station_id",
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "station_id"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station_id"

    validate_station = AsyncMock(return_value={"name": "Manual Station"})
    monkeypatch.setattr(config_flow, "_validate_station", validate_station)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATION_ID: "123"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Manual Station"
    assert result["data"] == {CONF_STATION_ID: "123"}
    validate_station.assert_awaited_once_with(hass, "123")


async def test_nearby_home_path_adds_selected_stations(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Discover locally and create one entry for each selected station."""
    hass.config.latitude = 41.9
    hass.config.longitude = 12.5
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=(
                {
                    "id": "456",
                    "name": "Nearby Station",
                    "brand": "Brand",
                    "address": "Via Roma 1",
                    "latitude": 41.901,
                    "longitude": 12.5,
                },
                {
                    "id": "789",
                    "name": "Second Station",
                    "latitude": 41.902,
                    "longitude": 12.5,
                },
            ),
            updated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
            is_stale=False,
        )
    )
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)
    validate_station = AsyncMock(
        side_effect=[{"name": "Nearby Station"}, {"name": "Second Station"}]
    )
    monkeypatch.setattr(config_flow, "_validate_station", validate_station)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "home"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "home"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {config_flow.CONF_RADIUS_KM: 5},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_station"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATION_ID: ["456", "789"]},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_STATION_ID: "456"}
    assert result["result"].unique_id == "station_456"
    entries = hass.config_entries.async_entries(DOMAIN)
    assert {entry.data[CONF_STATION_ID] for entry in entries} == {"456", "789"}
    assert {entry.unique_id for entry in entries} == {"station_456", "station_789"}
    manager.async_ensure_registry.assert_awaited_once_with(allow_stale=True)
    assert validate_station.await_args_list == [
        ((hass, "456"),),
        ((hass, "789"),),
    ]


async def test_reconfigure_changes_station_in_place(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Use Home Assistant's reconfigure contract without creating a new entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Old Station",
        unique_id="station_123",
        data={CONF_STATION_ID: "123"},
    )
    entry.add_to_hass(hass)
    validate_station = AsyncMock(return_value={"name": "New Station"})
    monkeypatch.setattr(config_flow, "_validate_station", validate_station)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATION_ID: "456"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_STATION_ID: "456"}
    assert entry.unique_id == "station_456"
    assert entry.title == "New Station"
