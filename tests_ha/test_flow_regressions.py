"""Focused config-flow regressions against real Home Assistant selectors."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import NumberSelector, SelectSelector

from custom_components.osservaprezzi_carburanti import config_flow
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN


async def test_coordinate_selectors_are_bounded_real_number_selectors(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Keep coordinate controls valid under the installed HA selector schema."""
    manager = MagicMock()
    manager.registry_station_types.return_value = ("Stradale", "Autostradale")
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"next_step_id": "coordinates"},
    )

    assert result["type"] is FlowResultType.FORM
    fields = result["data_schema"].schema
    latitude = next(marker for marker in fields if marker.schema == config_flow.CONF_LATITUDE)
    longitude = next(marker for marker in fields if marker.schema == config_flow.CONF_LONGITUDE)
    station_type = next(marker for marker in fields if marker.schema == config_flow.CONF_STATION_TYPE)
    assert isinstance(fields[latitude], NumberSelector)
    assert fields[latitude].config == {
        "min": -90.0,
        "max": 90.0,
        "step": "any",
        "mode": "box",
    }
    assert fields[longitude].config["min"] == -180.0
    assert fields[longitude].config["max"] == 180.0
    assert isinstance(fields[station_type], SelectSelector)
    assert fields[station_type].config["options"] == ["Stradale", "Autostradale"]
    assert fields[station_type].config["custom_value"] is True


async def test_reconfigure_duplicate_is_rejected_before_station_api(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Do not call MIMIT when the requested unique ID already exists."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Current",
        unique_id="station_123",
        data={CONF_STATION_ID: "123"},
    )
    duplicate = MockConfigEntry(
        domain=DOMAIN,
        title="Other",
        unique_id="station_456",
        data={CONF_STATION_ID: "456"},
    )
    entry.add_to_hass(hass)
    duplicate.add_to_hass(hass)
    validate_station = AsyncMock(return_value={"name": "Should not be used"})
    monkeypatch.setattr(config_flow, "_validate_station", validate_station)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATION_ID: "456"},
    )

    assert result["errors"] == {CONF_STATION_ID: "already_configured"}
    validate_station.assert_not_awaited()


async def test_capped_search_selection_uses_real_flow_handler(hass, monkeypatch):
    """Submit the extra capped-results step through Home Assistant's flow manager."""
    from custom_components.osservaprezzi_carburanti.csv_manager import RegistrySnapshot

    manager = MagicMock()
    manager.registry_station_types.return_value = ()
    manager.async_ensure_registry = AsyncMock(return_value=RegistrySnapshot(
        stations=({"id": "123", "name": "Alpha", "municipality": "Roma"},
                  {"id": "456", "name": "Beta", "municipality": "Roma"}),
        updated_at=None,
        is_stale=True,
    ))
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)
    monkeypatch.setattr(config_flow, "_validate_station", AsyncMock(return_value={"name": "Alpha"}))
    monkeypatch.setattr(hass.config_entries, "async_setup", AsyncMock(return_value=True))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "area"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"municipality": "Roma", "result_limit": 1})
    assert result["step_id"] == "select_station_stale_limited"
    assert result["description_placeholders"]["result_limit"] == "1"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STATION_ID: ["123"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_STATION_ID: "123"}
