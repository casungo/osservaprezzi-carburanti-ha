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
    ) -> None:
        """Initialize counters."""
        self.clear_calls = 0
        self.initialize_calls = 0
        self.load_calls = 0
        self.load_result = load_result
        self.initialize_result = initialize_result

    async def async_clear_cache(self) -> bool:
        """Track cache clear calls."""
        self.clear_calls += 1
        return True

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
    ) -> None:
        """Initialize counters."""
        self.csv_manager = FakeCSVManager(load_result, initialize_result)
        self.force_update_calls = 0
        self.refresh_calls = 0
        self.first_refresh_calls = 0
        self.shutdown_calls = 0
        self.update_result = update_result
        self.data = {}

    async def async_config_entry_first_refresh(self) -> None:
        """Track first refresh calls."""
        self.first_refresh_calls += 1

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
    assert second.csv_manager.load_calls == 1
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
    assert second.csv_manager.load_calls == 1
    assert second.csv_manager.initialize_calls == 0
    assert first.refresh_calls == 1
    assert second.refresh_calls == 1


def test_clear_cache_service_initializes_secondary_when_cache_load_fails(monkeypatch) -> None:
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
    assert second.csv_manager.load_calls == 1
    assert second.csv_manager.initialize_calls == 1
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


def test_setup_entry_registers_services_after_last_entry_unload(monkeypatch) -> None:
    monkeypatch.setattr(init_module, "CarburantiDataUpdateCoordinator", FakeCoordinator)
    monkeypatch.setattr(init_module, "get_next_run_time", lambda cron: datetime(2026, 1, 1))
    monkeypatch.setattr(init_module, "async_track_point_in_utc_time", lambda *args: lambda: None)

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
