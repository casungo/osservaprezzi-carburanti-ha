"""Tests for local nearby-station discovery."""
from __future__ import annotations

import pytest

from custom_components.osservaprezzi_carburanti.discovery import (
    find_nearby_stations,
    find_stations_by_area,
)


def test_find_nearby_stations_distance_matches_geodesic_ground_truth() -> None:
    candidates = find_nearby_stations(
        [
            {"id": "1", "name": "One degree north", "latitude": 42.9, "longitude": 12.5},
            {"id": "2", "name": "Half degree north", "latitude": 42.4, "longitude": 12.5},
        ],
        latitude=41.9,
        longitude=12.5,
        radius_km=200,
        limit=20,
    )

    by_id = {candidate.station_id: candidate for candidate in candidates}
    assert by_id["1"].distance_km == pytest.approx(111.19, abs=0.5)
    assert by_id["2"].distance_km == pytest.approx(55.60, abs=0.5)


def test_find_nearby_stations_excludes_station_just_outside_radius() -> None:
    candidates = find_nearby_stations(
        [{"id": "1", "name": "Far", "latitude": 42.4, "longitude": 12.5}],
        latitude=41.9,
        longitude=12.5,
        radius_km=50,
        limit=20,
    )

    assert candidates == ()


def test_find_nearby_stations_filters_sorts_and_limits() -> None:
    stations = [
        {
            "id": "20",
            "name": "Far",
            "latitude": 41.92,
            "longitude": 12.50,
        },
        {
            "id": "10",
            "name": "Near B",
            "latitude": 41.9005,
            "longitude": 12.50,
        },
        {
            "id": "2",
            "name": "Near A",
            "latitude": 41.9005,
            "longitude": 12.50,
        },
        {
            "id": "outside",
            "name": "Outside",
            "latitude": 42.5,
            "longitude": 12.5,
        },
        {
            "id": None,
            "name": "Missing ID",
            "latitude": 41.9,
            "longitude": 12.5,
        },
    ]

    candidates = find_nearby_stations(
        stations,
        latitude=41.9,
        longitude=12.5,
        radius_km=5,
        limit=2,
    )

    assert [candidate.station_id for candidate in candidates] == ["2", "10"]
    assert all(candidate.distance_km < 0.1 for candidate in candidates)


def test_find_nearby_stations_sorts_non_numeric_ids() -> None:
    candidates = find_nearby_stations(
        [
            {"id": "beta", "name": "Same", "latitude": 41.9, "longitude": 12.5},
            {"id": "alpha", "name": "Same", "latitude": 41.9, "longitude": 12.5},
        ],
        latitude=41.9,
        longitude=12.5,
        radius_km=2,
        limit=20,
    )

    assert [candidate.station_id for candidate in candidates] == ["alpha", "beta"]


def test_find_nearby_stations_preserves_display_metadata() -> None:
    candidates = find_nearby_stations(
        [
            {
                "id": "123",
                "name": "Station",
                "brand": "Brand",
                "address": "Via Roma 1",
                "municipality": "Roma",
                "province": "RM",
                "station_type": "Stradale",
                "latitude": 41.9,
                "longitude": 12.5,
            }
        ],
        latitude=41.9,
        longitude=12.5,
        radius_km=2,
        limit=20,
    )

    assert len(candidates) == 1
    assert candidates[0].brand == "Brand"
    assert candidates[0].address == "Via Roma 1"
    assert candidates[0].municipality == "Roma"
    assert candidates[0].province == "RM"
    assert candidates[0].station_type == "Stradale"
    assert candidates[0].distance_km == 0


def test_find_nearby_stations_rejects_invalid_inputs_and_rows() -> None:
    invalid_rows = [
        {"id": "missing-coordinates"},
        {"id": "", "latitude": 41.9, "longitude": 12.5},
        {"id": "bad-latitude", "latitude": 100, "longitude": 12.5},
        {"id": "boolean", "latitude": True, "longitude": 12.5},
    ]

    assert (
        find_nearby_stations(
            invalid_rows,
            latitude=41.9,
            longitude=12.5,
            radius_km=5,
            limit=20,
        )
        == ()
    )
    assert (
        find_nearby_stations(
            (),
            latitude=91,
            longitude=12.5,
            radius_km=5,
            limit=20,
        )
        == ()
    )
    assert (
        find_nearby_stations(
            (),
            latitude=41.9,
            longitude=12.5,
            radius_km=0,
            limit=20,
        )
        == ()
    )
    assert (
        find_nearby_stations(
            (),
            latitude=41.9,
            longitude=181,
            radius_km=5,
            limit=20,
        )
        == ()
    )
    assert (
        find_nearby_stations(
            (),
            latitude=41.9,
            longitude=12.5,
            radius_km=5,
            limit=0,
        )
        == ()
    )


def test_nearby_filters_are_case_and_accent_insensitive() -> None:
    stations = [
        {
            "id": "1",
            "name": "Stazione Città",
            "operator": "Gestore Élite",
            "station_type": "Autostradale",
            "latitude": 41.9,
            "longitude": 12.5,
        },
        {
            "id": "2",
            "name": "Altro",
            "station_type": "Stradale",
            "latitude": 41.9,
            "longitude": 12.5,
        },
    ]

    candidates = find_nearby_stations(
        stations,
        latitude=41.9,
        longitude=12.5,
        radius_km=2,
        limit=20,
        text_filter="elite",
        station_type="AUTOSTRADA",
    )

    assert [candidate.station_id for candidate in candidates] == ["1"]


def test_find_stations_by_area_filters_sorts_and_limits() -> None:
    stations = [
        {
            "id": "20",
            "name": "Zeta",
            "brand": "Blu",
            "municipality": "Città di Castello",
            "province": "Perugia",
            "station_type": "Stradale",
        },
        {
            "id": "2",
            "name": "Alfa",
            "address": "Via Blu 1",
            "municipality": "Citta di Castello",
            "province": "PG",
            "station_type": "Stradale",
        },
        {
            "id": "3",
            "name": "Excluded",
            "municipality": "Perugia",
            "province": "PG",
            "station_type": "Autostradale",
        },
        {
            "id": "4",
            "name": "Wrong province",
            "brand": "Blu",
            "municipality": "Citta di Castello",
            "province": "TR",
            "station_type": "Stradale",
        },
        {
            "id": "5",
            "name": "Wrong text",
            "municipality": "Citta di Castello",
            "province": "PG",
            "station_type": "Stradale",
        },
        {
            "id": "6",
            "name": "Blu wrong type",
            "municipality": "Citta di Castello",
            "province": "PG",
            "station_type": "Impianto nautico",
        },
    ]

    candidates = find_stations_by_area(
        stations,
        municipality="citta di castello",
        province="PG",
        text_filter="blu",
        station_type="strada",
        limit=1,
    )

    assert [candidate.station_id for candidate in candidates] == ["2"]
    assert candidates[0].distance_km is None


def test_find_stations_by_area_supports_province_and_non_numeric_ids() -> None:
    candidates = find_stations_by_area(
        [
            {
                "id": "beta",
                "name": "Same",
                "municipality": "Roma",
                "province": "RM",
            },
            {
                "id": "alpha",
                "name": "Same",
                "municipality": "Roma",
                "province": "RM",
            },
            {
                "id": None,
                "name": "Missing",
                "municipality": "Roma",
                "province": "RM",
            },
        ],
        municipality="",
        province="rm",
        limit=20,
    )

    assert [candidate.station_id for candidate in candidates] == ["alpha", "beta"]


def test_find_stations_by_area_requires_a_filter_and_valid_limit() -> None:
    assert find_stations_by_area([], municipality="", limit=20) == ()
    assert find_stations_by_area([], municipality="Roma", limit=0) == ()


def test_area_candidate_uses_id_as_missing_name_and_normalizes_empty_metadata() -> None:
    candidates = find_stations_by_area(
        [
            {
                "id": 42,
                "name": "",
                "brand": "",
                "municipality": "Roma",
            }
        ],
        municipality="Roma",
        limit=20,
    )

    assert candidates[0].name == "42"
    assert candidates[0].brand is None
