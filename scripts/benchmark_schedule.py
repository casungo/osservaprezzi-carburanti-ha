"""Compare schedule recomputation with cached next-change property reads."""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID
from custom_components.osservaprezzi_carburanti.sensor import StationNextChangeSensor


def main() -> None:
    """Measure 1,000 property-read pairs across nine repetitions."""
    coordinator = SimpleNamespace(hass=None, data={"opening_hours": [
        {"giornoSettimanaId": day, "oraAperturaMattina": "08:00", "oraChiusuraMattina": "12:00", "oraAperturaPomeriggio": "15:00", "oraChiusuraPomeriggio": "19:00"}
        for day in range(1, 8)
    ]})
    entity = StationNextChangeSensor(coordinator, SimpleNamespace(data={CONF_STATION_ID: "1"}))
    now = datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Europe/Rome"))
    entity._refresh_next_change(now)
    assert entity._next_change == entity._compute_next_change(now)
    timings = {}
    for label, operation in {
        "recompute": lambda: (entity._compute_next_change(now), entity._compute_next_change(now)),
        "cached": lambda: (entity.native_value, entity.extra_state_attributes),
    }.items():
        samples = []
        for _ in range(9):
            started = time.perf_counter()
            for _ in range(1000):
                operation()
            samples.append((time.perf_counter() - started) * 1000)
        timings[label] = round(statistics.median(samples), 3)
    print(json.dumps({"read_pairs": 1000, "repeats": 9, "median_ms": timings}, indent=2))


if __name__ == "__main__":
    main()
