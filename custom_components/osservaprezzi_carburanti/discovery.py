"""Pure helpers for discovering nearby fuel stations."""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from typing import Any

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class StationCandidate:
    """A station that matched a nearby search."""

    station_id: str
    name: str
    brand: str | None
    address: str | None
    municipality: str | None
    province: str | None
    station_type: str | None
    distance_km: float | None = None


def _as_coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    """Return a valid coordinate or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    coordinate = float(value)
    if not minimum <= coordinate <= maximum:
        return None
    return coordinate


def _haversine_distance_km(
    latitude: float,
    longitude: float,
    station_latitude: float,
    station_longitude: float,
) -> float:
    """Calculate the great-circle distance between two points."""
    latitude_delta = radians(station_latitude - latitude)
    longitude_delta = radians(station_longitude - longitude)
    origin_latitude = radians(latitude)
    destination_latitude = radians(station_latitude)

    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(origin_latitude)
        * cos(destination_latitude)
        * sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(haversine))


def _station_sort_id(station_id: str) -> tuple[int, int | str]:
    """Sort numeric station IDs numerically and other IDs lexicographically."""
    if station_id.isdigit():
        return 0, int(station_id)
    return 1, station_id.casefold()


def _normalize_text(value: Any) -> str:
    """Normalize text for accent-insensitive local matching."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        character for character in text if not unicodedata.combining(character)
    ).casefold()


def _station_matches_filters(
    station: Mapping[str, Any],
    *,
    text_filter: str | None,
    station_type: str | None,
) -> bool:
    """Return whether a station matches optional registry filters."""
    if text_filter:
        needle = _normalize_text(text_filter).strip()
        searchable = " ".join(
            _normalize_text(station.get(field))
            for field in (
                "name",
                "brand",
                "address",
                "operator",
                "municipality",
                "province",
            )
        )
        if needle not in searchable:
            return False

    if station_type:
        expected_type = _normalize_text(station_type).strip()
        if expected_type not in _normalize_text(station.get("station_type")):
            return False
    return True


def _candidate_from_station(
    station: Mapping[str, Any],
    *,
    distance_km: float | None,
) -> StationCandidate | None:
    """Build a candidate from a valid registry station."""
    station_id_value = station.get("id")
    if station_id_value is None:
        return None
    station_id = str(station_id_value).strip()
    if not station_id:
        return None

    name_value = station.get("name")
    name = str(name_value).strip() if name_value else station_id
    return StationCandidate(
        station_id=station_id,
        name=name,
        brand=_optional_text(station.get("brand")),
        address=_optional_text(station.get("address")),
        municipality=_optional_text(station.get("municipality")),
        province=_optional_text(station.get("province")),
        station_type=_optional_text(station.get("station_type")),
        distance_km=distance_km,
    )


def find_nearby_stations(
    stations: Iterable[Mapping[str, Any]],
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    limit: int,
    text_filter: str | None = None,
    station_type: str | None = None,
) -> tuple[StationCandidate, ...]:
    """Return a deterministic list of stations inside the requested radius."""
    origin_latitude = _as_coordinate(latitude, -90, 90)
    origin_longitude = _as_coordinate(longitude, -180, 180)
    if (
        origin_latitude is None
        or origin_longitude is None
        or isinstance(radius_km, bool)
        or radius_km <= 0
        or limit <= 0
    ):
        return ()

    candidates: list[StationCandidate] = []
    for station in stations:
        if not _station_matches_filters(
            station,
            text_filter=text_filter,
            station_type=station_type,
        ):
            continue
        station_latitude = _as_coordinate(station.get("latitude"), -90, 90)
        station_longitude = _as_coordinate(station.get("longitude"), -180, 180)
        if station_latitude is None or station_longitude is None:
            continue

        distance_km = _haversine_distance_km(
            origin_latitude,
            origin_longitude,
            station_latitude,
            station_longitude,
        )
        if distance_km > radius_km:
            continue

        candidate = _candidate_from_station(station, distance_km=distance_km)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(
        key=lambda station: (
            station.distance_km if station.distance_km is not None else float("inf"),
            station.name.casefold(),
            _station_sort_id(station.station_id),
        )
    )
    return tuple(candidates[:limit])


def find_stations_by_area(
    stations: Iterable[Mapping[str, Any]],
    *,
    municipality: str,
    province: str | None = None,
    text_filter: str | None = None,
    station_type: str | None = None,
    limit: int,
) -> tuple[StationCandidate, ...]:
    """Return stations matching a municipality and optional province."""
    municipality_filter = _normalize_text(municipality).strip()
    province_filter = _normalize_text(province).strip()
    if (
        limit <= 0
        or not any(
            (
                municipality_filter,
                province_filter,
                _normalize_text(text_filter).strip(),
                _normalize_text(station_type).strip(),
            )
        )
    ):
        return ()

    candidates: list[StationCandidate] = []
    for station in stations:
        station_municipality = _normalize_text(station.get("municipality"))
        station_province = _normalize_text(station.get("province"))
        if municipality_filter and municipality_filter not in station_municipality:
            continue
        if province_filter and province_filter not in station_province:
            continue
        if not _station_matches_filters(
            station,
            text_filter=text_filter,
            station_type=station_type,
        ):
            continue
        candidate = _candidate_from_station(station, distance_km=None)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(
        key=lambda station: (
            station.name.casefold(),
            _station_sort_id(station.station_id),
        )
    )
    return tuple(candidates[:limit])


def _optional_text(value: Any) -> str | None:
    """Normalize an optional text value."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
