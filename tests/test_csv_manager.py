"""Tests for CSV manager parsing logic."""
from __future__ import annotations
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest


sys.path.insert(0, ".")

from custom_components.osservaprezzi_carburanti import csv_manager as csv_module
from custom_components.osservaprezzi_carburanti.csv_manager import CSVStationManager


async def _run_in_executor(func, *args):
    return func(*args)


@pytest.fixture
def csv_manager():
    hass = MagicMock()
    hass.config.path.return_value = "/tmp/test_storage"
    hass.async_add_executor_job.side_effect = _run_in_executor
    return CSVStationManager(hass)


def _parse_csv_lines(csv_manager, lines):
    success, separator, stations_cache = csv_manager._parse_csv_lines_to_cache(lines)
    if success:
        csv_manager._detected_separator = separator
        csv_manager._stations_cache = stations_cache
    return success


class FakeCSVResponse:
    """Async response context for CSV download tests."""

    def __init__(self, status=200, text="", headers=None):
        self.status = status
        self._text = text
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def text(self):
        return self._text


class FakeCSVSession:
    """Session fake returning a configured CSV response or exception."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


PIPE_CSV_LINES = [
    "2025-01-15T10:00:00",
    "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine",
    "12345|Operator A|Brand X|Stradale|Station Alpha|Via Roma 1|Roma|RM|41.902782|12.496366",
    "67890|Operator B|Brand Y|Autostradale|Station Beta|Via Milano 2|Milano|MI|45.4642|9.1900",
    "11111|Operator C|Brand Z|Stradale|Station NoCoords|Via Napoli 3|Napoli|NA||",
]

QUOTED_PIPE_CSV_LINES = [
    "2025-01-15T10:00:00",
    "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine",
    '22222|Operator D|Brand Q|Stradale|"Station | Quoted"|"Via Roma | 2"|Roma|RM|41.902782|12.496366',
]

SEMICOLON_CSV_LINES = [
    "2025-01-15T10:00:00",
    "idImpianto;Gestore;Bandiera;Tipo Impianto;Nome Impianto;Indirizzo;Comune;Provincia;Latitudine;Longitudine",
    "12345;Operator A;Brand X;Stradale;Station Alpha;Via Roma 1;Roma;RM;41,902782;12,496366",
    "67890;Operator B;Brand Y;Autostradale;Station Beta;Via Milano 2;Milano;MI;45,4642;9,1900",
]


class TestCSVParsing:
    def test_parse_pipe_separated(self, csv_manager):
        result = _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        assert result is True
        assert len(csv_manager._stations_cache) == 2
        assert "12345" in csv_manager._stations_cache
        assert "67890" in csv_manager._stations_cache

    def test_parse_lines_to_cache_does_not_mutate_manager_state(self, csv_manager):
        csv_manager._detected_separator = ";"

        success, separator, stations_cache = csv_manager._parse_csv_lines_to_cache(PIPE_CSV_LINES)

        assert success is True
        assert separator == "|"
        assert "12345" in stations_cache
        assert csv_manager._detected_separator == ";"
        assert csv_manager._stations_cache == {}

    def test_parse_semicolon_separated(self, csv_manager):
        result = _parse_csv_lines(csv_manager, SEMICOLON_CSV_LINES)
        assert result is True
        assert len(csv_manager._stations_cache) == 2

    def test_parse_quoted_separator_in_fields(self, csv_manager):
        result = _parse_csv_lines(csv_manager, QUOTED_PIPE_CSV_LINES)
        assert result is True
        assert csv_manager._stations_cache["22222"]["name"] == "Station | Quoted"
        assert csv_manager._stations_cache["22222"]["address"] == "Via Roma | 2"

    def test_parse_downloaded_content_with_quoted_embedded_newline(self, csv_manager):
        content = "\n".join(
            [
                "2025-01-15T10:00:00",
                "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine",
                '33333|Operator E|Brand N|Stradale|"Station\nNewline"|Via Torino 4|Torino|TO|45.0703|7.6869',
            ]
        )

        success, separator, stations_cache = csv_manager._parse_csv_content_to_cache(content)

        assert success is True
        assert separator == "|"
        assert stations_cache["33333"]["name"] == "Station\nNewline"

    def test_italian_decimal_format(self, csv_manager):
        _parse_csv_lines(csv_manager, SEMICOLON_CSV_LINES)
        station = csv_manager._stations_cache.get("12345")
        assert station is not None
        assert isinstance(station["latitude"], float)
        assert isinstance(station["longitude"], float)
        assert abs(station["latitude"] - 41.902782) < 0.001

    def test_stations_without_coords_excluded(self, csv_manager):
        _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        assert "11111" not in csv_manager._stations_cache

    def test_insufficient_lines(self, csv_manager):
        assert _parse_csv_lines(csv_manager, ["header"]) is False
        assert _parse_csv_lines(csv_manager, []) is False

    @pytest.mark.parametrize(
        "content",
        [
            "<html>\n<body>Service unavailable</body>",
            "2025-01-15T10:00:00\nidImpianto|Latitudine|Longitudine",
            "2025-01-15T10:00:00\nLatitudine|Longitudine\n41.9|12.5",
            "2025-01-15T10:00:00\nidImpianto|Longitudine\n12345|12.5",
            "2025-01-15T10:00:00\nidImpianto|Latitudine\n12345|41.9",
        ],
    )
    def test_rejects_unusable_csv(self, csv_manager, content):
        success, _, stations = csv_manager._parse_csv_content_to_cache(content)

        assert success is False
        assert stations == {}

    def test_accepts_csv_without_optional_columns(self, csv_manager):
        content = "2025-01-15T10:00:00\nidImpianto|Latitudine|Longitudine\n12345|41.9|12.5"

        success, separator, stations = csv_manager._parse_csv_content_to_cache(content)

        assert success is True
        assert separator == "|"
        assert stations == {"12345": {"id": "12345", "latitude": 41.9, "longitude": 12.5}}

    def test_station_data_fields(self, csv_manager):
        _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        station = csv_manager._stations_cache["12345"]
        assert station["operator"] == "Operator A"
        assert station["brand"] == "Brand X"
        assert station["station_type"] == "Stradale"
        assert station["name"] == "Station Alpha"
        assert station["address"] == "Via Roma 1"
        assert station["municipality"] == "Roma"
        assert station["province"] == "RM"

    def test_get_station_by_id(self, csv_manager):
        _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        station = csv_manager.get_station_by_id("12345")
        assert station is not None
        assert station["name"] == "Station Alpha"

    def test_get_station_by_id_not_found(self, csv_manager):
        _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        assert csv_manager.get_station_by_id("99999") is None

    def test_is_data_available(self, csv_manager):
        assert csv_manager.is_data_available() is False
        _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        assert csv_manager.is_data_available() is True

    def test_detect_separator_pipe(self, csv_manager):
        _parse_csv_lines(csv_manager, PIPE_CSV_LINES)
        assert csv_manager._detected_separator == "|"

    def test_detect_separator_semicolon(self, csv_manager):
        _parse_csv_lines(csv_manager, SEMICOLON_CSV_LINES)
        assert csv_manager._detected_separator == ";"

    def test_get_separator_direct_branches(self):
        assert CSVStationManager._get_separator("a|b|c") == "|"
        assert CSVStationManager._get_separator("a;b;c") == ";"
        assert CSVStationManager._get_separator("abc") == "|"

    def test_build_column_indices_marks_missing_columns(self, csv_manager):
        indices = csv_manager._build_column_indices("idImpianto|Latitudine|Longitudine", "|")

        assert indices["id"] == 0
        assert indices["latitude"] == 1
        assert indices["longitude"] == 2
        assert indices["operator"] == -1

    def test_parse_station_values_empty_and_incomplete_rows(self, csv_manager):
        indices = csv_manager._build_column_indices(PIPE_CSV_LINES[1], "|")

        parsed = csv_manager._parse_station_values(PIPE_CSV_LINES[2].split("|"), indices)

        assert parsed is not None
        assert parsed[0] == "12345"
        assert csv_manager._parse_station_values([], indices) is None
        assert csv_manager._parse_station_values(["12345"], indices) is None

    def test_parse_coordinate_invalid_values(self):
        assert CSVStationManager._parse_coordinate("") is None
        assert CSVStationManager._parse_coordinate("not-a-number") is None

    def test_parse_csv_data_from_file_streaming(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")

        hass.async_add_executor_job.side_effect = _run_in_executor

        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        csv_manager._cache_path = str(tmp_path / "cache.json")

        csv_manager_path = tmp_path / "stations.csv"
        csv_manager_path.write_text("\n".join(PIPE_CSV_LINES), encoding="utf-8")

        result = asyncio.run(csv_manager._parse_csv_data_from_file())
        assert result is True
        assert "12345" in csv_manager._stations_cache
        assert "67890" in csv_manager._stations_cache

    def test_parse_csv_data_from_file_insufficient_data(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        (tmp_path / "stations.csv").write_text("only first line\n", encoding="utf-8")

        assert asyncio.run(csv_manager._parse_csv_data_from_file()) is False

    def test_parse_csv_data_from_file_missing_file(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "missing.csv")

        assert asyncio.run(csv_manager._parse_csv_data_from_file()) is False


class TestCSVCacheValidation:
    def test_builds_conditional_headers(self, csv_manager):
        csv_manager._csv_etag = '"abc123"'
        csv_manager._csv_last_modified = "Wed, 01 Jan 2025 00:00:00 GMT"

        headers = csv_manager._build_csv_request_headers(force_update=False)

        assert headers["If-None-Match"] == '"abc123"'
        assert headers["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"

    def test_force_update_skips_conditional_headers(self, csv_manager):
        csv_manager._csv_etag = '"abc123"'
        csv_manager._csv_last_modified = "Wed, 01 Jan 2025 00:00:00 GMT"

        headers = csv_manager._build_csv_request_headers(force_update=True)

        assert "If-None-Match" not in headers
        assert "If-Modified-Since" not in headers

    def test_loads_cache_http_metadata(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor

        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")

        cache_payload = {
            "stations": {"12345": {"id": "12345"}},
            "last_update": "2025-01-15T10:00:00+00:00",
            "version": "2.0",
            "csv_separator": "|",
            "csv_etag": '"abc123"',
            "csv_last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        }
        (tmp_path / "cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

        result = asyncio.run(csv_manager.async_load_cached_data())

        assert result is True
        assert csv_manager._csv_etag == '"abc123"'
        assert csv_manager._csv_last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"

    def test_load_cache_missing_file(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "missing.json")

        assert asyncio.run(csv_manager.async_load_cached_data()) is False

    def test_load_cache_invalid_json(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._stations_cache = {"existing": {"id": "existing"}}
        (tmp_path / "cache.json").write_text("{invalid", encoding="utf-8")

        assert asyncio.run(csv_manager.async_load_cached_data()) is False
        assert csv_manager._stations_cache == {"existing": {"id": "existing"}}

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"version": "2.0", "stations": []},
            {"version": 2, "stations": {}},
            {"version": "2.0", "stations": {}, "last_update": 1},
            {"version": "2.0", "stations": {}, "csv_separator": 1},
            {"version": "2.0", "stations": {}, "csv_etag": 1},
            {"version": "2.0", "stations": {}, "csv_last_modified": 1},
        ],
    )
    def test_load_cache_rejects_invalid_shapes_without_partial_state(self, tmp_path, payload):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._stations_cache = {"existing": {"id": "existing"}}
        csv_manager._detected_separator = ";"
        (tmp_path / "cache.json").write_text(json.dumps(payload), encoding="utf-8")

        assert asyncio.run(csv_manager.async_load_cached_data()) is False
        assert csv_manager._stations_cache == {"existing": {"id": "existing"}}
        assert csv_manager._detected_separator == ";"

    def test_load_cache_accepts_optional_metadata_from_older_document(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        (tmp_path / "cache.json").write_text(
            json.dumps({"version": "2.0", "stations": {"123": {"id": "123"}}}),
            encoding="utf-8",
        )

        assert asyncio.run(csv_manager.async_load_cached_data()) is True
        assert csv_manager._stations_cache == {"123": {"id": "123"}}
        assert csv_manager._detected_separator == "|"

    def test_load_cache_offloads_open_and_decode_together(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        (tmp_path / "cache.json").write_text(
            json.dumps({"version": "2.0", "stations": {}}), encoding="utf-8"
        )

        assert asyncio.run(csv_manager.async_load_cached_data()) is True
        assert hass.async_add_executor_job.call_args.args == (
            csv_module._load_json_file_sync,
            str(tmp_path / "cache.json"),
        )

    def test_parse_cached_datetime_variants(self, csv_manager, monkeypatch):
        fixed_now = datetime(2026, 6, 1, tzinfo=timezone.utc)

        def parse_datetime(value):
            if value == "needs-fallback":
                return None
            return datetime.fromisoformat(value)

        monkeypatch.setattr(csv_module.dt_util, "parse_datetime", parse_datetime)
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: fixed_now)

        assert csv_manager._parse_cached_datetime(None) is None
        assert csv_manager._parse_cached_datetime("bad") is None
        assert csv_manager._parse_cached_datetime("needs-fallback") is None
        parsed = csv_manager._parse_cached_datetime("2026-06-01T08:30:00")
        assert parsed == datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)

    def test_outdated_cache_does_not_replace_existing_memory_state(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor

        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._stations_cache = {"existing": {"id": "existing"}}
        csv_manager._detected_separator = ";"

        cache_payload = {
            "stations": {"12345": {"id": "12345"}},
            "last_update": "2025-01-15T10:00:00+00:00",
            "version": "1.0",
            "csv_separator": "|",
        }
        (tmp_path / "cache.json").write_text(json.dumps(cache_payload), encoding="utf-8")

        result = asyncio.run(csv_manager.async_load_cached_data())

        assert result is False
        assert csv_manager._stations_cache == {"existing": {"id": "existing"}}
        assert csv_manager._detected_separator == ";"

    def test_save_cached_data_preserves_station_and_http_metadata(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor

        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._stations_cache = {"12345": {"id": "12345", "name": "Station Alpha"}}
        csv_manager._last_update = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        csv_manager._detected_separator = ";"
        csv_manager._csv_etag = '"abc123"'
        csv_manager._csv_last_modified = "Wed, 01 Jan 2025 00:00:00 GMT"

        result = asyncio.run(csv_manager.async_save_cached_data())

        assert result is True
        saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
        assert saved["stations"] == {"12345": {"id": "12345", "name": "Station Alpha"}}
        assert saved["last_update"] == "2026-06-01T08:30:00+00:00"
        assert saved["version"] == "2.0"
        assert saved["csv_separator"] == ";"
        assert saved["csv_etag"] == '"abc123"'
        assert saved["csv_last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
        assert hass.async_add_executor_job.call_args.args[0] is csv_module._write_json_file_atomic_sync
        assert hass.async_add_executor_job.call_args.args[1] == str(tmp_path / "cache.json")

    def test_save_cached_data_handles_write_error(self, tmp_path, monkeypatch):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        monkeypatch.setattr(
            "custom_components.osservaprezzi_carburanti.csv_manager._write_json_file_atomic_sync",
            MagicMock(side_effect=OSError("nope")),
        )

        assert asyncio.run(csv_manager.async_save_cached_data()) is False

    @pytest.mark.parametrize("failure", [TypeError("encode"), OSError("write")])
    def test_atomic_cache_save_preserves_destination_on_json_failure(
        self, tmp_path, monkeypatch, failure
    ):
        destination = tmp_path / "cache.json"
        destination.write_bytes(b"old cache")
        monkeypatch.setattr(csv_module.json, "dump", MagicMock(side_effect=failure))

        with pytest.raises(type(failure)):
            csv_module._write_json_file_atomic_sync(str(destination), {"stations": {}})

        assert destination.read_bytes() == b"old cache"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_atomic_cache_save_preserves_destination_on_replace_failure(
        self, tmp_path, monkeypatch
    ):
        destination = tmp_path / "cache.json"
        destination.write_bytes(b"old cache")
        monkeypatch.setattr(csv_module.os, "replace", MagicMock(side_effect=OSError("replace")))

        with pytest.raises(OSError):
            csv_module._write_json_file_atomic_sync(str(destination), {"stations": {}})

        assert destination.read_bytes() == b"old cache"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_clear_cache_removes_files_and_resets_memory_metadata(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor

        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._stations_cache = {"12345": {"id": "12345"}}
        csv_manager._last_update = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        csv_manager._csv_etag = '"abc123"'
        csv_manager._csv_last_modified = "Wed, 01 Jan 2025 00:00:00 GMT"
        (tmp_path / "stations.csv").write_text("csv", encoding="utf-8")
        (tmp_path / "cache.json").write_text("{}", encoding="utf-8")

        result = asyncio.run(csv_manager.async_clear_cache())

        assert result is True
        assert csv_manager._stations_cache == {}
        assert csv_manager._last_update is None
        assert csv_manager._csv_etag is None
        assert csv_manager._csv_last_modified is None
        assert not (tmp_path / "stations.csv").exists()
        assert not (tmp_path / "cache.json").exists()

    def test_clear_cache_handles_remove_error(self, tmp_path):
        async def raise_remove(func, *args):
            if func is __import__("os").remove:
                raise OSError("nope")
            return func(*args)

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = raise_remove
        csv_manager = CSVStationManager(hass)
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        (tmp_path / "cache.json").write_text("{}", encoding="utf-8")

        assert asyncio.run(csv_manager.async_clear_cache()) is False

    def test_migrate_legacy_files_moves_missing_new_files(self, tmp_path):
        hass = MagicMock()

        def path(*parts):
            return str(tmp_path.joinpath(*parts))

        hass.config.path.side_effect = path
        hass.async_add_executor_job.side_effect = _run_in_executor
        old_csv = tmp_path / "osservaprezzi_stations.csv"
        old_cache = tmp_path / "osservaprezzi_cache.json"
        old_csv.write_text("csv", encoding="utf-8")
        old_cache.write_text("cache", encoding="utf-8")
        storage = tmp_path / ".storage"
        storage.mkdir()
        csv_manager = CSVStationManager(hass)

        asyncio.run(csv_manager._async_migrate_legacy_files())

        assert not old_csv.exists()
        assert not old_cache.exists()
        assert (storage / "osservaprezzi_carburanti_stations.csv").read_text(encoding="utf-8") == "csv"
        assert (storage / "osservaprezzi_carburanti_cache.json").read_text(encoding="utf-8") == "cache"

    def test_migrate_legacy_files_skips_existing_destination(self, tmp_path):
        hass = MagicMock()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        hass.async_add_executor_job.side_effect = _run_in_executor
        old_csv = tmp_path / "osservaprezzi_stations.csv"
        old_csv.write_text("old", encoding="utf-8")
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "osservaprezzi_carburanti_stations.csv").write_text("new", encoding="utf-8")
        csv_manager = CSVStationManager(hass)

        asyncio.run(csv_manager._async_migrate_legacy_files())

        assert old_csv.exists()
        assert (storage / "osservaprezzi_carburanti_stations.csv").read_text(encoding="utf-8") == "new"

    def test_migrate_legacy_files_ignores_move_error(self, tmp_path, monkeypatch):
        hass = MagicMock()
        hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))
        hass.async_add_executor_job.side_effect = _run_in_executor
        old_csv = tmp_path / "osservaprezzi_stations.csv"
        old_csv.write_text("old", encoding="utf-8")
        (tmp_path / ".storage").mkdir()
        csv_manager = CSVStationManager(hass)
        monkeypatch.setattr(csv_module.shutil, "move", MagicMock(side_effect=OSError("nope")))

        asyncio.run(csv_manager._async_migrate_legacy_files())

        assert old_csv.exists()

    def test_update_skips_recent_cache(self, csv_manager, monkeypatch):
        now = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        csv_manager._last_update = now - timedelta(hours=1)
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: now)

        assert asyncio.run(csv_manager.async_update_csv_data()) is True

    def test_update_handles_304(self, tmp_path, monkeypatch):
        now = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(FakeCSVResponse(status=304))
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: now)

        assert asyncio.run(csv_manager.async_update_csv_data()) is True
        assert csv_manager._last_update == now

    def test_update_304_returns_false_when_metadata_save_fails(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(FakeCSVResponse(status=304))
        csv_manager._async_save_cache_data = AsyncMock(return_value=False)

        assert asyncio.run(csv_manager.async_update_csv_data()) is False

    def test_load_cached_data_handles_missing_file(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "missing")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)

        assert asyncio.run(csv_manager.async_load_cached_data()) is False

    def test_update_304_ignores_response_after_generation_change(self, tmp_path):
        class ClearingSession(FakeCSVSession):
            def get(self, *args, **kwargs):
                csv_manager._cache_generation += 1
                return super().get(*args, **kwargs)

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager.session = ClearingSession(FakeCSVResponse(status=304))

        assert asyncio.run(csv_manager.async_update_csv_data()) is False

    def test_update_handles_http_error(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(FakeCSVResponse(status=500))

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is False

    def test_update_handles_parse_failure(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job = AsyncMock(return_value=(False, "|", {}))
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(FakeCSVResponse(status=200, text="bad"))

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is False

    def test_invalid_download_preserves_known_good_state(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        csv_manager._stations_cache = {"existing": {"id": "existing"}}
        csv_manager._detected_separator = ";"
        csv_manager._csv_etag = '"old"'
        csv_manager._csv_last_modified = "yesterday"
        original_content = "known-good-csv"
        (tmp_path / "stations.csv").write_text(original_content, encoding="utf-8")
        csv_manager.session = FakeCSVSession(
            FakeCSVResponse(
                status=200,
                text="<html>\n<body>Service unavailable</body>",
                headers={"ETag": '"new"', "Last-Modified": "today"},
            )
        )

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is False
        assert csv_manager._stations_cache == {"existing": {"id": "existing"}}
        assert csv_manager._detected_separator == ";"
        assert csv_manager._csv_etag == '"old"'
        assert csv_manager._csv_last_modified == "yesterday"
        assert (tmp_path / "stations.csv").read_text(encoding="utf-8") == original_content

    def test_update_discards_download_after_generation_change(self, tmp_path):
        async def parse_and_clear(func, *args):
            result = func(*args)
            csv_manager._cache_generation += 1
            return result

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = parse_and_clear
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(
            FakeCSVResponse(status=200, text="\n".join(PIPE_CSV_LINES))
        )

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is False

    def test_update_commits_downloaded_snapshot(self, tmp_path, monkeypatch):
        now_values = iter(
            [
                datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc),
                datetime(2026, 6, 1, 8, 31, tzinfo=timezone.utc),
            ]
        )

        async def parse_and_refresh(func, *args):
            result = func(*args)
            csv_manager._last_update = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
            return result

        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = parse_and_refresh
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(
            FakeCSVResponse(status=200, text="\n".join(PIPE_CSV_LINES))
        )
        csv_manager._async_write_csv_file = AsyncMock()
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: next(now_values))

        assert asyncio.run(csv_manager.async_update_csv_data()) is True
        csv_manager._async_write_csv_file.assert_awaited_once()

    def test_update_handles_client_error(self, tmp_path):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        csv_manager = CSVStationManager(hass)
        csv_manager.session = FakeCSVSession(aiohttp.ClientError("boom"))

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is False

    def test_write_csv_file_removes_temp_on_replace_error(self, tmp_path, monkeypatch):
        temp_path = tmp_path / "temp.tmp"
        hass = MagicMock(config=MagicMock(path=MagicMock(return_value=str(tmp_path / "x"))))
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        monkeypatch.setattr(csv_module, "_create_temp_file_sync", MagicMock(return_value=str(temp_path)))
        monkeypatch.setattr(csv_module, "_replace_file_sync", MagicMock(side_effect=OSError("nope")))

        with pytest.raises(OSError):
            asyncio.run(csv_manager._async_write_csv_file("content"))

        assert not temp_path.exists()

    def test_parse_csv_text_logs_bad_line_and_continues(self, csv_manager, monkeypatch):
        original = csv_manager._parse_station_values
        calls = 0

        def flaky_parse(values, indices):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("bad row")
            return original(values, indices)

        monkeypatch.setattr(csv_manager, "_parse_station_values", flaky_parse)
        content = "\n".join(PIPE_CSV_LINES[:2] + PIPE_CSV_LINES[2:4])

        success, _, stations = csv_manager._parse_csv_text_to_cache(content)

        assert success is True
        assert list(stations) == ["67890"]

    def test_parse_csv_file_logs_bad_line_and_continues(self, tmp_path, monkeypatch):
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        (tmp_path / "stations.csv").write_text("\n".join(PIPE_CSV_LINES[:4]), encoding="utf-8")
        original = csv_manager._parse_station_values
        calls = 0

        def flaky_parse(values, indices):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("bad row")
            return original(values, indices)

        monkeypatch.setattr(csv_manager, "_parse_station_values", flaky_parse)

        assert asyncio.run(csv_manager._parse_csv_data_from_file()) is True
        assert list(csv_manager._stations_cache) == ["67890"]

    def test_update_success_writes_cache_and_metadata(self, tmp_path, monkeypatch):
        now = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        csv_manager.session = FakeCSVSession(
            FakeCSVResponse(
                status=200,
                text="\n".join(PIPE_CSV_LINES),
                headers={"ETag": '"abc"', "Last-Modified": "today"},
            )
        )
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: now)

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is True
        assert csv_manager._csv_etag == '"abc"'
        assert csv_manager._csv_last_modified == "today"
        assert "12345" in csv_manager._stations_cache
        assert (tmp_path / "stations.csv").exists()

    def test_initialize_uses_recent_cache(self, csv_manager, monkeypatch):
        now = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        csv_manager._stations_cache = {"123": {"id": "123"}}
        csv_manager._last_update = now - timedelta(hours=1)
        csv_manager._async_migrate_legacy_files = AsyncMock()
        csv_manager._async_load_cached_data = AsyncMock(return_value=True)
        csv_manager._async_update_csv_data = AsyncMock()
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: now)

        assert asyncio.run(csv_manager.async_initialize()) is True
        csv_manager._async_update_csv_data.assert_not_awaited()

    def test_concurrent_initialize_runs_one_transaction(self, csv_manager):
        async def _exercise():
            started = asyncio.Event()
            release = asyncio.Event()

            async def initialize_once():
                started.set()
                await release.wait()
                return True

            csv_manager._async_initialize = AsyncMock(side_effect=initialize_once)
            first = asyncio.create_task(csv_manager.async_initialize())
            await started.wait()
            second = asyncio.create_task(csv_manager.async_initialize())
            await asyncio.sleep(0)
            assert not second.done()
            release.set()
            assert await asyncio.gather(first, second) == [True, True]
            csv_manager._async_initialize.assert_awaited_once()

        asyncio.run(_exercise())

    def test_initialize_updates_stale_cache(self, csv_manager, monkeypatch):
        monkeypatch.setattr(
            csv_module.dt_util,
            "now",
            lambda: datetime(2026, 6, 3, tzinfo=timezone.utc),
        )
        csv_manager._async_migrate_legacy_files = AsyncMock()
        csv_manager._async_load_cached_data = AsyncMock(return_value=True)
        csv_manager._last_update = datetime(2026, 6, 1, tzinfo=timezone.utc)
        csv_manager._stations_cache = {"123": {"id": "123"}}
        csv_manager._async_update_csv_data = AsyncMock(return_value=True)

        assert asyncio.run(csv_manager.async_initialize()) is True
        csv_manager._async_update_csv_data.assert_awaited_once_with(force_update=True)

    @pytest.mark.parametrize(
        ("cache_loaded", "last_update", "stations", "update_result"),
        [
            (False, None, {}, True),
            (True, None, {"123": {"id": "123"}}, True),
            (True, datetime(2026, 6, 1, tzinfo=timezone.utc), {}, False),
        ],
    )
    def test_initialize_forces_update_when_cache_unusable(
        self,
        csv_manager,
        monkeypatch,
        cache_loaded,
        last_update,
        stations,
        update_result,
    ):
        monkeypatch.setattr(
            csv_module.dt_util,
            "now",
            lambda: datetime(2026, 6, 2, tzinfo=timezone.utc),
        )
        csv_manager._async_migrate_legacy_files = AsyncMock()
        csv_manager._async_load_cached_data = AsyncMock(return_value=cache_loaded)
        csv_manager._last_update = last_update
        csv_manager._stations_cache = stations
        csv_manager._async_update_csv_data = AsyncMock(return_value=update_result)

        assert asyncio.run(csv_manager.async_initialize()) is update_result
        csv_manager._async_update_csv_data.assert_awaited_once_with(force_update=True)

    def test_periodic_update_propagates_update_result(self, csv_manager):
        csv_manager.async_update_csv_data = AsyncMock(return_value=True)
        assert asyncio.run(csv_manager.async_periodic_update()) is True

        csv_manager.async_update_csv_data = AsyncMock(return_value=False)
        assert asyncio.run(csv_manager.async_periodic_update()) is False

    def test_update_persistence_failure_preserves_memory_state(self, tmp_path, monkeypatch):
        now = datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc)
        hass = MagicMock()
        hass.config.path.return_value = str(tmp_path / "unused")
        hass.async_add_executor_job.side_effect = _run_in_executor
        csv_manager = CSVStationManager(hass)
        csv_manager._csv_path = str(tmp_path / "stations.csv")
        csv_manager._cache_path = str(tmp_path / "cache.json")
        csv_manager._stations_cache = {"existing": {"id": "existing"}}
        csv_manager._last_update = datetime(2026, 5, 1, tzinfo=timezone.utc)
        csv_manager.session = FakeCSVSession(
            FakeCSVResponse(status=200, text="\n".join(PIPE_CSV_LINES))
        )
        monkeypatch.setattr(csv_module.dt_util, "now", lambda: now)
        monkeypatch.setattr(
            csv_module, "_write_json_file_atomic_sync", MagicMock(side_effect=OSError("full"))
        )

        assert asyncio.run(csv_manager.async_update_csv_data(force_update=True)) is False
        assert csv_manager._stations_cache == {"existing": {"id": "existing"}}
        assert csv_manager._last_update == datetime(2026, 5, 1, tzinfo=timezone.utc)

    def test_public_transactions_serialize_while_downloading(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            csv_module.dt_util,
            "now",
            lambda: datetime(2026, 6, 1, 8, 30, tzinfo=timezone.utc),
        )

        async def _exercise():
            hass = MagicMock()
            hass.config.path.return_value = str(tmp_path / "unused")
            hass.async_add_executor_job.side_effect = _run_in_executor

            csv_manager = CSVStationManager(hass)
            csv_manager._csv_path = str(tmp_path / "stations.csv")
            csv_manager._cache_path = str(tmp_path / "cache.json")

            text_started = asyncio.Event()
            release_text = asyncio.Event()

            class FakeResponse:
                status = 200
                headers = {
                    "ETag": '"abc123"',
                    "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT",
                }

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, traceback):
                    return None

                async def text(self):
                    text_started.set()
                    await release_text.wait()
                    return "\n".join(PIPE_CSV_LINES)

            class FakeSession:
                def get(self, *args, **kwargs):
                    return FakeResponse()

            csv_manager.session = FakeSession()

            update_task = asyncio.create_task(csv_manager.async_update_csv_data(force_update=True))
            await asyncio.wait_for(text_started.wait(), timeout=1)

            load_task = asyncio.create_task(csv_manager.async_load_cached_data())
            await asyncio.sleep(0)
            assert not load_task.done()

            release_text.set()
            assert await update_task is True
            assert await load_task is True

        asyncio.run(_exercise())
