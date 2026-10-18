"""Branch tests for service coordinator filtering."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.osservaprezzi_carburanti import services as services_module
from custom_components.osservaprezzi_carburanti.const import DOMAIN, SERVICE_COMPARE_STATIONS


class _Coordinator:
    """Small accepted coordinator shape for service handler tests."""

    data = {
        "station_info": {"id": "123", "name": "Valid station"},
        "fuels": {},
    }


class _ServiceRegistry:
    """Capture registered service callbacks."""

    def __init__(self) -> None:
        self.handlers = {}

    def async_register(self, domain, service, handler, **kwargs) -> None:
        self.handlers[(domain, service)] = handler


def test_compare_stations_skips_invalid_coordinator_entries(monkeypatch) -> None:
    monkeypatch.setattr(services_module, "CarburantiDataUpdateCoordinator", _Coordinator)
    service_registry = _ServiceRegistry()
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "valid": {"coordinator": _Coordinator()},
                "invalid": {"coordinator": object()},
                "not_entry_data": object(),
            }
        },
        services=service_registry,
    )

    services_module.async_register_services(hass)
    handler = service_registry.handlers[(DOMAIN, SERVICE_COMPARE_STATIONS)]
    result = asyncio.run(handler(SimpleNamespace(data={})))

    assert result["stations"] == {
        "valid": {
            "station_name": "Valid station",
            "station_id": "123",
            "brand": None,
            "address": None,
            "fuels": {},
        }
    }
