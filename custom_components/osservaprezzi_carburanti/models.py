"""Typed payload models used by the Osservaprezzi integration."""
from __future__ import annotations

from typing import NotRequired, Required, TypedDict


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


class ProcessedPayload(TypedDict):
    """Persisted and coordinator-ready station data."""

    station_info: ProcessedStationInfo
    fuels: dict[str, ProcessedFuel]
    services: list[object]
    opening_hours: list[dict[str, object]]
    last_update: str
