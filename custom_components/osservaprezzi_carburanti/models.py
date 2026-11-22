"""Typed payload models used by the Osservaprezzi integration."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, NotRequired, Required, TypedDict

_MISSING: Any = object()
_STATION_FIELD_NAMES: tuple[str, ...] = (
    "id",
    "operator",
    "brand",
    "station_type",
    "name",
    "address",
    "municipality",
    "province",
    "latitude",
    "longitude",
)
_STATION_FIELDS: frozenset[str] = frozenset(_STATION_FIELD_NAMES)


@dataclass(frozen=True, slots=True, eq=False)
class StationRecord(Mapping[str, Any]):
    """Immutable station registry record."""

    id: Any = _MISSING
    operator: Any = _MISSING
    brand: Any = _MISSING
    station_type: Any = _MISSING
    name: Any = _MISSING
    address: Any = _MISSING
    municipality: Any = _MISSING
    province: Any = _MISSING
    latitude: Any = _MISSING
    longitude: Any = _MISSING
    _extra: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> StationRecord:
        """Create a record preserving unknown fields."""
        if mapping.keys() <= _STATION_FIELDS:
            return cls(**mapping)
        known: dict[str, Any] = {}
        extra: list[tuple[str, Any]] = []
        for key, value in mapping.items():
            if key in _STATION_FIELDS:
                known[key] = value
            else:
                extra.append((key, value))
        return cls(**known, _extra=tuple(extra))

    def __getitem__(self, key: str) -> Any:
        if key in _STATION_FIELDS:
            value = getattr(self, key)
            if value is not _MISSING:
                return value
            raise KeyError(key)
        for name, value in self._extra:
            if name == key:
                return value
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Fast O(1) lookup for known station slots."""
        if key in _STATION_FIELDS:
            value = getattr(self, key)
            return default if value is _MISSING else value
        for name, value in self._extra:
            if name == key:
                return value
        return default

    def _items(self) -> Iterator[tuple[str, Any]]:
        for field_name in _STATION_FIELD_NAMES:
            value = getattr(self, field_name)
            if value is not _MISSING:
                yield field_name, value
        yield from self._extra

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items())

    def __len__(self) -> int:
        return sum(1 for _ in self._items())

    def to_dict(self) -> dict[str, Any]:
        """Return the fields present in this record."""
        return dict(self._items())


class FuelPayload(TypedDict):
    """Fuel record returned by the station API."""

    name: Required[str]
    price: Required[int | float]
    fuelId: Required[object]
    isSelf: Required[bool]
    serviceAreaId: Required[object]
    insertDate: NotRequired[str | None]
    validityDate: NotRequired[str | None]


class StationPayload(TypedDict):
    """Normalized station payload returned by the API helper."""

    id: Required[int | str]
    name: Required[str]
    fuels: Required[list[FuelPayload]]
    services: Required[list[object]]
    orariapertura: Required[list[dict[str, object]]]
    nomeImpianto: NotRequired[str | None]
    address: NotRequired[str | None]
    brand: NotRequired[str | None]
    company: NotRequired[str | None]
    phoneNumber: NotRequired[str | None]
    email: NotRequired[str | None]
    website: NotRequired[str | None]


class ProcessedStationInfo(TypedDict, total=False):
    """Station information exposed to Home Assistant entities."""

    id: int | str
    name: str | None
    nomeImpianto: str | None
    address: str | None
    brand: str | None
    company: str | None
    phoneNumber: str | None
    email: str | None
    website: str | None
    latitude: float | None
    longitude: float | None
    operator: str | None
    station_type: str | None
    municipality: str | None
    province: str | None
    coordinate_source: str | None


class ProcessedFuel(TypedDict):
    """Fuel record enriched with normalized dates and price history."""

    price: int | float
    last_update: str | None
    validity_date: str | None
    fuel_id: object
    is_self: bool
    service_area_id: object
    previous_price: int | float | None
    price_changed_at: str | None
    price_history: NotRequired[list[dict[str, Any]]]


class ProcessedPayload(TypedDict):
    """Persisted and coordinator-ready station data."""

    station_info: ProcessedStationInfo
    fuels: dict[str, ProcessedFuel]
    services: list[object]
    opening_hours: list[dict[str, object]]
    last_update: str
