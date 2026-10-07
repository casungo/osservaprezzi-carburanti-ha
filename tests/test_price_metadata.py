"""Action and entity price metadata must use the same freshness rules."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.osservaprezzi_carburanti import price_metadata as metadata_module


@pytest.mark.parametrize("price,previous,delta,direction", [(1.8, 1.9, -0.1, "down"), (1.8, 1.8, 0, "unchanged"), (1.8, None, None, "new"), (None, 1.8, None, None), (True, 1.8, None, None)])
def test_price_direction_and_missing_history(price, previous, delta, direction):
    result = metadata_module.price_metadata(price=price, previous_price=previous, last_update=None, stale_hours=24)
    assert result["price_delta"] == delta
    assert result["price_direction"] == direction
    assert result["price_age_minutes"] is None
    assert result["price_is_stale"] is None


@pytest.mark.parametrize("date,age,stale", [("2026-10-05T07:00:00+00:00", 60, False), ("2026-10-04T07:00:00+00:00", 1500, True), ("2026-10-06T08:00:00+00:00", 0, False), ("invalid", None, None), ("2026-10-05T07:00:00", None, None)])
def test_price_freshness_and_invalid_timestamps(monkeypatch, date, age, stale):
    monkeypatch.setattr(metadata_module.dt_util, "now", lambda: datetime(2026, 10, 5, 8, tzinfo=timezone.utc))
    def parse(value):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    monkeypatch.setattr(metadata_module.dt_util, "parse_datetime", parse)
    result = metadata_module.price_metadata(price=1.8, previous_price=1.7, last_update=date, stale_hours=24)
    assert result["price_age_minutes"] == age
    assert result["price_is_stale"] == stale
