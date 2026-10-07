"""Compare JSON cache encodings on a synthetic station registry."""
from __future__ import annotations

import argparse
import json
import statistics
import time


def main() -> None:
    """Print paired cache-encoding measurements and their output sizes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    stations = {
        str(index): {
            "id": str(index),
            "name": f"Stazione {index}",
            "brand": "Marchio",
            "operator": "Gestore",
            "station_type": "Stradale",
            "address": f"Via Roma {index}",
            "municipality": "Roma",
            "province": "RM",
            "latitude": 41.9,
            "longitude": 12.5,
        }
        for index in range(args.stations)
    }
    cache = {"version": "2.0", "stations": stations, "last_update": "2026-10-05T08:30:00+02:00"}
    encodings = {
        "formatted": lambda: json.dumps(cache, ensure_ascii=False, indent=2),
        "compact": lambda: json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
    }
    timings: dict[str, list[float]] = {name: [] for name in encodings}
    encoded: dict[str, str] = {}
    for operation in encodings.values():
        operation()  # Warm both encoders before collecting samples.
    for index in range(args.repeats):
        order = list(encodings.items())
        if index % 2:
            order.reverse()
        for name, operation in order:
            started = time.perf_counter()
            encoded[name] = operation()
            timings[name].append((time.perf_counter() - started) * 1000)

    assert json.loads(encoded["formatted"]) == json.loads(encoded["compact"])
    formatted_ms = statistics.median(timings["formatted"])
    compact_ms = statistics.median(timings["compact"])
    formatted_bytes = len(encoded["formatted"].encode())
    compact_bytes = len(encoded["compact"].encode())
    print(
        json.dumps(
            {
                "stations": args.stations,
                "repeats": args.repeats,
                "order": "alternating",
                "median_ms": {
                    "formatted": round(formatted_ms, 3),
                    "compact": round(compact_ms, 3),
                },
                "range_ms": {
                    "formatted": [
                        round(min(timings["formatted"]), 3),
                        round(max(timings["formatted"]), 3),
                    ],
                    "compact": [
                        round(min(timings["compact"]), 3),
                        round(max(timings["compact"]), 3),
                    ],
                },
                "speedup_percent": round((1 - compact_ms / formatted_ms) * 100, 1),
                "bytes": {"formatted": formatted_bytes, "compact": compact_bytes},
                "byte_reduction_percent": round(
                    (1 - compact_bytes / formatted_bytes) * 100, 1
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
