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


def _suggestions(result) -> dict:
    """Read suggestions as Home Assistant's frontend receives them."""
    return {
        marker.schema: marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if marker.description and "suggested_value" in marker.description
    }


async def test_area_retry_preserves_fields_and_excludes_configured(hass, monkeypatch) -> None:
    """Keep the user's filters and selections through a temporary API failure."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    existing = MockConfigEntry(domain=DOMAIN, unique_id="station_123", data={CONF_STATION_ID: "123"})
    existing.add_to_hass(hass)
    snapshot = RegistrySnapshot(
        stations=tuple({"id": station_id, "name": f"Station {station_id}", "municipality": "Roma"}
                       for station_id in ("123", "456")),
        updated_at=datetime.now(timezone.utc), is_stale=False,
    )
    manager = MagicMock()
    manager.registry_station_types.return_value = ()
    manager.async_ensure_registry = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "area"})
    values = {"municipality": "Unknown", "province": "", "text_filter": "", "result_limit": 9}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    assert result["errors"] == {"base": "no_stations_found"}
    assert _suggestions(result)["municipality"] == "Unknown"
    assert _suggestions(result)["result_limit"] == 9

    values["municipality"] = "Roma"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], values)
    selector = next(iter(result["data_schema"].schema.values()))
    assert [option["value"] for option in selector.config["options"]] == ["456"]
    validate = AsyncMock(side_effect=config_flow.CannotConnect("offline"))
    monkeypatch.setattr(config_flow, "_validate_station", validate)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STATION_ID: ["456"]})
    assert result["errors"] == {"base": "cannot_connect"}
    assert _suggestions(result)[CONF_STATION_ID] == ["456"]

    second = MockConfigEntry(domain=DOMAIN, unique_id="station_456", data={CONF_STATION_ID: "456"})
    second.add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STATION_ID: ["456"]})
    assert result["step_id"] == "area"
    assert result["errors"] == {"base": "all_stations_configured"}
    assert _suggestions(result)["municipality"] == "Roma"
    assert _suggestions(result)["result_limit"] == 9
    validate.assert_awaited_once()


async def test_options_presets_and_custom_cron(hass) -> None:
    """Preserve existing custom schedules and use native translated selectors."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.osservaprezzi_carburanti.const import CONF_CRON_EXPRESSION, CONF_PRICE_STALE_HOURS

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="station_123", data={CONF_STATION_ID: "123"},
        options={CONF_CRON_EXPRESSION: "17 9 * * 2", CONF_PRICE_STALE_HOURS: 48},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = result["data_schema"].schema
    schedule_marker = next(marker for marker in fields if marker.schema == "refresh_schedule")
    assert schedule_marker.default() == "custom"
    assert fields[schedule_marker].config["translation_key"] == "refresh_schedule"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh_schedule": "custom", CONF_PRICE_STALE_HOURS: 72}
    )
    assert result["step_id"] == "custom"
    assert next(iter(result["data_schema"].schema)).default() == "17 9 * * 2"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_CRON_EXPRESSION: "bad"})
    assert result["errors"] == {CONF_CRON_EXPRESSION: "invalid_cron_expression"}
    assert _suggestions(result)[CONF_CRON_EXPRESSION] == "bad"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_CRON_EXPRESSION: "0 6 * * *"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_CRON_EXPRESSION: "0 6 * * *", CONF_PRICE_STALE_HOURS: 72}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"refresh_schedule": "every_six_hours", CONF_PRICE_STALE_HOURS: 24}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_CRON_EXPRESSION: "0 */6 * * *", CONF_PRICE_STALE_HOURS: 24}


async def test_docker_probe_accepts_all_configured_search_results(hass, monkeypatch) -> None:
    """Keep the Docker regression compatible with the duplicate-free selectors."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from scripts import ha_docker_probe as probe

    hass.config.latitude = probe.ROME[config_flow.CONF_LATITUDE]
    hass.config.longitude = probe.ROME[config_flow.CONF_LONGITUDE]
    MockConfigEntry(
        domain=DOMAIN, unique_id="station_123", data={CONF_STATION_ID: "123"}
    ).add_to_hass(hass)
    manager = MagicMock()
    manager.registry_station_types.return_value = ()
    manager.async_ensure_registry = AsyncMock(return_value=RegistrySnapshot(
        stations=({"id": "123", "name": "Station", "municipality": "Roma", "province": "RM",
                   "latitude": hass.config.latitude, "longitude": hass.config.longitude},),
        updated_at=datetime.now(timezone.utc), is_stale=False,
    ))
    monkeypatch.setattr(config_flow, "get_shared_csv_manager", lambda hass: manager)
    await probe._exercise_home_path(hass, require_multi=True)
    await probe._exercise_coordinates_path(hass)
    await probe._exercise_area_path(hass)
    assert not hass.config_entries.flow.async_progress()
