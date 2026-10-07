"""Measure registry snapshot copying and cache encoding on synthetic station data."""
from __future__ import annotations

import argparse
import json
import statistics
import time

from custom_components.osservaprezzi_carburanti.csv_manager import _build_registry_snapshot


def measure(operation, repeats: int) -> tuple[float, object]:
    """Return median elapsed milliseconds and the last result."""
    timings = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings), result


def main() -> None:
    """Print reproducible cache and snapshot measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    stations = {
        str(index): {"id": str(index), "name": f"Stazione {index}", "brand": "Marchio", "operator": "Gestore", "station_type": "Stradale", "address": f"Via Roma {index}", "municipality": "Roma", "province": "RM", "latitude": 41.9, "longitude": 12.5}
        for index in range(args.stations)
    }
    cache = {"version": "2.0", "stations": stations, "last_update": "2026-10-05T08:30:00+02:00"}
    copy_ms, snapshot = measure(lambda: _build_registry_snapshot(stations), args.repeats)
    reuse_ms, _ = measure(lambda: snapshot, args.repeats)
    pretty_ms, pretty = measure(lambda: json.dumps(cache, ensure_ascii=False, indent=2), args.repeats)
    compact_ms, compact = measure(lambda: json.dumps(cache, ensure_ascii=False, separators=(",", ":")), args.repeats)
    assert json.loads(pretty) == json.loads(compact)
    print(json.dumps({"stations": args.stations, "repeats": args.repeats, "snapshot_copy_ms": round(copy_ms, 3), "snapshot_reuse_ms": round(reuse_ms, 6), "pretty_json_ms": round(pretty_ms, 3), "compact_json_ms": round(compact_ms, 3), "pretty_bytes": len(pretty.encode()), "compact_bytes": len(compact.encode())}, indent=2))


if __name__ == "__main__":
    main()
