"""Config-flow contracts against a real Home Assistant instance."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.osservaprezzi_carburanti import config_flow
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN
from custom_components.osservaprezzi_carburanti.csv_manager import RegistrySnapshot
from tests.ha.test_hub import hub, station


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
        "zone",
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
    assert result["title"] == "Osservaprezzi Carburanti"
    assert result["data"] == {}
    assert next(iter(result["result"].subentries.values())).data[CONF_STATION_ID] == "123"
    validate_station.assert_awaited_once_with(hass, "123")


async def test_nearby_home_path_adds_selected_stations(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Discover locally and create one entry for each selected station."""
    hass.config.latitude = 41.9
    hass.config.longitude = 12.5
    manager = MagicMock()
    manager.registry_station_types.return_value = ()
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
            updated_at=datetime(2026, 7, 28, tzinfo=UTC),
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
        {
            config_flow.CONF_RADIUS_KM: 3.5,
            config_flow.CONF_RESULT_LIMIT: 7,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_station"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATION_ID: ["456", "789"]},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    entry = result["result"]
    assert entry.unique_id == "hub"
    assert {subentry.unique_id for subentry in entry.subentries.values()} == {"456", "789"}
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
    entry = hub("123")
    entry.add_to_hass(hass)
    subentry = next(iter(entry.subentries.values()))
    validate_station = AsyncMock(return_value={"name": "New Station"})
    monkeypatch.setattr(config_flow, "_validate_station", validate_station)
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_STATION_ID: "456",
            "cron_expression": "30 8 * * *",
            "price_stale_hours": "24",
            "tracked_fuels": [],
            "tracked_service_modes": [],
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert subentry.data[CONF_STATION_ID] == "456"
    assert subentry.unique_id == "456"
    assert subentry.title == "New Station"


def _suggestions(result) -> dict:
    """Read suggestions as Home Assistant's frontend receives them."""
    return {
        marker.schema: marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if marker.description and "suggested_value" in marker.description
    }


async def test_area_retry_preserves_fields_and_excludes_configured(hass, monkeypatch) -> None:
    """Keep the user's filters and selections through a temporary API failure."""
    existing = hub("123")
    existing.add_to_hass(hass)
    snapshot = RegistrySnapshot(
        stations=tuple(
            {"id": station_id, "name": f"Station {station_id}", "municipality": "Roma"}
            for station_id in ("123", "456")
        ),
        updated_at=datetime.now(UTC),
        is_stale=False,
    )
    manager = MagicMock()
    manager.registry_station_types.return_value = ()
    manager.async_ensure_registry = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)
    result = await hass.config_entries.subentries.async_init(
        (existing.entry_id, "station"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "area"}
    )
    values = {"municipality": "Unknown", "province": "", "text_filter": "", "result_limit": 9}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], values)
    assert result["errors"] == {"base": "no_stations_found"}
    assert _suggestions(result)["municipality"] == "Unknown"
    assert _suggestions(result)["result_limit"] == 9

    values["municipality"] = "Roma"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], values)
    selector = next(iter(result["data_schema"].schema.values()))
    assert [option["value"] for option in selector.config["options"]] == ["456"]
    validate = AsyncMock(side_effect=config_flow.CannotConnect("offline"))
    monkeypatch.setattr(config_flow, "_validate_station", validate)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STATION_ID: "456"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert _suggestions(result)[CONF_STATION_ID] == "456"

    from types import MappingProxyType

    from homeassistant.config_entries import ConfigSubentry

    payload = station("456")
    payload["data"] = MappingProxyType(payload["data"])
    hass.config_entries.async_add_subentry(existing, ConfigSubentry(**payload))
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_STATION_ID: "456"}
    )
    assert result["step_id"] == "area"
    assert result["errors"] == {"base": "all_stations_configured"}
    assert _suggestions(result)["municipality"] == "Roma"
    assert _suggestions(result)["result_limit"] == 9
    validate.assert_awaited_once()


async def test_custom_cron_validation(hass, monkeypatch) -> None:
    """Reject invalid cron and retain station options until a valid update."""
    entry = hub("123")
    entry.add_to_hass(hass)
    subentry = next(iter(entry.subentries.values()))
    monkeypatch.setattr(
        config_flow, "_validate_station", AsyncMock(return_value={"name": "Station"})
    )
    monkeypatch.setattr(hass.config_entries, "async_reload", AsyncMock(return_value=True))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "station"),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    values = {
        CONF_STATION_ID: "123",
        "cron_expression": "bad",
        "price_stale_hours": "72",
        "tracked_fuels": [],
        "tracked_service_modes": [],
    }
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], values)
    assert result["errors"] == {"cron_expression": "invalid_cron_expression"}
    values["cron_expression"] = "0 6 * * *"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], values)
    assert result["type"] is FlowResultType.ABORT
    assert subentry.data["cron_expression"] == "0 6 * * *"
    assert subentry.data["price_stale_hours"] == 72


async def test_docker_probe_accepts_all_configured_search_results(hass, monkeypatch) -> None:
    """Keep the Docker regression compatible with the duplicate-free selectors."""
    from scripts import ha_docker_probe as probe

    hass.config.latitude = probe.ROME[config_flow.CONF_LATITUDE]
    hass.config.longitude = probe.ROME[config_flow.CONF_LONGITUDE]
    hub("123").add_to_hass(hass)
    manager = MagicMock()
    manager.registry_station_types.return_value = ()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=(
                {
                    "id": "123",
                    "name": "Station",
                    "municipality": "Roma",
                    "province": "RM",
                    "latitude": hass.config.latitude,
                    "longitude": hass.config.longitude,
                },
            ),
            updated_at=datetime.now(UTC),
            is_stale=False,
        )
    )
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)
    await probe._exercise_home_path(hass, require_multi=True)
    await probe._exercise_coordinates_path(hass)
    await probe._exercise_area_path(hass)
    assert not hass.config_entries.flow.async_progress()
    assert not hass.config_entries.subentries.async_progress()
