"""Small pure helpers shared by station processing and presentation."""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any


def as_coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    """Validate a numeric coordinate without accepting booleans or nonfinite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not minimum <= value <= maximum:
        return None
    coordinate = float(value)
    return coordinate if isfinite(coordinate) else None


def parse_coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    """Convert a CSV coordinate, then apply the shared numeric validation."""
    if isinstance(value, bool):
        return None
    try:
        coordinate = float(value.replace(",", ".") if isinstance(value, str) else value)
    except (TypeError, ValueError, OverflowError):
        return None
    return as_coordinate(coordinate, minimum, maximum)


def station_display_name(station: Mapping[str, Any], fallback: str = "") -> str:
    """Choose a public station name with a caller-supplied fallback."""
    for key in ("nomeImpianto", "name"):
        value = station.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def fuel_display_name(name: str) -> str:
    """Format fuel labels while preserving common acronyms."""
    acronyms = {"GPL", "GNL", "LNG", "CNG", "HVO", "E5", "E10", "B7", "B10"}
    return " ".join(
        word.upper() if word.upper() in acronyms else word.title()
        for word in name.replace("_", " ").split()
    )
