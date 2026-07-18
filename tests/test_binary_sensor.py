"""Tests for binary sensor entities."""
from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from custom_components.osservaprezzi_carburanti import binary_sensor as binary_sensor_module
from custom_components.osservaprezzi_carburanti.binary_sensor import (
    StationOpenClosedBinarySensor,
    StationServiceBinarySensor,
    async_setup_entry,
)
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN


def _sample_station_data():
    return {
        "station_info": {
            "id": "12345",
            "name": "Station Alpha",
            "nomeImpianto": "Alpha Fuel",
            "brand": "Brand X",
            "address": "Via Roma 1",
            "station_type": "Stradale",
        },
        "opening_hours": [
            {
                "giornoSettimanaId": 1,
                "flagOrarioContinuato": True,
                "oraAperturaOrarioContinuato": "08:00",
                "oraChiusuraOrarioContinuato": "20:00",
            }
        ],
        "services": [{"id": 1}, "8"],
    }


class TestBinarySensorSetup:
    def test_setup_entry_creates_binary_entities_and_unique_ids(self):
        listeners = []
        coordinator = SimpleNamespace(data=_sample_station_data(), hass=SimpleNamespace(),
                                      async_add_listener=lambda listener: listeners.append(listener) or listeners.pop)
        entry = SimpleNamespace(
            entry_id="entry_1",
            data={CONF_STATION_ID: "12345"},
            async_on_unload=lambda unsubscribe: None,
        )
        hass = SimpleNamespace(data={DOMAIN: {"entry_1": {"coordinator": coordinator}}})
        added_entities = []

        def _add_entities(entities, update_before_add=False):
            added_entities.extend(entities)
            assert update_before_add is True

        asyncio.run(async_setup_entry(hass, entry, _add_entities))

        unique_ids = {entity._attr_unique_id for entity in added_entities}
        assert {
            "12345_open_closed",
            "12345_service_1",
            "12345_service_8",
        } == unique_ids
        assert any(isinstance(entity, StationOpenClosedBinarySensor) for entity in added_entities)
        assert sum(isinstance(entity, StationServiceBinarySensor) for entity in added_entities) == 2

    def test_discovers_schedule_and_service_once_after_refresh(self):
        listeners = []
        unload_callbacks = []
        coordinator = SimpleNamespace(
            data={"station_info": {}, "opening_hours": [], "services": []}, hass=SimpleNamespace(),
            async_add_listener=lambda listener: listeners.append(listener)
            or (lambda: listeners.remove(listener)),
        )
        entry = SimpleNamespace(entry_id="entry_1", data={CONF_STATION_ID: "12345"},
                                async_on_unload=unload_callbacks.append)
        hass = SimpleNamespace(data={DOMAIN: {"entry_1": {"coordinator": coordinator}}})
        batches = []
        asyncio.run(async_setup_entry(
            hass, entry,
            lambda entities, update_before_add=False: batches.append(list(entities)),
        ))
        assert batches == []
        coordinator.data = _sample_station_data()
        listeners[0]()
        assert {entity._attr_unique_id for entity in batches[-1]} == {
            "12345_open_closed", "12345_service_1", "12345_service_8",
        }
        batch_count = len(batches)
        listeners[0]()
        coordinator.data["services"] = []
        coordinator.data["opening_hours"] = []
        listeners[0]()
        coordinator.data = _sample_station_data()
        listeners[0]()
        assert len(batches) == batch_count
        unload_callbacks[0]()
        assert listeners == []


class TestOpenClosedSensor:
    def test_no_schedule_is_closed_and_unavailable(self):
        sensor = StationOpenClosedBinarySensor.__new__(StationOpenClosedBinarySensor)
        sensor.coordinator = SimpleNamespace(data={})

        assert sensor.is_on is False
        assert sensor.available is False

    def test_closed_day_is_off(self, monkeypatch):
        sensor = StationOpenClosedBinarySensor.__new__(StationOpenClosedBinarySensor)
        sensor.coordinator = SimpleNamespace(
            data={"opening_hours": [{"giornoSettimanaId": 1, "flagChiusura": True}]}
        )
        monkeypatch.setattr(
            binary_sensor_module.dt_util,
            "now",
            lambda: datetime(2025, 3, 17, 12, 0),
        )

        assert sensor.is_on is False

    def test_h24_day_is_on(self, monkeypatch):
        sensor = StationOpenClosedBinarySensor.__new__(StationOpenClosedBinarySensor)
        sensor.coordinator = SimpleNamespace(
            data={"opening_hours": [{"giornoSettimanaId": 1, "flagH24": True}]}
        )
        monkeypatch.setattr(
            binary_sensor_module.dt_util,
            "now",
            lambda: datetime(2025, 3, 17, 12, 0),
        )

        assert sensor.is_on is True

    def test_regular_schedule_delegates_to_schedule_open(self, monkeypatch):
        sensor = StationOpenClosedBinarySensor.__new__(StationOpenClosedBinarySensor)
        sensor.coordinator = SimpleNamespace(
            data={
                "opening_hours": [
                    {
                        "giornoSettimanaId": 1,
                        "flagOrarioContinuato": True,
                        "oraAperturaOrarioContinuato": "08:00",
                        "oraChiusuraOrarioContinuato": "20:00",
                    }
                ]
            }
        )
        monkeypatch.setattr(
            binary_sensor_module.dt_util,
            "now",
            lambda: datetime(2025, 3, 17, 12, 0),
        )

        assert sensor.is_on is True

    def test_yesterday_overnight_spill_is_open_until_close_boundary(self, monkeypatch):
        timezone = ZoneInfo("Europe/Rome")
        sensor = StationOpenClosedBinarySensor.__new__(StationOpenClosedBinarySensor)
        sensor.coordinator = SimpleNamespace(data={"opening_hours": [{
            "giornoSettimanaId": 7,
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "02:00",
        }]})

        monkeypatch.setattr(
            binary_sensor_module.dt_util,
            "now",
            lambda: datetime(2025, 3, 17, 1, 59, tzinfo=timezone),
        )
        assert sensor.is_on is True

        monkeypatch.setattr(
            binary_sensor_module.dt_util,
            "now",
            lambda: datetime(2025, 3, 17, 2, 0, tzinfo=timezone),
        )
        assert sensor.is_on is False

    def test_today_overnight_is_open_at_open_boundary(self, monkeypatch):
        sensor = StationOpenClosedBinarySensor.__new__(StationOpenClosedBinarySensor)
        sensor.coordinator = SimpleNamespace(data={"opening_hours": [{
            "giornoSettimanaId": 1,
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "02:00",
        }]})
        monkeypatch.setattr(
            binary_sensor_module.dt_util,
            "now",
            lambda: datetime(2025, 3, 17, 22, 0),
        )

        assert sensor.is_on is True


class TestServiceBinarySensor:
    def test_service_entity_uses_italian_name_for_known_service(self):
        coordinator = SimpleNamespace(data=_sample_station_data(), hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})

        entity = StationServiceBinarySensor(
            coordinator,
            entry,
            "8",
            {
                "name": "Wi-Fi",
                "icon": "mdi:wifi",
                "description": "Wireless",
                "image_url": "https://example.test/8.gif",
            },
        )

        assert entity._attr_unique_id == "12345_service_8"
        assert entity._attr_has_entity_name is True
        assert entity._attr_name == "Wi-Fi"
        assert not hasattr(entity, "_attr_translation_key")
        assert entity.is_on is True

    def test_service_entity_falls_back_to_name_for_unknown_service(self):
        coordinator = SimpleNamespace(data={"services": ["99"], "station_info": {}}, hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})

        entity = StationServiceBinarySensor(
            coordinator,
            entry,
            "99",
            {
                "name": "Custom Service",
                "icon": "mdi:star",
                "description": "Custom upstream service",
                "image_url": "https://example.test/99.gif",
            },
        )

        assert entity._attr_name == "Custom Service"
        assert not hasattr(entity, "_attr_translation_key")
        assert entity.is_on is True

    def test_service_sensor_without_data_is_off_and_has_metadata(self):
        coordinator = SimpleNamespace(data=None, hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})
        entity = StationServiceBinarySensor(
            coordinator,
            entry,
            "99",
            {
                "name": "Custom",
                "icon": "mdi:star",
                "description": "Desc",
                "image_url": "https://example.test",
            },
        )

        assert entity.is_on is False
        assert entity.extra_state_attributes == {
            "service_id": "99",
            "service_name": "Custom",
            "service_description": "Desc",
            "service_icon": "mdi:star",
            "service_image_url": "https://example.test",
        }
