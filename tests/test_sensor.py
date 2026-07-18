"""Tests for pure sensor helper functions."""
from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.entity import (
    _compute_easter,
    _find_schedule_for_day,
    _get_available_service_ids,
    _has_valid_opening_hours,
    _is_italian_holiday,
    _is_schedule_open,
    _parse_time,
    _schedule_intervals_for_date,
    HOLIDAY_SCHEDULE_ID,
)
from custom_components.osservaprezzi_carburanti.sensor import (
    _get_fuel_icon,
    OsservaprezziStationSensor,
    StationLocationSensor,
    StationInfoSensor,
    StationNextChangeSensor,
    async_setup_entry,
)
from custom_components.osservaprezzi_carburanti import entity as entity_module
from custom_components.osservaprezzi_carburanti import sensor as sensor_module
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
    def test_setup_entry_creates_only_sensor_entities_and_unique_ids(self):
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
            "12345_gasolio_self",
            "12345_benzina_servito",
            "12345_name",
            "12345_nomeImpianto",
            "12345_id",
            "12345_brand",
            "12345_location",
            "12345_next_change",
        } <= unique_ids
        assert "12345_open_closed" not in unique_ids
        assert "12345_service_1" not in unique_ids
        assert "12345_service_8" not in unique_ids
        assert sum(isinstance(entity, OsservaprezziStationSensor) for entity in added_entities) == 2
        assert any(isinstance(entity, StationLocationSensor) for entity in added_entities)

    def test_discovers_only_new_valid_entities_after_refresh(self):
        listeners = []
        unload_callbacks = []
        coordinator = SimpleNamespace(
            data=_sample_station_data(), hass=SimpleNamespace(),
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
        initial_ids = {entity._attr_unique_id for entity in batches[0]}
        coordinator.data["fuels"]["gpl_self"] = {"price": 0.799}
        coordinator.data["fuels"]["malformed"] = {"price": 1.0}
        coordinator.data["station_info"]["company"] = "Example Srl"
        listeners[0]()
        assert {entity._attr_unique_id for entity in batches[-1]} == {
            "12345_gpl_self", "12345_company",
        }
        batch_count = len(batches)
        listeners[0]()
        del coordinator.data["fuels"]["gpl_self"]
        listeners[0]()
        coordinator.data["fuels"]["gpl_self"] = {"price": 0.8}
        listeners[0]()
        assert len(batches) == batch_count
        assert initial_ids.isdisjoint({"12345_gpl_self", "12345_company"})
        unload_callbacks[0]()
        assert listeners == []

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

    def test_base_entity_falls_back_to_station_id_for_device_name(self):
        coordinator = SimpleNamespace(data={"station_info": {}}, hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})

        entity = StationLocationSensor(coordinator, entry)

        assert entity.device_info["name"] == "12345"
        assert entity.device_info["model"] == "Fuel Station"

    def test_price_sensor_handles_missing_data_and_unknown_fuel(self):
        coordinator = SimpleNamespace(data=None, hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})
        entity = OsservaprezziStationSensor(coordinator, entry, "gpl_self")

        assert entity.native_value is None
        assert entity.extra_state_attributes == {}

        coordinator.data = {"fuels": {}, "station_info": {}}
        assert entity.extra_state_attributes == {}

    def test_info_and_location_sensor_properties(self):
        coordinator = SimpleNamespace(data=_sample_station_data(), hass=SimpleNamespace())
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})
        info = StationInfoSensor(coordinator, entry, "brand", "Marchio", "mdi:tag")
        location = StationLocationSensor(coordinator, entry)

        assert info._attr_name == "Marchio"
        assert not hasattr(info, "_attr_translation_key")
        assert info.native_value == "Brand X"
        assert location._attr_name == "Posizione"
        assert not hasattr(location, "_attr_translation_key")
        assert location.native_value == "Via Roma 1"
        assert location.available is True
        assert location.extra_state_attributes["latitude"] == 41.902782

    def test_location_sensor_unavailable_without_coordinates(self):
        coordinator = SimpleNamespace(
            data={"station_info": {"name": "Station"}},
            hass=SimpleNamespace(),
        )
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})

        entity = StationLocationSensor(coordinator, entry)

        assert entity.native_value == "Station"
        assert entity.available is False

    def test_schedule_tick_uses_thread_safe_state_update(self):
        entity = StationNextChangeSensor.__new__(StationNextChangeSensor)
        calls = []

        def _schedule_update_ha_state():
            calls.append("scheduled")

        entity.schedule_update_ha_state = _schedule_update_ha_state

        entity._handle_time_tick(datetime(2026, 6, 1, 12, 0))

        assert calls == ["scheduled"]

    def test_schedule_entity_registers_and_removes_timer(self, monkeypatch):
        listener = lambda: calls.append("removed")
        calls = []

        def fake_track_time_interval(hass, callback, interval):
            calls.append((hass, callback, interval))
            return listener

        monkeypatch.setattr(entity_module, "async_track_time_interval", fake_track_time_interval)
        coordinator = SimpleNamespace(data=_sample_station_data(), hass="hass")
        entry = SimpleNamespace(data={CONF_STATION_ID: "12345"})
        entity = StationNextChangeSensor(coordinator, entry)

        asyncio.run(entity.async_added_to_hass())
        assert entity._time_listener is listener
        asyncio.run(entity.async_will_remove_from_hass())
        assert entity._time_listener is None
        assert calls[-1] == "removed"


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

    def test_continuous_hours_missing_time_is_closed(self):
        assert _is_schedule_open({"flagOrarioContinuato": True}, time(12, 0)) is False


class TestScheduleIntervals:
    def test_overnight_interval_uses_next_local_date(self):
        timezone = ZoneInfo("Europe/Rome")
        schedule = {
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "02:00",
        }

        assert _schedule_intervals_for_date(schedule, date(2025, 3, 29), timezone) == [
            (
                datetime(2025, 3, 29, 22, 0, tzinfo=timezone),
                datetime(2025, 3, 30, 2, 0, tzinfo=timezone),
            )
        ]

    def test_h24_respects_dst_local_midnights(self):
        timezone = ZoneInfo("Europe/Rome")
        [(opens_at, closes_at)] = _schedule_intervals_for_date(
            {"flagH24": True}, date(2025, 3, 30), timezone
        )

        assert opens_at == datetime(2025, 3, 30, 0, 0, tzinfo=timezone)
        assert closes_at == datetime(2025, 3, 31, 0, 0, tzinfo=timezone)
        assert closes_at.utcoffset() != opens_at.utcoffset()

    def test_incomplete_interval_is_ignored(self):
        assert _schedule_intervals_for_date(
            {
                "flagOrarioContinuato": True,
                "oraAperturaOrarioContinuato": "08:00",
            },
            date(2025, 3, 30),
            ZoneInfo("Europe/Rome"),
        ) == []


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

    def test_continuous_hours_missing_close_is_not_valid(self):
        assert _has_valid_opening_hours({
            "opening_hours": [{
                "flagOrarioContinuato": True,
                "oraAperturaOrarioContinuato": "08:00",
            }]
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


class TestNextChangeSensor:
    def _sensor(self, opening_hours):
        sensor = StationNextChangeSensor.__new__(StationNextChangeSensor)
        sensor.coordinator = SimpleNamespace(data={"opening_hours": opening_hours})
        return sensor

    def test_no_schedule(self):
        sensor = StationNextChangeSensor.__new__(StationNextChangeSensor)
        sensor.coordinator = SimpleNamespace(data={})

        assert sensor._compute_next_change() == ("no_schedule", None)
        assert sensor.native_value == "no_schedule"
        assert sensor.extra_state_attributes == {
            "change_type": "no_schedule",
            "next_change_time": None,
        }

    def test_currently_open_continuous_closes_today(self, monkeypatch):
        sensor = self._sensor([
            {
                "giornoSettimanaId": 1,
                "flagOrarioContinuato": True,
                "oraAperturaOrarioContinuato": "08:00",
                "oraChiusuraOrarioContinuato": "20:00",
            }
        ])
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: datetime(2025, 3, 17, 12, 0))

        assert sensor._compute_next_change() == ("closes_at", datetime(2025, 3, 17, 20, 0))
        assert sensor.native_value == "20:00"
        assert sensor.extra_state_attributes["minutes_until_change"] == 480
        assert sensor.available is True

    def test_currently_open_split_closes_next_period(self, monkeypatch):
        sensor = self._sensor([
            {
                "giornoSettimanaId": 1,
                "oraAperturaMattina": "08:00",
                "oraChiusuraMattina": "12:00",
                "oraAperturaPomeriggio": "15:00",
                "oraChiusuraPomeriggio": "19:00",
            }
        ])
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: datetime(2025, 3, 17, 16, 0))

        assert sensor._compute_next_change() == ("closes_at", datetime(2025, 3, 17, 19, 0))

    def test_closed_now_opens_later_today(self, monkeypatch):
        sensor = self._sensor([
            {
                "giornoSettimanaId": 1,
                "oraAperturaMattina": "08:00",
                "oraChiusuraMattina": "12:00",
                "oraAperturaPomeriggio": "15:00",
                "oraChiusuraPomeriggio": "19:00",
            }
        ])
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: datetime(2025, 3, 17, 13, 0))

        assert sensor._compute_next_change() == ("opens_at", datetime(2025, 3, 17, 15, 0))

    def test_closed_today_opens_tomorrow_and_formats_date(self, monkeypatch):
        sensor = self._sensor([
            {"giornoSettimanaId": 1, "flagChiusura": True},
            {
                "giornoSettimanaId": 2,
                "flagOrarioContinuato": True,
                "oraAperturaOrarioContinuato": "08:00",
                "oraChiusuraOrarioContinuato": "20:00",
            },
        ])
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: datetime(2025, 3, 17, 13, 0))

        assert sensor.native_value == "08:00 (18/03)"

    def test_no_opening_found(self, monkeypatch):
        sensor = self._sensor([
            {"giornoSettimanaId": weekday, "flagChiusura": True}
            for weekday in range(1, 8)
        ])
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: datetime(2025, 3, 17, 13, 0))

        assert sensor._compute_next_change() == ("no_opening", None)

    def test_overnight_today_closes_tomorrow(self, monkeypatch):
        timezone = ZoneInfo("Europe/Rome")
        sensor = self._sensor([{
            "giornoSettimanaId": 1,
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "02:00",
        }])
        now = datetime(2025, 3, 17, 23, 0, tzinfo=timezone)
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: now)

        assert sensor._compute_next_change() == (
            "closes_at",
            datetime(2025, 3, 18, 2, 0, tzinfo=timezone),
        )

    def test_overnight_yesterday_closes_today(self, monkeypatch):
        timezone = ZoneInfo("Europe/Rome")
        sensor = self._sensor([{
            "giornoSettimanaId": 1,
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "22:00",
            "oraChiusuraOrarioContinuato": "02:00",
        }])
        now = datetime(2025, 3, 18, 1, 0, tzinfo=timezone)
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: now)

        assert sensor._compute_next_change() == (
            "closes_at",
            datetime(2025, 3, 18, 2, 0, tzinfo=timezone),
        )

    def test_finds_next_opening_exactly_seven_days_away(self, monkeypatch):
        sensor = self._sensor([{
            "giornoSettimanaId": 1,
            "flagOrarioContinuato": True,
            "oraAperturaOrarioContinuato": "08:00",
            "oraChiusuraOrarioContinuato": "09:00",
        }])
        now = datetime(2025, 3, 17, 9, 0)
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: now)

        assert sensor._compute_next_change() == (
            "opens_at",
            now + timedelta(days=7, hours=-1),
        )

    def test_future_h24_opens_at_local_midnight(self, monkeypatch):
        timezone = ZoneInfo("Europe/Rome")
        sensor = self._sensor([
            {"giornoSettimanaId": 1, "flagChiusura": True},
            {"giornoSettimanaId": 2, "flagH24": True},
        ])
        now = datetime(2025, 3, 17, 20, 0, tzinfo=timezone)
        monkeypatch.setattr(sensor_module.dt_util, "now", lambda: now)

        assert sensor._compute_next_change() == (
            "opens_at",
            datetime(2025, 3, 18, 0, 0, tzinfo=timezone),
        )
