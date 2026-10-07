"""Validation shared by discovery, registry parsing and station metadata."""
from __future__ import annotations

import pytest

from custom_components.osservaprezzi_carburanti.data_helpers import (
    as_coordinate, parse_coordinate, station_display_name, fuel_display_name,
)


@pytest.mark.parametrize("value", [True, False, None, "42", float("nan"), float("inf"), -91, 91, 10 ** 1000])
def test_strict_coordinates_reject_invalid_values(value):
    assert as_coordinate(value, -90, 90) is None


@pytest.mark.parametrize("value,expected", [(0, 0), (-90, -90), (90.0, 90.0)])
def test_strict_coordinates_accept_boundaries(value, expected):
    assert as_coordinate(value, -90, 90) == expected


@pytest.mark.parametrize("value,expected", [("41,9", 41.9), (12, 12), (True, None), ("invalid", None), ({}, None), ("NaN", None), ("181", None)])
def test_external_coordinates_support_decimal_comma(value, expected):
    assert parse_coordinate(value, -180, 180) == expected


@pytest.mark.parametrize("station,expected", [({"nomeImpianto": "  Nome  ", "name": "Fallback"}, "Nome"), ({"nomeImpianto": "", "name": "  CSV "}, "CSV"), ({"nomeImpianto": 12, "name": None}, "ID")])
def test_station_display_name_prioritizes_valid_names(station, expected):
    assert station_display_name(station, "ID") == expected


@pytest.mark.parametrize("value,expected", [("gpl", "GPL"), ("gnl", "GNL"), ("hvo diesel", "HVO Diesel"), ("benzina_e10", "Benzina E10"), ("gasolio", "Gasolio")])
def test_fuel_names_preserve_acronyms(value, expected):
    assert fuel_display_name(value) == expected
