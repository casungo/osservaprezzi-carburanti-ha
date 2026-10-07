# HA duration comparison

Two concurrent real HA processes with captured HTTP replay. Whole-process RSS includes probe overhead. Fixed upstream prices; no live price freshness, MIMIT uptime, or production disk-write claim.

| Version | Observed hours | RSS first/last MiB | RSS MiB/hour after first hour | Cron callbacks | p95 lateness ms | Common Recorder rows | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| 2.6.0 | 0.133 | 317.0390625 / 320.953125 | None | 8 | 1.3446807861328125 | 302 | completed |
| 2.7.0-beta.1 | 0.133 | 317.15234375 / 315.43359375 | None | 8 | 1.367807388305664 | 302 | completed |

Failures for baseline: []

Failures for candidate: []

Raw samples, fault recovery, reload durations and resource cleanup are in report.json.
RSS slope is descriptive, not proof of a leak. Short runs have no slope. Thresholds require repeated runs.
