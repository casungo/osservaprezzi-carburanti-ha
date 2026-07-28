"""Tests for privacy-safe diagnostics."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.osservaprezzi_carburanti.const import (
    CONF_CRON_EXPRESSION,
    CONF_STATION_ID,
    DOMAIN,
)
from custom_components.osservaprezzi_carburanti import diagnostics


class _Coordinator:
    """Minimal coordinator accepted by diagnostics."""

    def __init__(self) -> None:
        self.data = {
            "station_info": {
                "name": "Private station",
                "address": "Private address",
                "latitude": 41.9,
                "longitude": 12.5,
            },
            "fuels": {"benzina_self": {"price": 1.8}},
            "services": [{"id": 1}],
            "opening_hours": [{"giornoSettimanaId": 1}],
        }
        self.last_update_success = True
        self.csv_manager = SimpleNamespace(
            registry_status=lambda: {
                "initialized": True,
                "station_count": 100,
                "is_stale": False,
            }
        )


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="entry-1",
        data={CONF_STATION_ID: "12345"},
        options={CONF_CRON_EXPRESSION: "0 6 * * *"},
    )


def test_diagnostics_redacts_station_and_omits_location(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics, "CarburantiDataUpdateCoordinator", _Coordinator)
    coordinator = _Coordinator()
    hass = SimpleNamespace(
        data={DOMAIN: {"entry-1": {"coordinator": coordinator}}}
    )

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(hass, _entry())
    )

    assert result["entry"]["data"][CONF_STATION_ID] == "**REDACTED**"
    assert result["loaded"] is True
    assert result["coordinator"] == {
        "last_update_success": True,
        "has_data": True,
        "fuel_count": 1,
        "service_count": 1,
        "opening_hours_count": 1,
    }
    assert result["registry"]["station_count"] == 100
    assert "station_info" not in result
    assert "latitude" not in str(result)
    assert "Private" not in str(result)


def test_diagnostics_handles_unloaded_entry() -> None:
    hass = SimpleNamespace(data={})

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(hass, _entry())
    )

    assert result == {
        "entry": {
            "data": {CONF_STATION_ID: "**REDACTED**"},
            "options": {CONF_CRON_EXPRESSION: "0 6 * * *"},
        },
        "loaded": False,
    }


def test_diagnostics_handles_unexpected_payload_shapes(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics, "CarburantiDataUpdateCoordinator", _Coordinator)
    coordinator = _Coordinator()
    coordinator.data = {
        "fuels": [],
        "services": {},
        "opening_hours": {},
    }
    hass = SimpleNamespace(
        data={DOMAIN: {"entry-1": {"coordinator": coordinator}}}
    )

    result = asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(hass, _entry())
    )

    assert result["coordinator"]["fuel_count"] == 0
    assert result["coordinator"]["service_count"] == 0
    assert result["coordinator"]["opening_hours_count"] == 0
