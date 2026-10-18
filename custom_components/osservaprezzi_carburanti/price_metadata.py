"""Derived price attributes shared by entities and action responses."""
from __future__ import annotations

import math
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    ATTR_PRICE_AGE_MINUTES,
    ATTR_PRICE_DELTA,
    ATTR_PRICE_DIRECTION,
    ATTR_PRICE_IS_STALE,
)


def _finite_price(value: Any) -> float | None:
    """Return a finite numeric price as float, if representable."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def price_metadata(
    *,
    price: Any,
    previous_price: Any,
    last_update: Any,
    stale_hours: int,
) -> dict[str, Any]:
    """Return derived, presentation-safe price metadata."""
    finite_price = _finite_price(price)
    finite_previous_price = _finite_price(previous_price)
    price_delta: float | None = None
    price_direction: str | None = None
    if finite_price is not None and finite_previous_price is not None:
        delta = finite_price - finite_previous_price
        direction_delta = delta
        if math.isfinite(delta):
            price_delta = round(delta, 3)
            direction_delta = price_delta
        if direction_delta > 0:
            price_direction = "up"
        elif direction_delta < 0:
            price_direction = "down"
        else:
            price_direction = "unchanged"
    elif finite_price is not None:
        price_direction = "new"

    age_minutes: int | None = None
    price_is_stale: bool | None = None
    if isinstance(last_update, str):
        parsed_update = dt_util.parse_datetime(last_update)
        if parsed_update is not None and parsed_update.tzinfo is not None:
            age_minutes = max(
                0,
                int((dt_util.now() - parsed_update).total_seconds() / 60),
            )
            price_is_stale = age_minutes > stale_hours * 60

    return {
        ATTR_PRICE_DELTA: price_delta,
        ATTR_PRICE_DIRECTION: price_direction,
        ATTR_PRICE_AGE_MINUTES: age_minutes,
        ATTR_PRICE_IS_STALE: price_is_stale,
    }
