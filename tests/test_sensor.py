"""Tests for pure sensor helper functions."""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, time
from types import SimpleNamespace

import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.sensor import (
    _compute_easter,
    _find_schedule_for_day,
    _get_available_service_ids,
    _get_fuel_icon,
    _has_valid_opening_hours,
    _is_italian_holiday,
    _is_schedule_open,
    _parse_time,
    HOLIDAY_SCHEDULE_ID,
    OsservaprezziStationSensor,
    StationLocationSensor,
    StationNextChangeSensor,
    StationOpenClosedBinarySensor,
    StationServiceBinarySensor,
    async_setup_entry,
)
from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID, DOMAIN


def _sample_station_data():
    return {
        "fuels": {
            "gasolio_self": {
                "price": 1.701,
                "last_update": "2026-06-01T08:00:00+02:00",
                "validity_date": "2026-06-01T08:00:00+02:00",
                "previous_price": 1.755,
                "price_changed_at": "2026-05-31T08:00:00+02:00",
            },
            "benzina_servito": {"price": 1.899},
        },
        "station_info": {
            "id": "12345",
            "name": "Station Alpha",
            "nomeImpianto": "Alpha Fuel",
            "brand": "Brand X",
            "address": "Via Roma 1",
            "station_type": "Stradale",
            "latitude": 41.902782,
            "longitude": 12.496366,
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


class TestEntitySetupRegression:
    def test_setup_entry_creates_expected_entities_and_unique_ids(self):
        coordinator = SimpleNamespace(data=_sample_station_data(), hass=SimpleNamespace())
        entry = SimpleNamespace(
            entry_id="entry_1",
            data={CONF_STATION_ID: "12345"},
        )
        hass = SimpleNamespace(data={DOMAIN: {"entry_1": {"coordinator": coordinator}}})
        added_entities = []

        def _add_entities(entities, update_before_add=False):
            added_entities.extend(entities)
            assert update_before_add is True

        asyncio.run(async_setup_entry(hass, entry, _add_entities))

        unique_ids = {entity._attr_unique_id for entity in added_entities}
        assert {
            "12345_gasolio_self",
            "12345_benzina_servito",
            "12345_name",
            "12345_nomeImpianto",
            "12345_id",
            "12345_brand",
            "12345_location",
            "12345_open_closed",
            "12345_next_change",
            "12345_service_1",
            "12345_service_8",
        } <= unique_ids
        assert sum(isinstance(entity, OsservaprezziStationSensor) for entity in added_entities) == 2
        assert any(isinstance(entity, StationLocationSensor) for entity in added_entities)
        assert any(isinstance(entity, StationOpenClosedBinarySensor) for entity in added_entities)
        assert sum(isinstance(entity, StationServiceBinarySensor) for entity in added_entities) == 2

    def test_price_sensor_keeps_state_and_attributes_contract(self):
        coordinator = SimpleNamespace(data=_sample_station_data(), hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})

        entity = OsservaprezziStationSensor(coordinator, entry, "gasolio_self")

        assert entity._attr_unique_id == "12345_gasolio_self"
        assert entity._attr_has_entity_name is True
        assert entity.native_value == 1.701
        assert entity.extra_state_attributes["fuel_type_name"] == "Gasolio"
        assert entity.extra_state_attributes["is_self_service"] is True
        assert entity.extra_state_attributes["station_brand"] == "Brand X"
        assert entity.device_info["identifiers"] == {(DOMAIN, "12345")}
        assert entity.device_info["name"] == "Alpha Fuel"

    def test_service_entity_uses_translation_for_known_service(self):
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
        assert entity._attr_translation_key == "wifi"
        assert not hasattr(entity, "_attr_name")
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

        assert entity._attr_translation_key is None
        assert entity._attr_name == "Custom Service"
        assert entity.is_on is True

    @pytest.mark.parametrize("entity_cls", [StationOpenClosedBinarySensor, StationNextChangeSensor])
    def test_schedule_tick_uses_thread_safe_state_update(self, entity_cls):
        entity = entity_cls.__new__(entity_cls)
        calls = []

        def _schedule_update_ha_state():
            calls.append("scheduled")

        entity.schedule_update_ha_state = _schedule_update_ha_state

        entity._handle_time_tick(datetime(2026, 6, 1, 12, 0))

        assert calls == ["scheduled"]


class TestParseTime:
    def test_hh_mm_format(self):
        assert _parse_time("07:30") == time(7, 30)

    def test_hh_mm_format_midnight(self):
        assert _parse_time("00:00") == time(0, 0)

    def test_hh_dot_mm_format(self):
        assert _parse_time("07.30") == time(7, 30)

    def test_hh_dot_mm_format_24(self):
        assert _parse_time("24.00") == time(0, 0)

    def test_hh_only_format(self):
        assert _parse_time("7") == time(7, 0)

    def test_hh_only_format_24(self):
        assert _parse_time("24") == time(0, 0)

    def test_none_returns_none(self):
        assert _parse_time(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_time("") is None

    def test_invalid_returns_none(self):
        assert _parse_time("abc") is None


class TestComputeEaster:
    def test_easter_2024(self):
        assert _compute_easter(2024) == date(2024, 3, 31)

    def test_easter_2025(self):
        assert _compute_easter(2025) == date(2025, 4, 20)

    def test_easter_2026(self):
        assert _compute_easter(2026) == date(2026, 4, 5)

    def test_easter_2023(self):
        assert _compute_easter(2023) == date(2023, 4, 9)


class TestIsItalianHoliday:
    def test_capodanno(self):
        assert _is_italian_holiday(date(2025, 1, 1)) is True

    def test_epifania(self):
        assert _is_italian_holiday(date(2025, 1, 6)) is True

    def test_liberazione(self):
        assert _is_italian_holiday(date(2025, 4, 25)) is True

    def test_festa_lavoro(self):
        assert _is_italian_holiday(date(2025, 5, 1)) is True

    def test_festa_repubblica(self):
        assert _is_italian_holiday(date(2025, 6, 2)) is True

    def test_ferragosto(self):
        assert _is_italian_holiday(date(2025, 8, 15)) is True

    def test_tutti_santi(self):
        assert _is_italian_holiday(date(2025, 11, 1)) is True

    def test_immacolata(self):
        assert _is_italian_holiday(date(2025, 12, 8)) is True

    def test_natale(self):
        assert _is_italian_holiday(date(2025, 12, 25)) is True

    def test_santo_stefano(self):
        assert _is_italian_holiday(date(2025, 12, 26)) is True

    def test_easter_monday_2025(self):
        assert _is_italian_holiday(date(2025, 4, 21)) is True

    def test_easter_sunday_2025(self):
        assert _is_italian_holiday(date(2025, 4, 20)) is True

    def test_regular_day(self):
        assert _is_italian_holiday(date(2025, 3, 15)) is False

    def test_not_holiday(self):
        assert _is_italian_holiday(date(2025, 7, 14)) is False


class TestIsScheduleOpen:
    def test_continuous_hours_open(self):
        schedule = {
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "08:00",
            "oraChiusuraOrarioContinuato": "20:00",
        }
        assert _is_schedule_open(schedule, time(12, 0)) is True

    def test_continuous_hours_closed(self):
        schedule = {
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "08:00",
            "oraChiusuraOrarioContinuato": "20:00",
        }
        assert _is_schedule_open(schedule, time(21, 0)) is False

    def test_split_hours_morning_open(self):
        schedule = {
            "flagOrarioContinuato": False,
            "oraAperturaMattina": "07:00",
            "oraChiusuraMattina": "12:00",
            "oraAperturaPomeriggio": "15:00",
            "oraChiusuraPomeriggio": "19:00",
        }
        assert _is_schedule_open(schedule, time(9, 0)) is True

    def test_split_hours_afternoon_open(self):
        schedule = {
            "flagOrarioContinuato": False,
            "oraAperturaMattina": "07:00",
            "oraChiusuraMattina": "12:00",
            "oraAperturaPomeriggio": "15:00",
            "oraChiusuraPomeriggio": "19:00",
        }
        assert _is_schedule_open(schedule, time(16, 0)) is True

    def test_split_hours_closed_gap(self):
        schedule = {
            "flagOrarioContinuato": False,
            "oraAperturaMattina": "07:00",
            "oraChiusuraMattina": "12:00",
            "oraAperturaPomeriggio": "15:00",
            "oraChiusuraPomeriggio": "19:00",
        }
        assert _is_schedule_open(schedule, time(13, 30)) is False

    def test_overnight_open(self):
        schedule = {
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "06:00",
        }
        assert _is_schedule_open(schedule, time(23, 0)) is True

    def test_overnight_open_after_midnight(self):
        schedule = {
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "06:00",
        }
        assert _is_schedule_open(schedule, time(3, 0)) is True

    def test_overnight_closed(self):
        schedule = {
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "06:00",
        }
        assert _is_schedule_open(schedule, time(15, 0)) is False


class TestFindScheduleForDay:
    def test_finds_regular_weekday(self):
        opening_hours = [
            {"giornoSettimanaId": 1},
            {"giornoSettimanaId": 2},
        ]
        result = _find_schedule_for_day(opening_hours, 1, date(2025, 3, 17))
        assert result is not None
        assert result["giornoSettimanaId"] == 1

    def test_returns_none_for_missing_day(self):
        opening_hours = [
            {"giornoSettimanaId": 1},
        ]
        result = _find_schedule_for_day(opening_hours, 5, date(2025, 3, 17))
        assert result is None

    def test_uses_holiday_schedule_on_holiday(self):
        opening_hours = [
            {"giornoSettimanaId": 1, "flagChiusura": True},
            {"giornoSettimanaId": HOLIDAY_SCHEDULE_ID, "flagOrarioContinuato": True,
             "oraAperturaOrarioContinuato": "08:00", "oraChiusuraOrarioContinuato": "13:00"},
        ]
        result = _find_schedule_for_day(opening_hours, 1, date(2025, 1, 1))
        assert result is not None
        assert result["giornoSettimanaId"] == HOLIDAY_SCHEDULE_ID

    def test_regular_day_ignores_holiday_schedule(self):
        opening_hours = [
            {"giornoSettimanaId": 1, "flagOrarioContinuato": True,
             "oraAperturaOrarioContinuato": "08:00", "oraChiusuraOrarioContinuato": "20:00"},
            {"giornoSettimanaId": HOLIDAY_SCHEDULE_ID, "flagChiusura": True},
        ]
        result = _find_schedule_for_day(opening_hours, 1, date(2025, 3, 17))
        assert result is not None
        assert result["giornoSettimanaId"] == 1


class TestHasValidOpeningHours:
    def test_empty_data(self):
        assert _has_valid_opening_hours({}) is False
        assert _has_valid_opening_hours(None) is False

    def test_no_opening_hours_key(self):
        assert _has_valid_opening_hours({"fuels": {}}) is False

    def test_empty_opening_hours(self):
        assert _has_valid_opening_hours({"opening_hours": []}) is False

    def test_h24(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{"flagH24": True}]
        }) is True

    def test_non_communicated_hours_are_not_valid(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{"flagNonComunicato": True, "flagH24": True}]
        }) is False

    def test_continuous_hours(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{
                "flagOrarioContinuato": True,
                "oraAperturaOrarioContinuato": "08:00",
                "oraChiusuraOrarioContinuato": "20:00",
            }]
        }) is True

    def test_split_hours(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{
                "oraAperturaMattina": "07:00",
                "oraChiusuraMattina": "12:00",
                "oraAperturaPomeriggio": "15:00",
                "oraChiusuraPomeriggio": "19:00",
            }]
        }) is True

    def test_all_closed(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{"flagChiusura": True}]
        }) is False

    def test_no_valid_times(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{}]
        }) is False


class TestGetFuelIcon:
    def test_benzina(self):
        assert _get_fuel_icon("Benzina") == "mdi:gas-station"

    def test_gasolio(self):
        assert _get_fuel_icon("Gasolio") == "mdi:fuel"

    def test_diesel(self):
        assert _get_fuel_icon("Diesel") == "mdi:fuel"

    def test_gpl(self):
        assert _get_fuel_icon("GPL") == "mdi:gas-cylinder"

    def test_metano(self):
        assert _get_fuel_icon("Metano") == "mdi:molecule-co2"

    def test_other(self):
        assert _get_fuel_icon("Unknown Fuel") == "mdi:currency-eur"


class TestGetAvailableServiceIds:
    def test_normalizes_mixed_service_payloads(self):
        services = [{"id": 1}, "2", 3, {"id": "4"}, {"other": "ignored"}]
        assert _get_available_service_ids(services) == {"1", "2", "3", "4"}


class TestNextChangeH24:
    def test_h24_only_today_closes_at_next_closed_midnight(self, monkeypatch):
        sensor = StationNextChangeSensor.__new__(StationNextChangeSensor)
        opening_hours = [
            {"giornoSettimanaId": 1, "flagH24": True},
            {"giornoSettimanaId": 2, "flagChiusura": True},
        ]
        sensor.coordinator = SimpleNamespace(data={"opening_hours": opening_hours})
        fixed_now = datetime(2025, 3, 17, 12, 0)

        import custom_components.osservaprezzi_carburanti.sensor as sensor_module

        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: fixed_now)

        change_type, change_time = sensor._compute_next_change()

        assert change_type == "closes_at"
        assert change_time == datetime(2025, 3, 18, 0, 0)

    def test_all_week_h24_is_always_open(self, monkeypatch):
        sensor = StationNextChangeSensor.__new__(StationNextChangeSensor)
        opening_hours = [
            {"giornoSettimanaId": weekday, "flagH24": True}
            for weekday in range(1, 8)
        ]
        sensor.coordinator = SimpleNamespace(data={"opening_hours": opening_hours})
        fixed_now = datetime(2025, 3, 17, 12, 0)

        import custom_components.osservaprezzi_carburanti.sensor as sensor_module

        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: fixed_now)

        change_type, change_time = sensor._compute_next_change()

        assert change_type == "always_open"
        assert change_time is None
