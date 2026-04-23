"""Tests for pure sensor helper functions."""
from __future__ import annotations
import sys
from datetime import date, time

import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.sensor import (
    _parse_time,
    _is_italian_holiday,
    _compute_easter,
    _is_schedule_open,
    _find_schedule_for_day,
    _has_valid_opening_hours,
    _get_fuel_icon,
    HOLIDAY_SCHEDULE_ID,
)


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
