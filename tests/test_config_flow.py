"""Tests for config flow validation helpers."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.config_flow import (  # noqa: E402
    CannotConnect,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MUNICIPALITY,
    CONF_PROVINCE,
    CONF_RADIUS_KM,
    CONF_RESULT_LIMIT,
    CONF_STATION_TYPE,
    CONF_TEXT_FILTER,
    InvalidStation,
    OptionsFlowHandler,
    OsservaprezziCarburantiConfigFlow,
    _validate_station,
)
from custom_components.osservaprezzi_carburanti.csv_manager import (  # noqa: E402
    RegistrySnapshot,
    RegistryUnavailableError,
)
from custom_components.osservaprezzi_carburanti.discovery import (  # noqa: E402
    StationCandidate,
)
from custom_components.osservaprezzi_carburanti.const import (  # noqa: E402
    CONF_CRON_EXPRESSION,
    CONF_PRICE_STALE_HOURS,
    CONF_STATION_ID,
    DEFAULT_CRON_EXPRESSION,
    DEFAULT_PRICE_STALE_HOURS,
)


def _make_response_error(status: int) -> aiohttp.ClientResponseError:
    """Create a minimal response error for tests."""
    return aiohttp.ClientResponseError(
        request_info=cast(Any, None),
        history=(),
        status=status,
        message="test",
        headers=cast(Any, None),
    )


def test_validate_station_success(monkeypatch):
    hass_mock = AsyncMock()
    fetch_mock = AsyncMock(return_value={"id": "1234", "name": "Test Station"})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        fetch_mock,
    )

    result = asyncio.run(_validate_station(hass_mock, " 1234 "))
    assert result == {"name": "Test Station"}
    fetch_mock.assert_awaited_once_with(hass_mock, "1234")


def test_validate_station_invalid_payload(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(return_value={"id": "1234"}),
    )

    with pytest.raises(InvalidStation, match="Invalid station data"):
        asyncio.run(_validate_station(AsyncMock(), "1234"))


def test_validate_station_not_found(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(side_effect=_make_response_error(404)),
    )

    try:
        asyncio.run(_validate_station(AsyncMock(), "1234"))
    except InvalidStation:
        pass
    else:
        raise AssertionError("Expected InvalidStation")


def test_validate_station_service_error(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(side_effect=_make_response_error(500)),
    )

    with pytest.raises(CannotConnect, match="Service error: 500"):
        asyncio.run(_validate_station(AsyncMock(), "1234"))


def test_validate_station_connection_error(monkeypatch):
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.fetch_station_data",
        AsyncMock(side_effect=aiohttp.ClientError("boom")),
    )

    try:
        asyncio.run(_validate_station(AsyncMock(), "1234"))
    except CannotConnect:
        pass
    else:
        raise AssertionError("Expected CannotConnect")


def test_validate_station_empty_id():
    try:
        asyncio.run(_validate_station(AsyncMock(), "   "))
    except InvalidStation:
        pass
    else:
        raise AssertionError("Expected InvalidStation")


def _make_config_flow(monkeypatch: pytest.MonkeyPatch) -> OsservaprezziCarburantiConfigFlow:
    flow = OsservaprezziCarburantiConfigFlow()
    flow.hass = MagicMock()
    flow.hass.data = {}
    flow.hass.async_add_executor_job = AsyncMock(
        side_effect=lambda function, *args: function(*args)
    )
    flow.hass.config_entries.flow.async_init = AsyncMock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(
        side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
    )
    flow.async_show_form = MagicMock(
        side_effect=lambda **kwargs: {"type": "form", **kwargs}
    )
    flow.async_show_menu = MagicMock(
        side_effect=lambda **kwargs: {"type": "menu", **kwargs}
    )
    flow.async_abort = MagicMock(
        side_effect=lambda **kwargs: {"type": "abort", **kwargs}
    )
    flow._get_reconfigure_entry = MagicMock()
    flow._async_current_entries = MagicMock(return_value=[])
    flow.async_update_and_abort = MagicMock(
        side_effect=lambda entry, **kwargs: {
            "type": "abort",
            "reason": "reconfigure_successful",
            "entry": entry,
            **kwargs,
        }
    )
    return flow


def test_config_flow_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)
    validate_mock = AsyncMock(return_value={"name": "Station"})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        validate_mock,
    )

    result = asyncio.run(flow.async_step_user({CONF_STATION_ID: " 123 "}))

    assert result == {
        "type": "create_entry",
        "title": "Station",
        "data": {CONF_STATION_ID: "123"},
    }
    flow.async_set_unique_id.assert_awaited_once_with("station_123")
    flow._abort_if_unique_id_configured.assert_called_once()
    validate_mock.assert_awaited_once_with(flow.hass, "123")


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (InvalidStation("bad"), "invalid_station"),
        (CannotConnect("down"), "cannot_connect"),
        (ValueError("unexpected"), "unknown"),
    ],
)
def test_config_flow_user_errors_show_form(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Exception,
    error: str,
) -> None:
    flow = _make_config_flow(monkeypatch)
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(side_effect=side_effect),
    )

    result = asyncio.run(flow.async_step_user({CONF_STATION_ID: "123"}))

    assert result["type"] == "form"
    assert result["step_id"] == "station_id"
    assert result["errors"] == {"base": error}


def test_config_flow_user_initial_form(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)

    result = asyncio.run(flow.async_step_user())

    assert result["type"] == "menu"
    assert result["step_id"] == "user"
    assert result["menu_options"] == [
        "home",
        "coordinates",
        "area",
        "station_id",
    ]


def _candidate(station_id: str = "123") -> StationCandidate:
    return StationCandidate(
        station_id=station_id,
        name="Station",
        brand="Brand",
        address="Via Roma 1",
        municipality="Roma",
        province="RM",
        station_type="Stradale",
        distance_km=1.25,
    )


def test_config_flow_manual_id_step(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)

    result = asyncio.run(flow.async_step_station_id())

    assert result["type"] == "form"
    assert result["step_id"] == "station_id"
    assert result["errors"] == {}


def test_config_flow_home_search_and_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=(
                {
                    "id": "123",
                    "name": "Station",
                    "brand": "Brand",
                    "address": "Via Roma 1",
                    "latitude": 41.91,
                    "longitude": 12.5,
                },
            ),
            updated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
            is_stale=False,
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(flow.async_step_home({CONF_RADIUS_KM: 5}))

    assert result["type"] == "form"
    assert result["step_id"] == "select_station"
    assert result["errors"] == {}
    manager.async_ensure_registry.assert_awaited_once_with(allow_stale=True)

    validate_mock = AsyncMock(return_value={"name": "Station"})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        validate_mock,
    )
    result = asyncio.run(flow.async_step_select_station({CONF_STATION_ID: "123"}))

    assert result == {
        "type": "create_entry",
        "title": "Station",
        "data": {CONF_STATION_ID: "123"},
    }
    validate_mock.assert_awaited_once_with(flow.hass, "123")


def test_config_flow_adds_multiple_selected_stations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._nearby_candidates = (_candidate("123"), _candidate("456"))
    flow._registry_is_stale = False
    flow._registry_updated = "2026-08-28"
    validate_mock = AsyncMock(
        side_effect=[{"name": "First"}, {"name": "Second"}]
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        validate_mock,
    )

    result = asyncio.run(
        flow.async_step_select_station({CONF_STATION_ID: ["123", "456"]})
    )

    assert result == {
        "type": "create_entry",
        "title": "First",
        "data": {CONF_STATION_ID: "123"},
    }
    flow.hass.config_entries.flow.async_init.assert_awaited_once_with(
        "osservaprezzi_carburanti",
        context={"source": "import"},
        data={CONF_STATION_ID: "456", "name": "Second"},
    )
    assert validate_mock.await_args_list == [
        ((flow.hass, "123"),),
        ((flow.hass, "456"),),
    ]


def test_config_flow_imports_validated_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)

    assert asyncio.run(flow.async_step_import()) == {
        "type": "abort",
        "reason": "invalid_station",
    }
    assert asyncio.run(
        flow.async_step_import({CONF_STATION_ID: "123", "name": "Station"})
    ) == {
        "type": "create_entry",
        "title": "Station",
        "data": {CONF_STATION_ID: "123"},
    }


def test_config_flow_skips_already_configured_batch_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._nearby_candidates = (_candidate("123"), _candidate("456"))
    flow._registry_is_stale = False
    flow._registry_updated = "2026-08-28"
    flow._async_current_entries.return_value = [
        MagicMock(data={CONF_STATION_ID: "456"})
    ]
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(return_value={"name": "New Station"}),
    )

    result = asyncio.run(
        flow.async_step_select_station({CONF_STATION_ID: ["123", "456"]})
    )

    assert result == {
        "type": "create_entry",
        "title": "New Station",
        "data": {CONF_STATION_ID: "123"},
    }
    flow.hass.config_entries.flow.async_init.assert_not_awaited()


def test_config_flow_rejects_batch_when_all_stations_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._nearby_candidates = (_candidate("123"), _candidate("456"))
    flow._async_current_entries.return_value = [
        MagicMock(data={CONF_STATION_ID: "123"}),
        MagicMock(data={CONF_STATION_ID: "456"}),
    ]

    result = asyncio.run(
        flow.async_step_select_station({CONF_STATION_ID: ["123", "456"]})
    )

    assert result["errors"] == {"base": "already_configured"}


def test_config_flow_home_uses_stale_registry_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=(
                {
                    "id": "123",
                    "name": "Station",
                    "latitude": 41.91,
                    "longitude": 12.5,
                },
            ),
            updated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
            is_stale=True,
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(flow.async_step_home({CONF_RADIUS_KM: 5}))

    assert result["step_id"] == "select_station_stale"
    assert result["description_placeholders"] == {
        "registry_updated": "2026-07-27T00:00:00+00:00",
        "result_count": "1",
        "configured_count": "0",
    }


def test_config_flow_home_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = None
    flow.hass.config.longitude = None

    result = asyncio.run(flow.async_step_home({CONF_RADIUS_KM: 5}))

    assert result["errors"] == {"base": "home_location_unavailable"}

    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        side_effect=RegistryUnavailableError("unavailable")
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(flow.async_step_home({CONF_RADIUS_KM: 5}))

    assert result["errors"] == {"base": "registry_unavailable"}


def test_config_flow_home_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=(),
            updated_at=None,
            is_stale=False,
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(flow.async_step_home({CONF_RADIUS_KM: 5}))

    assert result["errors"] == {"base": "no_stations_found"}


def test_config_flow_home_accepts_custom_radius_and_result_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    manager = _registry_manager(
        (
            {
                "id": "123",
                "name": "Station",
                "latitude": 41.91,
                "longitude": 12.5,
            },
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(
        flow.async_step_home(
            {CONF_RADIUS_KM: 3.5, CONF_RESULT_LIMIT: 7}
        )
    )

    assert result["step_id"] == "select_station"


def test_config_flow_home_formats_missing_registry_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=(
                {
                    "id": "123",
                    "name": "Station",
                    "latitude": 41.91,
                    "longitude": 12.5,
                },
            ),
            updated_at=None,
            is_stale=True,
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(flow.async_step_home({CONF_RADIUS_KM: 5}))

    assert result["description_placeholders"] == {
        "registry_updated": "—",
        "result_count": "1",
        "configured_count": "0",
    }


def test_config_flow_rejects_selection_outside_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._nearby_candidates = (_candidate(),)
    flow._registry_is_stale = False
    flow._registry_updated = "2026-07-28"

    result = asyncio.run(
        flow.async_step_select_station({CONF_STATION_ID: "not-in-results"})
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_station"}


def test_config_flow_select_without_results_returns_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)

    result = asyncio.run(flow.async_step_select_station())

    assert result["step_id"] == "home"


def test_config_flow_stale_selection_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._nearby_candidates = (_candidate(),)
    flow._registry_is_stale = True
    flow._registry_updated = "2026-07-27"

    result = asyncio.run(flow.async_step_select_station_stale())

    assert result["step_id"] == "select_station_stale"


@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (CannotConnect("down"), "cannot_connect"),
        (ValueError("unexpected"), "unknown"),
    ],
)
def test_config_flow_nearby_selection_errors(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Exception,
    expected_error: str,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._nearby_candidates = (_candidate(),)
    flow._registry_is_stale = False
    flow._registry_updated = "2026-07-28"
    monkeypatch.setattr(
        flow,
        "_async_create_station_entry",
        AsyncMock(side_effect=side_effect),
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(return_value={"name": "Station"}),
    )

    result = asyncio.run(
        flow.async_step_select_station({CONF_STATION_ID: "123"})
    )

    assert result["errors"] == {"base": expected_error}


def test_candidate_label_contains_distance_location_and_id() -> None:
    label = OsservaprezziCarburantiConfigFlow._format_candidate_label(_candidate())

    assert label == "1.2 km · Station · Brand · Stradale · Via Roma 1 · ID 123"


def test_candidate_label_without_distance_uses_area_and_avoids_duplicate_brand() -> None:
    candidate = StationCandidate(
        station_id="456",
        name="Brand Roma",
        brand="Brand",
        address=None,
        municipality="Roma",
        province="RM",
        station_type=None,
    )

    label = OsservaprezziCarburantiConfigFlow._format_candidate_label(candidate)

    assert label == "Brand Roma · Roma, RM · ID 456"


def test_candidate_label_caps_long_chip_text() -> None:
    candidate = StationCandidate(
        station_id="987654",
        name="Stazione di servizio con un nome molto lungo",
        brand="Un marchio altrettanto lungo",
        address="Via con un indirizzo molto lungo 123456789",
        municipality="Roma",
        province="RM",
        station_type="Stradale",
        distance_km=1.25,
    )

    label = OsservaprezziCarburantiConfigFlow._format_candidate_label(candidate)

    assert len(label) == 64
    assert label.endswith(" · ID 987654")


def _registry_manager(
    stations: tuple[dict[str, Any], ...],
    *,
    stale: bool = False,
) -> MagicMock:
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        return_value=RegistrySnapshot(
            stations=stations,
            updated_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
            is_stale=stale,
        )
    )
    return manager


def test_config_flow_coordinates_search_with_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    manager = _registry_manager(
        (
            {
                "id": "123",
                "name": "Stazione Città",
                "station_type": "Stradale",
                "latitude": 41.9,
                "longitude": 12.5,
            },
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(
        flow.async_step_coordinates(
            {
                CONF_LATITUDE: 41.9,
                CONF_LONGITUDE: 12.5,
                CONF_RADIUS_KM: 5,
                CONF_RESULT_LIMIT: 5,
                CONF_TEXT_FILTER: "citta",
                CONF_STATION_TYPE: "strada",
            }
        )
    )

    assert result["step_id"] == "select_station"
    assert result["description_placeholders"]["result_count"] == "1"


@pytest.mark.parametrize(
    "user_input",
    [
        {
            CONF_LATITUDE: 91,
            CONF_LONGITUDE: 12.5,
            CONF_RADIUS_KM: 5,
        },
        {
            CONF_LATITUDE: "bad",
            CONF_LONGITUDE: 12.5,
            CONF_RADIUS_KM: 5,
        },
    ],
)
def test_config_flow_coordinates_rejects_invalid_location(
    monkeypatch: pytest.MonkeyPatch,
    user_input: dict[str, Any],
) -> None:
    flow = _make_config_flow(monkeypatch)

    result = asyncio.run(flow.async_step_coordinates(user_input))

    assert result["errors"] == {"base": "invalid_location"}


def test_config_flow_coordinates_registry_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    manager = MagicMock()
    manager.async_ensure_registry = AsyncMock(
        side_effect=RegistryUnavailableError("offline")
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(
        flow.async_step_coordinates(
            {
                CONF_LATITUDE: 41.9,
                CONF_LONGITUDE: 12.5,
                CONF_RADIUS_KM: 5,
            }
        )
    )

    assert result["errors"] == {"base": "registry_unavailable"}


def test_config_flow_area_search_and_selection_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    manager = _registry_manager(
        (
            {
                "id": "123",
                "name": "Station",
                "municipality": "Roma",
                "province": "RM",
            },
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(
        flow.async_step_area(
            {
                CONF_MUNICIPALITY: "Roma",
                CONF_PROVINCE: "RM",
                CONF_RESULT_LIMIT: 10,
            }
        )
    )
    assert result["step_id"] == "select_station"

    flow._nearby_candidates = ()
    result = asyncio.run(flow.async_step_select_station())
    assert result["step_id"] == "area"


def test_config_flow_area_reports_no_results_and_registry_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    manager = _registry_manager(())
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(
        flow.async_step_area({CONF_MUNICIPALITY: "Roma"})
    )
    assert result["errors"] == {"base": "no_stations_found"}

    manager.async_ensure_registry = AsyncMock(
        side_effect=RegistryUnavailableError("offline")
    )
    result = asyncio.run(
        flow.async_step_area({CONF_MUNICIPALITY: "Roma"})
    )
    assert result["errors"] == {"base": "registry_unavailable"}


def test_config_flow_area_accepts_custom_result_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    manager = _registry_manager(
        (
            {
                "id": "123",
                "name": "Station",
                "municipality": "Roma",
            },
        )
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
        lambda hass: manager,
    )

    result = asyncio.run(
        flow.async_step_area(
            {
                CONF_MUNICIPALITY: "Roma",
                CONF_RESULT_LIMIT: 3,
            }
        )
    )

    assert result["step_id"] == "select_station"


@pytest.mark.parametrize(
    ("step", "user_input"),
    [
        ("area", {CONF_MUNICIPALITY: "Roma", CONF_RESULT_LIMIT: 0}),
        (
            "home",
            {CONF_RADIUS_KM: 0, CONF_RESULT_LIMIT: 20},
        ),
        (
            "home",
            {CONF_RADIUS_KM: 5, CONF_RESULT_LIMIT: 0},
        ),
    ],
)
def test_config_flow_rejects_out_of_range_search_values(
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    user_input: dict[str, Any],
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow.hass.config.latitude = 41.9
    flow.hass.config.longitude = 12.5
    if step == "area":
        manager = _registry_manager(())
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.config_flow.get_shared_csv_manager",
            lambda hass: manager,
        )

    result = asyncio.run(getattr(flow, f"async_step_{step}")(user_input))

    assert result["errors"] == {"base": "unknown"}


def test_config_flow_reconfigure_updates_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    entry = MagicMock(
        entry_id="entry-1",
        unique_id="station_123",
        data={CONF_STATION_ID: "123"},
    )
    flow._get_reconfigure_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(return_value={"name": "New Station"}),
    )

    result = asyncio.run(
        flow.async_step_reconfigure({CONF_STATION_ID: " 456 "})
    )

    assert result["reason"] == "reconfigure_successful"
    assert result["unique_id"] == "station_456"
    assert result["title"] == "New Station"
    assert result["data_updates"] == {CONF_STATION_ID: "456"}


def test_config_flow_reconfigure_initial_and_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _make_config_flow(monkeypatch)
    entry = MagicMock(
        entry_id="entry-1",
        unique_id="station_123",
        data={CONF_STATION_ID: "123"},
    )
    flow._get_reconfigure_entry.return_value = entry

    result = asyncio.run(flow.async_step_reconfigure())
    assert result["step_id"] == "reconfigure"

    flow._async_current_entries.return_value = [
        entry,
        MagicMock(entry_id="entry-2", unique_id="station_456"),
    ]
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(return_value={"name": "Duplicate"}),
    )
    result = asyncio.run(
        flow.async_step_reconfigure({CONF_STATION_ID: "456"})
    )
    assert result["errors"] == {"base": "already_configured"}


@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (InvalidStation("bad"), "invalid_station"),
        (CannotConnect("down"), "cannot_connect"),
        (ValueError("unexpected"), "unknown"),
    ],
)
def test_config_flow_reconfigure_errors(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Exception,
    expected_error: str,
) -> None:
    flow = _make_config_flow(monkeypatch)
    flow._get_reconfigure_entry.return_value = MagicMock(
        entry_id="entry-1",
        data={CONF_STATION_ID: "123"},
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow._validate_station",
        AsyncMock(side_effect=side_effect),
    )

    result = asyncio.run(
        flow.async_step_reconfigure({CONF_STATION_ID: "456"})
    )

    assert result["errors"] == {"base": expected_error}


def _make_options_flow(options: dict[str, Any] | None = None) -> OptionsFlowHandler:
    handler = object.__new__(OptionsFlowHandler)
    handler.config_entry = MagicMock(title="Station")
    handler.options = options or {}
    handler.async_create_entry = MagicMock(
        side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
    )
    handler.async_show_form = MagicMock(
        side_effect=lambda **kwargs: {"type": "form", **kwargs}
    )
    return handler


def test_async_get_options_flow_returns_handler() -> None:
    handler = OsservaprezziCarburantiConfigFlow.async_get_options_flow(MagicMock())

    assert isinstance(handler, OptionsFlowHandler)


def test_options_flow_initial_form() -> None:
    handler = _make_options_flow({CONF_CRON_EXPRESSION: "0 6 * * *"})

    result = asyncio.run(handler.async_step_init())

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {}
    assert "next_run" in result["description_placeholders"]


def test_options_flow_valid_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _make_options_flow({CONF_CRON_EXPRESSION: DEFAULT_CRON_EXPRESSION})
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.validate_cron_expression",
        lambda cron_expr: True,
    )

    result = asyncio.run(handler.async_step_init({CONF_CRON_EXPRESSION: "0 6 * * *"}))

    assert result == {
        "type": "create_entry",
        "title": "",
        "data": {
            CONF_CRON_EXPRESSION: "0 6 * * *",
            CONF_PRICE_STALE_HOURS: DEFAULT_PRICE_STALE_HOURS,
        },
    }


def test_options_flow_invalid_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _make_options_flow()
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.validate_cron_expression",
        lambda cron_expr: False,
    )

    result = asyncio.run(handler.async_step_init({CONF_CRON_EXPRESSION: "bad"}))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_cron_expression"}


@pytest.mark.parametrize("stale_hours", [3, "bad"])
def test_options_flow_invalid_stale_threshold(stale_hours: Any) -> None:
    handler = _make_options_flow()

    result = asyncio.run(
        handler.async_step_init(
            {
                CONF_CRON_EXPRESSION: DEFAULT_CRON_EXPRESSION,
                CONF_PRICE_STALE_HOURS: stale_hours,
            }
        )
    )

    assert result["errors"] == {"base": "invalid_stale_hours"}


def test_options_flow_keeps_supported_stale_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _make_options_flow(
        {
            CONF_CRON_EXPRESSION: "0 6 * * *",
            CONF_PRICE_STALE_HOURS: 48,
        }
    )
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.validate_cron_expression",
        lambda cron_expr: True,
    )

    result = asyncio.run(
        handler.async_step_init(
            {
                CONF_CRON_EXPRESSION: "0 6 * * *",
                CONF_PRICE_STALE_HOURS: 48,
            }
        )
    )

    assert result["data"][CONF_PRICE_STALE_HOURS] == 48


def test_options_flow_handles_unavailable_cron_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _make_options_flow()
    monkeypatch.setattr(
        "custom_components.osservaprezzi_carburanti.config_flow.get_next_run_time",
        MagicMock(side_effect=ValueError("bad")),
    )

    result = asyncio.run(handler.async_step_init())

    assert result["description_placeholders"] == {"next_run": "—"}
