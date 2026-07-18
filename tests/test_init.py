"""Tests for integration setup helpers."""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


init_module = importlib.import_module("custom_components.osservaprezzi_carburanti")


class FakeCSVManager:
    """Small async fake for service handler tests."""

    def __init__(
        self,
        load_result: bool = True,
        initialize_result: bool = True,
        clear_result: bool = True,
    ) -> None:
        """Initialize counters."""
        self.clear_calls = 0
        self.initialize_calls = 0
        self.load_calls = 0
        self.load_result = load_result
        self.initialize_result = initialize_result
        self.clear_result = clear_result

    async def async_clear_cache(self) -> bool:
        """Track cache clear calls."""
        self.clear_calls += 1
        return self.clear_result

    async def async_initialize(self) -> bool:
        """Track initialize calls."""
        self.initialize_calls += 1
        return self.initialize_result

    async def async_load_cached_data(self) -> bool:
        """Track cache load calls."""
        self.load_calls += 1
        return self.load_result


class FakeCoordinator:
    """Small coordinator fake for service handler tests."""

    def __init__(
        self,
        *args,
        update_result: bool = True,
        load_result: bool = True,
        initialize_result: bool = True,
        clear_result: bool = True,
    ) -> None:
        """Initialize counters."""
        self.csv_manager = args[2] if len(args) > 2 else FakeCSVManager(
            load_result, initialize_result, clear_result
        )
        self.force_update_calls = 0
        self.refresh_calls = 0
        self.first_refresh_calls = 0
        self.shutdown_calls = 0
        self.update_result = update_result
        self.data = {}
        self.raise_first_refresh = False

    async def async_config_entry_first_refresh(self) -> None:
        """Track first refresh calls."""
        self.first_refresh_calls += 1
        if self.raise_first_refresh:
            raise init_module.ConfigEntryNotReady("not ready")

    async def async_shutdown(self) -> None:
        """Track shutdown calls."""
        self.shutdown_calls += 1

    async def async_force_csv_update(self) -> bool:
        """Track forced CSV updates."""
        self.force_update_calls += 1
        return self.update_result

    async def async_request_refresh(self) -> None:
        """Track coordinator refresh calls."""
        self.refresh_calls += 1


class FakeEntityRegistry:
    """Small entity registry fake for migration tests."""

    def __init__(self, entities: dict[str, SimpleNamespace]) -> None:
        """Store entities and track updates/removals."""
        self.entities = entities
        self.updated: list[tuple[str, dict[str, object]]] = []
        self.removed: list[str] = []

    def async_update_entity(self, entity_id: str, **kwargs) -> None:
        """Track registry updates."""
        self.updated.append((entity_id, kwargs))

    def async_remove(self, entity_id: str) -> None:
        """Track registry removals."""
        self.removed.append(entity_id)


def _build_hass_with_services() -> tuple[MagicMock, dict[str, object]]:
    hass = MagicMock()
    registered_services: dict[str, object] = {}

    def _register(domain, service, handler, **kwargs):
        registered_services[service] = handler

    hass.services.async_register.side_effect = _register
    return hass, registered_services


def test_force_csv_update_service_updates_shared_csv_once(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    second = FakeCoordinator()
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_FORCE_CSV_UPDATE](SimpleNamespace()))

    assert first.force_update_calls == 1
    assert second.force_update_calls == 0
    assert second.csv_manager.load_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_force_csv_update_service_does_not_refresh_when_shared_update_fails(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator(update_result=False)
    second = FakeCoordinator()
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_FORCE_CSV_UPDATE](SimpleNamespace()))

    assert first.force_update_calls == 1
    assert second.force_update_calls == 0
    assert second.csv_manager.load_calls == 0
    assert first.refresh_calls == 0
    assert second.refresh_calls == 0


def test_force_csv_update_service_does_not_reload_shared_manager(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    second = FakeCoordinator(load_result=False)
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_FORCE_CSV_UPDATE](SimpleNamespace()))

    assert second.csv_manager.load_calls == 0
    assert second.csv_manager.initialize_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_force_csv_update_service_refreshes_all_with_shared_manager(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    second = FakeCoordinator(load_result=False, initialize_result=False)
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_FORCE_CSV_UPDATE](SimpleNamespace()))

    assert second.csv_manager.load_calls == 0
    assert second.csv_manager.initialize_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_clear_cache_service_clears_shared_csv_once(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    second = FakeCoordinator()
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_CLEAR_CACHE](SimpleNamespace()))

    assert first.csv_manager.clear_calls == 1
    assert second.csv_manager.clear_calls == 0
    assert first.csv_manager.initialize_calls == 1
    assert second.csv_manager.load_calls == 0
    assert second.csv_manager.initialize_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_clear_cache_service_does_not_refresh_when_clear_fails(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator(clear_result=False)
    second = FakeCoordinator()
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_CLEAR_CACHE](SimpleNamespace()))

    assert first.csv_manager.clear_calls == 1
    assert first.csv_manager.initialize_calls == 0
    assert second.csv_manager.load_calls == 0
    assert first.refresh_calls == 0
    assert second.refresh_calls == 0


def test_clear_cache_service_does_not_refresh_when_primary_initialize_fails(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator(initialize_result=False)
    second = FakeCoordinator()
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_CLEAR_CACHE](SimpleNamespace()))

    assert first.csv_manager.clear_calls == 1
    assert first.csv_manager.initialize_calls == 1
    assert second.csv_manager.load_calls == 0
    assert first.refresh_calls == 0
    assert second.refresh_calls == 0


def test_clear_cache_service_does_not_reload_shared_manager(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    second = FakeCoordinator(load_result=False)
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_CLEAR_CACHE](SimpleNamespace()))

    assert first.csv_manager.clear_calls == 1
    assert second.csv_manager.clear_calls == 0
    assert second.csv_manager.load_calls == 0
    assert second.csv_manager.initialize_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_clear_cache_service_refreshes_all_with_shared_manager(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    second = FakeCoordinator(load_result=False, initialize_result=False)
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    asyncio.run(registered_services[init_module.SERVICE_CLEAR_CACHE](SimpleNamespace()))

    assert second.csv_manager.load_calls == 0
    assert second.csv_manager.initialize_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_async_setup_registers_global_services_without_entries() -> None:
    hass, registered_services = _build_hass_with_services()
    hass.data = {}

    result = asyncio.run(init_module.async_setup(hass, {}))

    assert result is True
    assert init_module.SERVICE_FORCE_CSV_UPDATE in registered_services
    assert init_module.SERVICE_CLEAR_CACHE in registered_services
    assert init_module.SERVICE_COMPARE_STATIONS in registered_services


def test_register_services_is_idempotent() -> None:
    hass, registered_services = _build_hass_with_services()
    hass.data = {init_module._SERVICES_REGISTERED: True}

    init_module._async_register_services(hass)

    assert registered_services == {}


def test_services_return_when_no_coordinators() -> None:
    hass, registered_services = _build_hass_with_services()
    hass.data = {init_module.DOMAIN: {"other": object()}}

    init_module._async_register_services(hass)

    asyncio.run(registered_services[init_module.SERVICE_FORCE_CSV_UPDATE](SimpleNamespace()))
    asyncio.run(registered_services[init_module.SERVICE_CLEAR_CACHE](SimpleNamespace()))
    result = asyncio.run(registered_services[init_module.SERVICE_COMPARE_STATIONS](SimpleNamespace()))

    assert result == {"stations": {}}


def test_compare_stations_service_returns_station_payload(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    hass, registered_services = _build_hass_with_services()
    first = FakeCoordinator()
    first.data = {
        "station_info": {
            "id": "123",
            "nomeImpianto": "Station Display",
            "brand": "Brand",
            "address": "Street",
        },
        "fuels": {
            "benzina_self": {
                "price": 1.8,
                "previous_price": 1.7,
                "price_changed_at": "2026-06-01T08:00:00+02:00",
                "is_self": True,
                "last_update": "2026-06-01T08:00:00+02:00",
            }
        },
    }
    second = FakeCoordinator()
    second.data = {}
    hass.data = {
        init_module.DOMAIN: {
            "entry_1": {"coordinator": first},
            "entry_2": {"coordinator": second},
        },
    }

    init_module._async_register_services(hass)
    result = asyncio.run(registered_services[init_module.SERVICE_COMPARE_STATIONS](SimpleNamespace()))

    assert result == {
        "stations": {
            "entry_1": {
                "station_name": "Station Display",
                "station_id": "123",
                "brand": "Brand",
                "address": "Street",
                "fuels": {
                    "benzina_self": {
                        "price": 1.8,
                        "previous_price": 1.7,
                        "price_changed_at": "2026-06-01T08:00:00+02:00",
                        "is_self": True,
                        "last_update": "2026-06-01T08:00:00+02:00",
                    }
                },
            }
        }
    }


def test_setup_entry_registers_services_after_last_entry_unload(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(init_module, "get_next_run_time", lambda cron: datetime(2026, 1, 1))
    monkeypatch.setattr(init_module, "async_track_point_in_utc_time", lambda *args: lambda: None)
    monkeypatch.setattr(init_module, "async_track_time_interval", lambda *args: lambda: None)
    monkeypatch.setattr(
        init_module.er,
        "async_get",
        lambda hass: FakeEntityRegistry({}),
    )

    hass, registered_services = _build_hass_with_services()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    entry = SimpleNamespace(
        entry_id="entry_1",
        title="Test Station",
        unique_id="station_1",
        options={},
        async_on_unload=MagicMock(),
        add_update_listener=MagicMock(return_value=lambda: None),
    )

    asyncio.run(init_module.async_setup_entry(hass, entry))

    assert init_module.SERVICE_FORCE_CSV_UPDATE in registered_services
    assert init_module.SERVICE_CLEAR_CACHE in registered_services
    assert init_module.SERVICE_COMPARE_STATIONS in registered_services


def test_two_entries_share_one_manager_and_registry_timer(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(init_module, "CSVStationManager", FakeCSVManager)
    monkeypatch.setattr(init_module, "get_next_run_time", lambda cron: datetime(2026, 1, 1))
    monkeypatch.setattr(init_module, "async_track_point_in_utc_time", lambda *args: lambda: None)
    registry_listener = MagicMock()
    track_registry = MagicMock(return_value=registry_listener)
    monkeypatch.setattr(init_module, "async_track_time_interval", track_registry)
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: FakeEntityRegistry({}))

    hass, _ = _build_hass_with_services()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    def make_entry(entry_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            entry_id=entry_id,
            title=entry_id,
            unique_id=entry_id,
            data={"station_id": entry_id},
            options={},
            async_on_unload=MagicMock(),
            add_update_listener=MagicMock(return_value=lambda: None),
        )

    first = make_entry("entry_1")
    second = make_entry("entry_2")
    assert asyncio.run(init_module.async_setup_entry(hass, first)) is True
    assert asyncio.run(init_module.async_setup_entry(hass, second)) is True

    domain_data = hass.data[init_module.DOMAIN]
    shared_manager = domain_data[init_module._CSV_MANAGER]
    assert domain_data["entry_1"]["coordinator"].csv_manager is shared_manager
    assert domain_data["entry_2"]["coordinator"].csv_manager is shared_manager
    track_registry.assert_called_once()
    shared_manager.async_periodic_update = AsyncMock(return_value=False)
    registry_callback = track_registry.call_args.args[1]
    asyncio.run(registry_callback(datetime(2026, 1, 1)))
    shared_manager.async_periodic_update.assert_awaited_once()

    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    assert asyncio.run(init_module.async_unload_entry(hass, second)) is True
    registry_listener.assert_not_called()
    assert domain_data[init_module._CSV_MANAGER] is shared_manager

    assert asyncio.run(init_module.async_unload_entry(hass, first)) is True
    registry_listener.assert_called_once()
    assert init_module._CSV_MANAGER not in domain_data
    assert init_module._CSV_UPDATE_LISTENER not in domain_data


def test_cleanup_legacy_entity_registry_clears_old_default_name(monkeypatch) -> None:
    registry = FakeEntityRegistry(
        {
            "sensor.station_brand": SimpleNamespace(
                entity_id="sensor.station_brand",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="123_brand",
                name="Brand",
            )
        }
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: registry)
    entry = SimpleNamespace(entry_id="entry_1", data={"station_id": "123"})

    init_module._async_cleanup_legacy_entity_registry(MagicMock(), entry)

    assert registry.updated == [("sensor.station_brand", {"name": None})]
    assert registry.removed == []


def test_cleanup_legacy_entity_registry_keeps_custom_name(monkeypatch) -> None:
    registry = FakeEntityRegistry(
        {
            "sensor.station_brand": SimpleNamespace(
                entity_id="sensor.station_brand",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="123_brand",
                name="Marchio preferito",
            )
        }
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: registry)
    entry = SimpleNamespace(entry_id="entry_1", data={"station_id": "123"})

    init_module._async_cleanup_legacy_entity_registry(MagicMock(), entry)

    assert registry.updated == []
    assert registry.removed == []


def test_cleanup_legacy_entity_registry_skips_unrelated_or_invalid_entries(monkeypatch) -> None:
    registry = FakeEntityRegistry(
        {
            "sensor.other_platform": SimpleNamespace(
                entity_id="sensor.other_platform",
                platform="other",
                config_entry_id="entry_1",
                unique_id="123_brand",
                name="Brand",
            ),
            "sensor.other_entry": SimpleNamespace(
                entity_id="sensor.other_entry",
                platform=init_module.DOMAIN,
                config_entry_id="entry_2",
                unique_id="123_brand",
                name="Brand",
            ),
            "sensor.invalid_ids": SimpleNamespace(
                entity_id=None,
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id=None,
                name="Brand",
            ),
            "sensor.other_station": SimpleNamespace(
                entity_id="sensor.other_station",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="456_brand",
                name="Brand",
            ),
        }
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: registry)
    entry = SimpleNamespace(entry_id="entry_1", data={"station_id": "123"})

    init_module._async_cleanup_legacy_entity_registry(MagicMock(), entry)

    assert registry.updated == []
    assert registry.removed == []


def test_cleanup_legacy_entity_registry_removes_stale_address(monkeypatch) -> None:
    registry = FakeEntityRegistry(
        {
            "sensor.station_address": SimpleNamespace(
                entity_id="sensor.station_address",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="123_address",
                name="Address",
            )
        }
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: registry)
    entry = SimpleNamespace(entry_id="entry_1", data={"station_id": "123"})

    init_module._async_cleanup_legacy_entity_registry(MagicMock(), entry)

    assert registry.updated == []
    assert registry.removed == ["sensor.station_address"]


def test_cleanup_legacy_entity_registry_removes_legacy_service_sensors(monkeypatch) -> None:
    registry = FakeEntityRegistry(
        {
            "sensor.station_service": SimpleNamespace(
                entity_id="sensor.station_service",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="123_service_1",
                name="Food & Beverage",
            ),
            "binary_sensor.station_service": SimpleNamespace(
                entity_id="binary_sensor.station_service",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="123_service_1",
                name=None,
            ),
        }
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: registry)
    entry = SimpleNamespace(entry_id="entry_1", data={"station_id": "123"})

    init_module._async_cleanup_legacy_entity_registry(MagicMock(), entry)

    assert registry.updated == []
    assert registry.removed == ["sensor.station_service"]


def test_cleanup_legacy_entity_registry_keeps_unrelated_service_entities(monkeypatch) -> None:
    registry = FakeEntityRegistry(
        {
            "sensor.other_station_service": SimpleNamespace(
                entity_id="sensor.other_station_service",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="456_service_1",
                name="Food & Beverage",
            ),
            "sensor.other_entry_service": SimpleNamespace(
                entity_id="sensor.other_entry_service",
                platform=init_module.DOMAIN,
                config_entry_id="entry_2",
                unique_id="123_service_1",
                name="Food & Beverage",
            ),
            "binary_sensor.station_service": SimpleNamespace(
                entity_id="binary_sensor.station_service",
                platform=init_module.DOMAIN,
                config_entry_id="entry_1",
                unique_id="123_service_1",
                name=None,
            ),
        }
    )
    monkeypatch.setattr(init_module.er, "async_get", lambda hass: registry)
    entry = SimpleNamespace(entry_id="entry_1", data={"station_id": "123"})

    init_module._async_cleanup_legacy_entity_registry(MagicMock(), entry)

    assert registry.updated == []
    assert registry.removed == []


def test_platforms_include_sensor_and_binary_sensor() -> None:
    assert init_module.PLATFORMS == ["sensor", "binary_sensor"]


def test_setup_entry_scheduled_refresh_reschedules(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(init_module, "get_next_run_time", lambda cron: datetime(2026, 1, 1))
    monkeypatch.setattr(
        init_module.er,
        "async_get",
        lambda hass: FakeEntityRegistry({}),
    )
    callbacks = []

    def fake_track(hass, callback, when):
        callbacks.append(callback)
        return lambda: None

    monkeypatch.setattr(init_module, "async_track_point_in_utc_time", fake_track)
    monkeypatch.setattr(init_module, "async_track_time_interval", lambda *args: lambda: None)
    monkeypatch.setattr(init_module.dt_util, "as_utc", lambda value: value)
    hass, _ = _build_hass_with_services()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = SimpleNamespace(
        entry_id="entry_1",
        title="Test Station",
        unique_id="station_1",
        options={},
        async_on_unload=MagicMock(),
        add_update_listener=MagicMock(return_value=lambda: None),
    )

    assert asyncio.run(init_module.async_setup_entry(hass, entry)) is True
    coordinator = hass.data[init_module.DOMAIN]["entry_1"]["coordinator"]
    asyncio.run(callbacks[0](datetime(2026, 1, 1)))

    assert coordinator.refresh_calls == 1
    assert len(callbacks) == 2


def test_setup_entry_returns_false_when_cron_schedule_fails(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(init_module, "get_next_run_time", MagicMock(side_effect=ValueError("bad")))
    monkeypatch.setattr(init_module, "async_track_time_interval", lambda *args: lambda: None)
    hass, _ = _build_hass_with_services()
    hass.data = {}
    entry = SimpleNamespace(
        entry_id="entry_1",
        title="Test Station",
        unique_id="station_1",
        options={},
    )

    assert asyncio.run(init_module.async_setup_entry(hass, entry)) is False
    assert "entry_1" not in hass.data.get(init_module.DOMAIN, {})


def test_setup_entry_shuts_down_on_first_refresh_not_ready(monkeypatch) -> None:
    created = []

    class NotReadyCoordinator(FakeCoordinator):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.raise_first_refresh = True
            created.append(self)

    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", NotReadyCoordinator)
    monkeypatch.setattr(init_module, "async_track_time_interval", lambda *args: lambda: None)
    hass, _ = _build_hass_with_services()
    hass.data = {}
    entry = SimpleNamespace(entry_id="entry_1", title="Test Station", unique_id="station_1", options={})

    try:
        asyncio.run(init_module.async_setup_entry(hass, entry))
    except init_module.ConfigEntryNotReady:
        pass
    else:
        raise AssertionError("Expected ConfigEntryNotReady")

    assert created[0].shutdown_calls == 1


def test_migrate_entry_version_one_removes_config_type() -> None:
    hass = MagicMock()
    entry = SimpleNamespace(version=1, data={"station_id": "123", "config_type": "old"})

    assert asyncio.run(init_module.async_migrate_entry(hass, entry)) is True
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        data={"station_id": "123"},
        version=2,
    )


def test_migrate_entry_current_version_is_noop() -> None:
    hass = MagicMock()
    entry = SimpleNamespace(version=2, data={"station_id": "123"})

    assert asyncio.run(init_module.async_migrate_entry(hass, entry)) is True
    hass.config_entries.async_update_entry.assert_not_called()


def test_unload_entry_removes_listener_coordinator_and_services() -> None:
    hass, _ = _build_hass_with_services()
    listener = MagicMock()
    coordinator = FakeCoordinator()
    hass.data = {
        init_module.DOMAIN: {
            init_module._CSV_MANAGER: coordinator.csv_manager,
            init_module._CSV_UPDATE_LISTENER: MagicMock(),
            "entry_1": {"listener": listener, "coordinator": coordinator},
        },
        init_module._SERVICES_REGISTERED: True,
    }
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    entry = SimpleNamespace(entry_id="entry_1")

    assert asyncio.run(init_module.async_unload_entry(hass, entry)) is True
    listener.assert_called_once()
    assert coordinator.shutdown_calls == 1
    hass.services.async_remove.assert_any_call(init_module.DOMAIN, init_module.SERVICE_FORCE_CSV_UPDATE)
    hass.services.async_remove.assert_any_call(init_module.DOMAIN, init_module.SERVICE_CLEAR_CACHE)
    hass.services.async_remove.assert_any_call(init_module.DOMAIN, init_module.SERVICE_COMPARE_STATIONS)
    assert init_module._SERVICES_REGISTERED not in hass.data


def test_unload_entry_returns_false_without_cleanup() -> None:
    hass, _ = _build_hass_with_services()
    hass.data = {init_module.DOMAIN: {"entry_1": {"listener": MagicMock(), "coordinator": FakeCoordinator()}}}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    entry = SimpleNamespace(entry_id="entry_1")

    assert asyncio.run(init_module.async_unload_entry(hass, entry)) is False
    assert "entry_1" in hass.data[init_module.DOMAIN]


def test_reload_entry_delegates_to_config_entries() -> None:
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = SimpleNamespace(entry_id="entry_1", title="Test Station")

    asyncio.run(init_module.async_reload_entry(hass, entry))

    hass.config_entries.async_reload.assert_awaited_once_with("entry_1")
