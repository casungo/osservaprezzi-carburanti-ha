"""Branch tests for service coordinator filtering."""

from __future__ import annotations

import asyncio
from datetime import UTC
from types import SimpleNamespace

from custom_components.osservaprezzi_carburanti import services as services_module
from custom_components.osservaprezzi_carburanti.const import DOMAIN, SERVICE_COMPARE_STATIONS


class _Coordinator:
    """Small accepted coordinator shape for service handler tests."""

    data = {  # noqa: RUF012
        "station_info": {"id": "123", "name": "Valid station"},
        "fuels": {},
    }


class _ServiceRegistry:
    """Capture registered service callbacks."""

    def __init__(self) -> None:
        self.handlers = {}

    def async_register(self, domain, service, handler, **kwargs) -> None:
        self.handlers[(domain, service)] = handler


def _add_hub(hass):
    stations = {
        key: SimpleNamespace(coordinator=value["coordinator"])
        for key, value in hass.data.get(DOMAIN, {}).items()
        if isinstance(value, dict)
        and "coordinator" in value
        and (hasattr(value["coordinator"], "data") or hasattr(value["coordinator"], "config_entry"))
    }
    entry = SimpleNamespace(
        state=services_module.ConfigEntryState.LOADED,
        runtime_data=SimpleNamespace(stations=stations),
    )
    hass.config_entries = SimpleNamespace(async_entries=lambda domain: [entry])


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

    _add_hub(hass)
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


def test_refresh_prices_with_blank_station_ids_raises_error() -> None:
    import pytest

    service_registry = _ServiceRegistry()
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry_1": {
                    "coordinator": SimpleNamespace(
                        config_entry=SimpleNamespace(data={"station_id": "123"})
                    )
                }
            }
        },
        services=service_registry,
    )
    _add_hub(hass)
    services_module.async_register_services(hass)
    handler = service_registry.handlers[(DOMAIN, services_module.SERVICE_REFRESH_PRICES)]
    with pytest.raises(services_module.ServiceValidationError) as error:
        asyncio.run(handler(SimpleNamespace(data={"station_ids": ["  "]})))
    assert error.value.translation_key == "no_matching_entries"
    assert error.value.translation_domain == DOMAIN


def test_search_registry_nearby_and_invalid_origins(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import pytest

    registry = _ServiceRegistry()
    snapshot = SimpleNamespace(stations=(), updated_at=None, is_stale=False)
    manager = SimpleNamespace(async_ensure_registry=AsyncMock(return_value=snapshot))
    monkeypatch.setattr(services_module, "get_shared_csv_manager", lambda hass: manager)
    hass = SimpleNamespace(
        data={},
        services=registry,
        states=SimpleNamespace(
            get=lambda zone: (
                None
                if zone == "zone.missing"
                else SimpleNamespace(attributes={"latitude": 41.9, "longitude": 12.5})
            )
        ),
        async_add_executor_job=AsyncMock(side_effect=lambda function: function()),
    )
    _add_hub(hass)
    services_module.async_register_services(hass)
    handler = registry.handlers[(DOMAIN, services_module.SERVICE_SEARCH_REGISTRY)]
    for data in ({"latitude": 41.9, "longitude": 12.5}, {"zone": "zone.home"}):
        assert asyncio.run(handler(SimpleNamespace(data=data)))["results"] == []
    for data in ({"zone": "zone.missing"}, {"latitude": 41.9}, {"zone": "zone.empty"}):
        if data.get("zone") == "zone.empty":
            hass.states.get = lambda zone: SimpleNamespace(attributes={})
        with pytest.raises(services_module.ServiceValidationError) as error:
            asyncio.run(handler(SimpleNamespace(data=data)))
        assert error.value.translation_domain == DOMAIN
        assert error.value.translation_key == (
            "zone_not_found" if data.get("zone") == "zone.missing" else "missing_coordinates"
        )


def test_compare_stations_ranking_filters_open_distance_and_liters(monkeypatch) -> None:
    from datetime import datetime

    from custom_components.osservaprezzi_carburanti import price_metadata as price_module

    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(services_module.dt_util, "now", lambda: now)
    monkeypatch.setattr(price_module.dt_util, "now", lambda: now)
    monkeypatch.setattr(price_module.dt_util, "parse_datetime", datetime.fromisoformat)

    class _FakeCoord:
        def __init__(self, data):
            self.data = data
            self.config_entry = SimpleNamespace(options={"price_stale_hours": 24})

    coord_open = _FakeCoord(
        {
            "station_info": {
                "id": "1",
                "name": "Near Open",
                "brand": "Brand A",
                "address": "Via 1",
                "latitude": 41.90,
                "longitude": 12.50,
            },
            "opening_hours": [
                {
                    "giornoSettimanaId": 1,
                    "flagOrarioContinuato": True,
                    "oraAperturaOrarioContinuato": "08:00",
                    "oraChiusuraOrarioContinuato": "20:00",
                }
            ],
            "fuels": {
                "Benzina_self": {
                    "price": 1.80,
                    "is_self": True,
                    "last_update": "2026-06-01T10:00:00+00:00",
                },
                "Benzina_servito": {
                    "price": 2.00,
                    "is_self": False,
                    "last_update": "2026-06-01T10:00:00+00:00",
                },
                "Gasolio_self": {
                    "price": 1.65,
                    "is_self": True,
                    "last_update": "2026-05-01T10:00:00+00:00",
                },
                "Bad_self": {"price": None, "is_self": True},
            },
        }
    )
    coord_closed = _FakeCoord(
        {
            "station_info": {
                "id": "2",
                "name": "Far Closed",
                "brand": "Brand B",
                "address": "Via 2",
            },
            "opening_hours": [{"giornoSettimanaId": 1, "flagChiusura": True}],
            "fuels": {
                "Benzina_self": {
                    "price": 1.70,
                    "is_self": True,
                    "last_update": "2026-06-01T11:00:00+00:00",
                },
            },
        }
    )
    coord_no_hours = _FakeCoord(
        {
            "station_info": {"id": "3", "name": "No Hours"},
            "opening_hours": [],
            "fuels": {},
        }
    )

    monkeypatch.setattr(services_module, "CarburantiDataUpdateCoordinator", _FakeCoord)
    registry = _ServiceRegistry()
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "e1": {"coordinator": coord_open},
                "e2": {"coordinator": coord_closed},
                "e3": {"coordinator": coord_no_hours},
            }
        },
        services=registry,
        states=SimpleNamespace(get=lambda _z: None),
    )
    _add_hub(hass)
    services_module.async_register_services(hass)
    handler = registry.handlers[(DOMAIN, SERVICE_COMPARE_STATIONS)]

    # Default ranking sorted by price + liters cost/savings
    res = asyncio.run(
        handler(
            SimpleNamespace(data={"fuel_type": "benzina", "service_mode": "self", "liters": 50})
        )
    )
    assert [item["station_id"] for item in res["ranking"]] == ["2", "1"]
    assert res["ranking"][0]["estimated_total_eur"] == 85.0
    assert res["ranking"][0]["savings_eur"] == 5.0

    # Served filter + only_open + exclude_stale
    res_served = asyncio.run(
        handler(
            SimpleNamespace(
                data={"service_mode": "served", "only_open": True, "exclude_stale": True}
            )
        )
    )
    assert [item["fuel_key"] for item in res_served["ranking"]] == ["Benzina_servito"]

    # Exclude stale removes old Gasolio_self
    res_fresh = asyncio.run(
        handler(
            SimpleNamespace(data={"service_mode": "self", "exclude_stale": True, "only_open": True})
        )
    )
    assert [item["fuel_key"] for item in res_fresh["ranking"]] == ["Benzina_self"]

    # Sort by distance with origin coordinates (station without coords sorts last)
    res_dist = asyncio.run(
        handler(
            SimpleNamespace(
                data={
                    "fuel_type": "benzina",
                    "service_mode": "self",
                    "sort_by": "distance",
                    "latitude": 41.9,
                    "longitude": 12.5,
                }
            )
        )
    )
    assert [item["station_id"] for item in res_dist["ranking"]] == ["1", "2"]
    assert res_dist["ranking"][0]["distance_km"] == 0.0
    assert "distance_km" not in res_dist["ranking"][1]


def test_refresh_prices_supports_device_and_entity_targets(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    class _RefreshCoord:
        def __init__(self, station_id: str) -> None:
            self.config_entry = SimpleNamespace(data={"station_id": station_id})
            self.last_update_success = True
            self.last_refresh_from_cache = False
            self.async_request_manual_refresh = AsyncMock()

    c1 = _RefreshCoord("101")
    c2 = _RefreshCoord("202")
    monkeypatch.setattr(services_module, "CarburantiDataUpdateCoordinator", _RefreshCoord)
    monkeypatch.setattr(
        services_module.dr,
        "async_get",
        lambda _hass: SimpleNamespace(
            async_get=lambda dev_id: (
                SimpleNamespace(identifiers={(DOMAIN, "101")}) if dev_id == "dev1" else None
            )
        ),
    )
    monkeypatch.setattr(
        services_module.er,
        "async_get",
        lambda _hass: SimpleNamespace(
            async_get=lambda ent_id: (
                SimpleNamespace(config_subentry_id="e2") if ent_id == "sensor.s2" else None
            )
        ),
    )

    registry = _ServiceRegistry()
    hass = SimpleNamespace(
        data={DOMAIN: {"e1": {"coordinator": c1}, "e2": {"coordinator": c2}}},
        services=registry,
    )
    _add_hub(hass)
    services_module.async_register_services(hass)
    handler = registry.handlers[(DOMAIN, services_module.SERVICE_REFRESH_PRICES)]

    result = asyncio.run(
        handler(
            SimpleNamespace(
                data={
                    "device_id": ["dev1", "missing_dev"],
                    "entity_id": ["sensor.s2", "sensor.missing"],
                }
            )
        )
    )
    assert set(result["refreshed_station_ids"]) == {"101", "202"}
    c1.async_request_manual_refresh.assert_awaited_once()
    c2.async_request_manual_refresh.assert_awaited_once()


def test_services_ignore_unloaded_and_missing_runtime_entries():
    registry = _ServiceRegistry()
    unloaded = SimpleNamespace(state=object())
    no_runtime = SimpleNamespace(state=services_module.ConfigEntryState.LOADED)
    hass = SimpleNamespace(
        data={},
        services=registry,
        config_entries=SimpleNamespace(async_entries=lambda domain: [unloaded, no_runtime]),
    )
    services_module.async_register_services(hass)
    handler = registry.handlers[(DOMAIN, SERVICE_COMPARE_STATIONS)]
    assert asyncio.run(handler(SimpleNamespace(data={}))) == {"stations": {}, "ranking": []}
