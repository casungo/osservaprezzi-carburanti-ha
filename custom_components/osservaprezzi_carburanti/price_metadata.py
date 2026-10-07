"""Derived price attributes shared by entities and action responses."""
from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .const import ATTR_PRICE_DELTA, ATTR_PRICE_DIRECTION, ATTR_PRICE_AGE_MINUTES, ATTR_PRICE_IS_STALE

def price_metadata(
    *,
    price: Any,
    previous_price: Any,
    last_update: Any,
    stale_hours: int,
) -> dict[str, Any]:
    """Return derived, presentation-safe price metadata."""
    price_is_number = isinstance(price, (int, float)) and not isinstance(price, bool)
    previous_is_number = isinstance(previous_price, (int, float)) and not isinstance(
        previous_price, bool
    )
    price_delta: float | None = None
    price_direction: str | None = None
    if price_is_number and previous_is_number:
        price_delta = round(float(price) - float(previous_price), 3)
        if price_delta > 0:
            price_direction = "up"
        elif price_delta < 0:
            price_direction = "down"
        else:
            price_direction = "unchanged"
    elif price_is_number:
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

