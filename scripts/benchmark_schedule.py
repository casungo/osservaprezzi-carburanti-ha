"""Compare schedule recomputation with cached next-change property reads."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from custom_components.osservaprezzi_carburanti.const import CONF_STATION_ID
from custom_components.osservaprezzi_carburanti.sensor import StationNextChangeSensor


def main() -> None:
    """Compare recomputed and cached properties with alternating samples."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-pairs", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    coordinator = SimpleNamespace(hass=None, data={"opening_hours": [
        {"giornoSettimanaId": day, "oraAperturaMattina": "08:00", "oraChiusuraMattina": "12:00", "oraAperturaPomeriggio": "15:00", "oraChiusuraPomeriggio": "19:00"}
        for day in range(1, 8)
    ]})
    entity = StationNextChangeSensor(coordinator, SimpleNamespace(data={CONF_STATION_ID: "1"}))
    now = datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Europe/Rome"))
    entity._refresh_next_change(now)
    assert entity._next_change == entity._compute_next_change(now)
    operations = {
        "recompute": lambda: (entity._compute_next_change(now), entity._compute_next_change(now)),
        "cached": lambda: (entity.native_value, entity.extra_state_attributes),
    }
    timings: dict[str, list[float]] = {name: [] for name in operations}
    for operation in operations.values():
        operation()
    for index in range(args.repeats):
        order = list(operations.items())
        if index % 2:
            order.reverse()
        for label, operation in order:
            started = time.perf_counter()
            for _ in range(args.read_pairs):
                operation()
            timings[label].append((time.perf_counter() - started) * 1000)

    recompute_ms = statistics.median(timings["recompute"])
    cached_ms = statistics.median(timings["cached"])
    print(
        json.dumps(
            {
                "read_pairs": args.read_pairs,
                "repeats": args.repeats,
                "order": "alternating",
                "median_ms": {
                    "recompute": round(recompute_ms, 3),
                    "cached": round(cached_ms, 3),
                },
                "range_ms": {
                    name: [round(min(samples), 3), round(max(samples), 3)]
                    for name, samples in timings.items()
                },
                "speedup": round(recompute_ms / cached_ms, 1),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
