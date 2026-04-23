"""Tests for CSV manager parsing logic."""
from __future__ import annotations
import sys
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager, CSV_COLUMNS


@pytest.fixture
def csv_manager():
    hass = MagicMock()
    hass.config.path.return_value = "/tmp/test_storage"
    return CSVStationManager(hass)


PIPE_CSV_LINES = [
    "2025-01-15T10:00:00",
    "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine",
    "12345|Operator A|Brand X|Stradale|Station Alpha|Via Roma 1|Roma|RM|41.902782|12.496366",
    "67890|Operator B|Brand Y|Autostradale|Station Beta|Via Milano 2|Milano|MI|45.4642|9.1900",
    "11111|Operator C|Brand Z|Stradale|Station NoCoords|Via Napoli 3|Napoli|NA||",
]

SEMICOLON_CSV_LINES = [
    "2025-01-15T10:00:00",
    "idImpianto;Gestore;Bandiera;Tipo Impianto;Nome Impianto;Indirizzo;Comune;Provincia;Latitudine;Longitudine",
    "12345;Operator A;Brand X;Stradale;Station Alpha;Via Roma 1;Roma;RM;41,902782;12,496366",
    "67890;Operator B;Brand Y;Autostradale;Station Beta;Via Milano 2;Milano;MI;45,4642;9,1900",
]


class TestCSVParsing:
    def test_parse_pipe_separated(self, csv_manager):
        result = csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        assert result is True
        assert len(csv_manager._stations_cache) == 2
        assert "12345" in csv_manager._stations_cache
        assert "67890" in csv_manager._stations_cache

    def test_parse_semicolon_separated(self, csv_manager):
        result = csv_manager._parse_csv_lines(SEMICOLON_CSV_LINES)
        assert result is True
        assert len(csv_manager._stations_cache) == 2

    def test_italian_decimal_format(self, csv_manager):
        csv_manager._parse_csv_lines(SEMICOLON_CSV_LINES)
        station = csv_manager._stations_cache.get("12345")
        assert station is not None
        assert isinstance(station["latitude"], float)
        assert isinstance(station["longitude"], float)
        assert abs(station["latitude"] - 41.902782) < 0.001

    def test_stations_without_coords_excluded(self, csv_manager):
        csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        assert "11111" not in csv_manager._stations_cache

    def test_insufficient_lines(self, csv_manager):
        assert csv_manager._parse_csv_lines(["header"]) is False
        assert csv_manager._parse_csv_lines([]) is False

    def test_station_data_fields(self, csv_manager):
        csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        station = csv_manager._stations_cache["12345"]
        assert station["operator"] == "Operator A"
        assert station["brand"] == "Brand X"
        assert station["station_type"] == "Stradale"
        assert station["name"] == "Station Alpha"
        assert station["address"] == "Via Roma 1"
        assert station["municipality"] == "Roma"
        assert station["province"] == "RM"

    def test_get_station_by_id(self, csv_manager):
        csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        station = csv_manager.get_station_by_id("12345")
        assert station is not None
        assert station["name"] == "Station Alpha"

    def test_get_station_by_id_not_found(self, csv_manager):
        csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        assert csv_manager.get_station_by_id("99999") is None

    def test_is_data_available(self, csv_manager):
        assert csv_manager.is_data_available() is False
        csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        assert csv_manager.is_data_available() is True

    def test_detect_separator_pipe(self, csv_manager):
        csv_manager._parse_csv_lines(PIPE_CSV_LINES)
        assert csv_manager._detected_separator == "|"

    def test_detect_separator_semicolon(self, csv_manager):
        csv_manager._parse_csv_lines(SEMICOLON_CSV_LINES)
        assert csv_manager._detected_separator == ";"
