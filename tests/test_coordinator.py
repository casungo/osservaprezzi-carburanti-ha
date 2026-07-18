"""Tests for coordinator retry helpers."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.coordinator import (
    CarburantiDataUpdateCoordinator,
)
from custom_components.osservaprezzi_carburanti import coordinator as coordinator_module
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID


def _make_coordinator() -> CarburantiDataUpdateCoordinator:
    """Create a coordinator instance without invoking Home Assistant base classes."""
    coordinator = object.__new__(CarburantiDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.config_entry = MagicMock(data={CONF_STATION_ID: "123"})
    coordinator.csv_manager = MagicMock()
    coordinator.data = None
    coordinator._csv_update_listener = None
    return coordinator


@pytest.fixture(autouse=True)
def real_datetime_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace mocked Home Assistant datetime helpers with deterministic functions."""

    def parse_datetime(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    monkeypatch.setattr(coordinator_module.dt_util, "parse_datetime", parse_datetime)
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "now",
        lambda: datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc),
    )


def _make_response_error(
    status: int,
    headers: dict[str, str] | None = None,
) -> aiohttp.ClientResponseError:
    """Create a minimal response error for tests."""
    return aiohttp.ClientResponseError(
        request_info=cast(Any, SimpleNamespace(real_url="https://example.test")),
        history=(),
        status=status,
        message="test",
        headers=cast(Any, headers),
    )


class TestRetryDelay:
    def test_uses_retry_after_header(self) -> None:
        err = _make_response_error(429, {"Retry-After": "15"})
        assert CarburantiDataUpdateCoordinator._get_retry_delay(err, 30) == 15

    def test_ignores_invalid_retry_after_header(self) -> None:
        err = _make_response_error(429, {"Retry-After": "invalid"})
        assert CarburantiDataUpdateCoordinator._get_retry_delay(err, 30) == 30

    def test_ignores_non_positive_retry_after_header(self) -> None:
        err = _make_response_error(429, {"Retry-After": "0"})
        assert CarburantiDataUpdateCoordinator._get_retry_delay(err, 30) == 30


class TestTransientErrors:
    def test_timeout_is_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(asyncio.TimeoutError()) is True

    def test_404_is_not_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(_make_response_error(404)) is False

    def test_429_is_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(_make_response_error(429)) is True

    def test_client_error_is_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(aiohttp.ClientError()) is True

    def test_none_is_not_transient(self) -> None:
        assert CarburantiDataUpdateCoordinator._is_transient_error(None) is False


class TestStationProcessing:
    def test_get_station_coordinates_from_csv(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.get_station_by_id.return_value = {
            "latitude": "45.1",
            "longitude": "9.2",
        }

        result = coordinator._get_station_coordinates("123")

        assert result == {"latitude": 45.1, "longitude": 9.2, "source": "csv"}

    @pytest.mark.parametrize(
        ("station_id", "csv_station"),
        [
            (None, {"latitude": "45.1", "longitude": "9.2"}),
            ("123", None),
            ("123", {"latitude": None, "longitude": "9.2"}),
            ("123", {"latitude": "45.1", "longitude": None}),
        ],
    )
    def test_get_station_coordinates_missing_data(
        self,
        station_id: str | None,
        csv_station: dict[str, Any] | None,
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.get_station_by_id.return_value = csv_station

        assert coordinator._get_station_coordinates(station_id) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("2025-03-01T10:11:12.123456+00:00", "2025-03-01T10:11:12+00:00"),
            ("2025-03-01T10:11:12Z", "2025-03-01T10:11:12+00:00"),
            ("not-a-date", "not-a-date"),
        ],
    )
    def test_parse_iso_datetime(self, value: str | None, expected: str | None) -> None:
        coordinator = _make_coordinator()

        assert coordinator._parse_iso_datetime(value) == expected

    def test_process_station_data_preserves_price_change_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.get_station_by_id.return_value = None
        times = iter(
            datetime(2025, 3, day, 12, 0, tzinfo=timezone.utc) for day in range(1, 5)
        )
        monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: next(times))

        def payload(price: float, *, include_gasolio: bool = False) -> dict[str, Any]:
            fuels = [{"name": "Benzina", "isSelf": True, "price": price}]
            if include_gasolio:
                fuels.append({"name": "Gasolio", "isSelf": True, "price": 1.6})
            return {"id": 123, "fuels": fuels}

        coordinator.data = coordinator._process_station_data(payload(1.7))
        assert coordinator.data["fuels"]["Benzina_self"]["previous_price"] is None
        assert coordinator.data["fuels"]["Benzina_self"]["price_changed_at"] is None

        coordinator.data = coordinator._process_station_data(payload(1.8))
        changed_at = "2025-03-02T12:00:00+00:00"
        assert coordinator.data["fuels"]["Benzina_self"]["previous_price"] == 1.7
        assert coordinator.data["fuels"]["Benzina_self"]["price_changed_at"] == changed_at

        coordinator.data = coordinator._process_station_data(payload(1.8, include_gasolio=True))
        assert coordinator.data["fuels"]["Benzina_self"]["previous_price"] == 1.7
        assert coordinator.data["fuels"]["Benzina_self"]["price_changed_at"] == changed_at
        assert coordinator.data["fuels"]["Gasolio_self"]["previous_price"] is None
        assert coordinator.data["fuels"]["Gasolio_self"]["price_changed_at"] is None

        coordinator.data = coordinator._process_station_data(payload(1.9))
        assert coordinator.data["fuels"]["Benzina_self"] == {
            "price": 1.9,
            "last_update": None,
            "validity_date": None,
            "fuel_id": None,
            "is_self": True,
            "service_area_id": None,
            "previous_price": 1.8,
            "price_changed_at": "2025-03-04T12:00:00+00:00",
        }

    def test_process_station_data_enriches_station_and_fuels(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.get_station_by_id.return_value = {
            "latitude": "45.1",
            "longitude": "9.2",
            "operator": "Operator",
            "station_type": "stradale",
            "municipality": "Milano",
            "province": "MI",
        }
        coordinator.data = {
            "fuels": {
                "Benzina_self": {
                    "price": 1.7,
                    "previous_price": None,
                    "price_changed_at": None,
                }
            }
        }
        payload = {
            "id": 123,
            "name": "Station",
            "nomeImpianto": "Plant",
            "address": "Street",
            "brand": "Brand",
            "company": "Company",
            "phoneNumber": "123",
            "email": "a@example.test",
            "website": "https://example.test",
            "services": [{"name": "bar"}],
            "orariapertura": [{"giorno": "Lunedi"}],
            "fuels": [
                {
                    "name": "Benzina",
                    "isSelf": True,
                    "price": 1.8,
                    "insertDate": "2025-03-01T10:11:12Z",
                    "validityDate": "2025-03-02T00:00:00Z",
                    "fuelId": 1,
                    "serviceAreaId": 55,
                },
                {"price": 2.0},
            ],
        }

        result = coordinator._process_station_data(payload)

        assert result["station_info"] == {
            "id": 123,
            "name": "Station",
            "nomeImpianto": "Plant",
            "address": "Street",
            "brand": "Brand",
            "company": "Company",
            "phoneNumber": "123",
            "email": "a@example.test",
            "website": "https://example.test",
            "latitude": 45.1,
            "longitude": 9.2,
            "operator": "Operator",
            "station_type": "stradale",
            "municipality": "Milano",
            "province": "MI",
            "coordinate_source": "csv",
        }
        assert result["services"] == [{"name": "bar"}]
        assert result["opening_hours"] == [{"giorno": "Lunedi"}]
        assert result["fuels"]["Benzina_self"]["previous_price"] == 1.7
        assert result["fuels"]["Benzina_self"]["price_changed_at"] == result["last_update"]
        assert result["fuels"]["Benzina_self"]["last_update"] == "2025-03-01T10:11:12+00:00"
        assert result["fuels"]["Benzina_self"]["validity_date"] == "2025-03-02T00:00:00+00:00"
        assert result["fuels"]["Unknown_servito"]["price"] == 2.0


class TestCoordinatorUpdates:
    def test_constructor_sets_up_csv_manager_and_schedule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        csv_manager = MagicMock()
        listener = MagicMock()
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.CSVStationManager",
            MagicMock(return_value=csv_manager),
        )
        track_mock = MagicMock(return_value=listener)
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.async_track_time_interval",
            track_mock,
        )
        hass = MagicMock()
        entry = MagicMock(unique_id=None, entry_id="entry_1")

        coordinator = CarburantiDataUpdateCoordinator(hass, entry)

        assert coordinator.config_entry is entry
        assert coordinator.csv_manager is csv_manager
        assert coordinator._csv_update_listener is listener
        track_mock.assert_called_once()

    def test_async_update_data_initializes_csv_and_fetches_station(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.is_data_available.return_value = False
        coordinator.csv_manager.async_initialize = AsyncMock(return_value=True)
        coordinator._async_fetch_station_data = AsyncMock(return_value={"ok": True})

        result = asyncio.run(coordinator._async_update_data())

        assert result == {"ok": True}
        coordinator.csv_manager.async_initialize.assert_awaited_once()
        coordinator._async_fetch_station_data.assert_awaited_once()

    def test_async_update_data_continues_when_csv_initialization_fails(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.is_data_available.return_value = False
        coordinator.csv_manager.async_initialize = AsyncMock(return_value=False)
        coordinator._async_fetch_station_data = AsyncMock(return_value={"ok": True})

        assert asyncio.run(coordinator._async_update_data()) == {"ok": True}

    def test_async_fetch_station_data_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = _make_coordinator()
        processed = {"station_info": {"id": "123"}}
        coordinator._process_station_data = MagicMock(return_value=processed)
        fetch_mock = AsyncMock(return_value={"id": "123"})
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
            fetch_mock,
        )

        result = asyncio.run(coordinator._async_fetch_station_data())

        assert result == processed
        fetch_mock.assert_awaited_once_with(coordinator.hass, "123")
        coordinator._process_station_data.assert_called_once_with({"id": "123"})

    def test_async_fetch_station_data_404_raises_update_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        coordinator = _make_coordinator()
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
            AsyncMock(side_effect=_make_response_error(404)),
        )

        with pytest.raises(Exception, match="not found"):
            asyncio.run(coordinator._async_fetch_station_data())

    def test_async_fetch_station_data_retries_response_error_then_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        coordinator = _make_coordinator()
        coordinator._process_station_data = MagicMock(return_value={"ok": True})
        fetch_mock = AsyncMock(side_effect=[_make_response_error(500), {"id": "123"}])
        sleep_mock = AsyncMock()
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
            fetch_mock,
        )
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.asyncio.sleep",
            sleep_mock,
        )

        assert asyncio.run(coordinator._async_fetch_station_data()) == {"ok": True}
        assert fetch_mock.await_count == 2
        sleep_mock.assert_awaited_once()

    def test_async_fetch_station_data_retries_then_keeps_last_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        coordinator = _make_coordinator()
        coordinator.data = {"last": "known"}
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
            AsyncMock(side_effect=aiohttp.ClientError("temporary")),
        )
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.asyncio.sleep",
            AsyncMock(),
        )

        result = asyncio.run(coordinator._async_fetch_station_data())

        assert result == {"last": "known"}

    def test_async_fetch_station_data_exhausts_retries(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        coordinator = _make_coordinator()
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.fetch_station_data",
            AsyncMock(side_effect=asyncio.TimeoutError()),
        )
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.coordinator.asyncio.sleep",
            AsyncMock(),
        )

        with pytest.raises(Exception, match="Error fetching station data"):
            asyncio.run(coordinator._async_fetch_station_data())

    def test_async_csv_update_callback(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.async_periodic_update = AsyncMock(return_value=True)

        asyncio.run(coordinator._async_csv_update_callback(MagicMock()))

        coordinator.csv_manager.async_periodic_update.assert_awaited_once()

    def test_async_csv_update_callback_logs_failure(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.async_periodic_update = AsyncMock(return_value=False)

        asyncio.run(coordinator._async_csv_update_callback(MagicMock()))

        coordinator.csv_manager.async_periodic_update.assert_awaited_once()

    def test_async_force_csv_update_propagates_success(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.async_update_csv_data = AsyncMock(return_value=True)

        assert asyncio.run(coordinator.async_force_csv_update()) is True
        coordinator.csv_manager.async_update_csv_data.assert_awaited_once_with(force_update=True)

    def test_async_force_csv_update_propagates_failure(self) -> None:
        coordinator = _make_coordinator()
        coordinator.csv_manager.async_update_csv_data = AsyncMock(return_value=False)

        assert asyncio.run(coordinator.async_force_csv_update()) is False

    def test_async_shutdown_calls_listener_and_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        coordinator = _make_coordinator()
        listener = MagicMock()
        coordinator._csv_update_listener = listener
        shutdown_mock = AsyncMock()
        monkeypatch.setattr(
            CarburantiDataUpdateCoordinator.__mro__[1],
            "async_shutdown",
            shutdown_mock,
            raising=False,
        )

        asyncio.run(coordinator.async_shutdown())

        listener.assert_called_once()
        assert coordinator._csv_update_listener is None
