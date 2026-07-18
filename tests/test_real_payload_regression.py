"""Regression tests using saved real MIMIT/Osservaprezzi payload snapshots."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti import coordinator as coordinator_module
from custom_components.osservaprezzi_carburanti.api import normalize_station_data  # noqa: E402
from custom_components.osservaprezzi_carburanti.coordinator import (  # noqa: E402
    CarburantiDataUpdateCoordinator,
)
from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_coordinator() -> CarburantiDataUpdateCoordinator:
    """Create a coordinator instance without invoking Home Assistant base classes."""
    coordinator = object.__new__(CarburantiDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.csv_manager = MagicMock()
    coordinator.data = None
    coordinator._csv_update_listener = None
    return coordinator


@pytest.fixture(autouse=True)
def deterministic_datetime_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep real payload processing assertions stable."""

    def parse_datetime(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    monkeypatch.setattr(coordinator_module.dt_util, "parse_datetime", parse_datetime)
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "now",
        lambda: datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )


def test_real_mimit_csv_snapshot_maps_expected_station_fields() -> None:
    hass = MagicMock()
    hass.config.path.return_value = "/tmp/test_storage"
    csv_manager = CSVStationManager(hass)

    content = (FIXTURES_DIR / "mimit_anagrafica_sample.csv").read_text(encoding="utf-8")
    success, separator, stations = csv_manager._parse_csv_content_to_cache(content)

    assert success is True
    assert separator == "|"
    assert stations["54233"] == {
        "id": "54233",
        "operator": "UNION GESTIONI SOCIETA' A RESPONSABILITA' LIMITATA",
        "brand": "Pompe Bianche",
        "station_type": "Stradale",
        "name": "UNION - BORGHESANO LUCCHESE",
        "address": "BORGHESANO LUCCHESE 2 00146",
        "municipality": "ROMA",
        "province": "RM",
        "latitude": 41.8947,
        "longitude": 12.49348,
    }


def test_real_station_payload_snapshot_maps_expected_coordinator_output() -> None:
    station_payload = json.loads(
        (FIXTURES_DIR / "mimit_station_54233.json").read_text(encoding="utf-8")
    )
    station_payload = normalize_station_data(station_payload, "54233")
    coordinator = _make_coordinator()
    coordinator.csv_manager.get_station_by_id.return_value = {
        "operator": "UNION GESTIONI SOCIETA' A RESPONSABILITA' LIMITATA",
        "station_type": "Stradale",
        "municipality": "ROMA",
        "province": "RM",
        "latitude": 41.8947,
        "longitude": 12.49348,
    }

    processed = coordinator._process_station_data(station_payload)

    assert processed["station_info"]["id"] == 54233
    assert processed["station_info"]["name"] == "UNION - BORGHESANO LUCCHESE"
    assert processed["station_info"]["latitude"] == 41.8947
    assert processed["station_info"]["longitude"] == 12.49348
    assert processed["station_info"]["operator"] == (
        "UNION GESTIONI SOCIETA' A RESPONSABILITA' LIMITATA"
    )
    assert processed["fuels"]["Benzina_self"] == {
        "price": 1.798,
        "last_update": "2026-06-06T18:15:30+00:00",
        "validity_date": "2026-06-06T18:24:29+00:00",
        "fuel_id": 1,
        "is_self": True,
        "service_area_id": 54233,
        "previous_price": None,
        "price_changed_at": None,
    }
    assert processed["fuels"]["Gasolio_self"]["price"] == 1.898
    assert processed["opening_hours"][0]["flagChiusura"] is True
