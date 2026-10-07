# HA duration measurements

`scripts/soak_release.py` observes two frozen integration versions in separate real
Home Assistant containers for 24 hours, after a ten-minute warmup. Both use the same
pinned HA image and the same captured MIMIT registry and four station responses.
No live Home Assistant configuration is mounted and no ports are published.

The duration starts after Home Assistant has reached its running state, all entries
have loaded, and warmup has finished. The probe is a HA background task, so it does
not hold startup open or prevent Recorder from committing during the observation.

## Run and supervise

Reuse a retained, hash-verified input snapshot. Pull the image specified by
`PINNED_IMAGE` in the runner if necessary. Output directories must be new.

```bash
make soak SOAK_ARGS='--inputs artifacts/kpi/smoke-20261007/inputs --output artifacts/kpi/soak-24h'
```

For an observation that outlives the terminal, start the runner in a user service:

```bash
systemd-run --user --unit=osservaprezzi-soak \
  --property=RuntimeMaxSec=26h --property=TimeoutStopSec=120 \
  --working-directory="$PWD" \
  "$PWD/.venv/bin/python" "$PWD/scripts/soak_release.py" \
  --inputs "$PWD/artifacts/kpi/smoke-20261007/inputs" \
  --output "$PWD/artifacts/kpi/soak-24h"

systemctl --user status osservaprezzi-soak
journalctl --user -u osservaprezzi-soak
```

The runner owns only its uniquely named containers. It stops them, records exit
codes and OOM status, saves logs, removes them, and writes `report.json` and
`report.md`. On SIGTERM it attempts the same cleanup. The host must remain running
throughout the observation. A reboot or interruption is not a completed 24-hour run.

## Workload and evidence

- Real cron callbacks every five minutes, using the integration's scheduler and
  production HTTP pacing. Callback deadline, firing delay and completion time are
  retained separately from HTTP response success.
- One entry reload every two hours, rotating through the four stations. Existing
  entity IDs, coordinator listeners and integration timer counts must remain stable.
- HTTP 503 interruptions at one hour and twelve hours. Forced refreshes exercise
  all configured stations with the original 30/60/120-second retry delays. The probe
  verifies cached prices remain correct, restores successful HTTP responses, and
  measures recovery time through network success and valid fuel state. On 2.6.0,
  which has no cache-fallback flag, fallback is established from failed HTTP
  attempts, a successful coordinator update and preserved valid prices.
- Whole-process RSS, CPU time, committed Recorder rows per unique ID, database and
  WAL file size every minute. Fuel correctness every second and a 100-ms event-loop
  heartbeat. Reload interruptions are recorded separately from steady-state and
  fault observations.
- Complete unload at the end, checking observed integration timers, surviving
  coordinator listeners and unfinished tasks whose names contain the domain.

`status.json` records the owned containers, source commits and hashes, frozen probe
hashes, image ID, input provenance and latest progress. Each profile writes
`soak-progress.json` and append-only `soak-samples.jsonl`. A failed run retains its
checkpoint evidence. Full profiles contain HA-generated storage and should remain
local; share the reports, selected samples and sanitized logs instead.

Final Recorder counts are read after shutdown and normalized over unique IDs shared
by both versions. New entities also contribute to the separately retained totals.
RSS slope uses only steady-state observations after the first hour and requires at
least six additional observed hours. Short verification runs return no slope.

Correctness failures, missing cron callbacks, failed fault recovery, timer/listener
changes after reload, HA log tracebacks and unclean shutdown fail the runner. Timing and memory slopes
are observations requiring investigation and repetition, without arbitrary release
thresholds. RSS includes HA and probe overhead. Two concurrent containers experience
host contention; small differences require another run with reversed launch order or
serial observations. This first runner launches baseline then candidate.

These are duration measurements with HTTP replay and unchanged captured prices.
They measure actual Recorder growth for that workload, not live MIMIT availability,
price propagation, physical disk writes, or a varied production workload. Sampling
can miss fuel interruptions shorter than one second and memory peaks between samples.
Only integration timers and named tasks covered by the probe are checked on unload.
The 24-hour run does not cover scalability to 10/50 stations or every class of fault.

## Short validation

A cron/reload verification without faults can run in under three minutes including
startup:

```bash
.venv/bin/python scripts/soak_release.py \
  --inputs artifacts/kpi/smoke-20261007/inputs \
  --output artifacts/kpi/soak-smoke \
  --duration 95 --warmup 2 --sample-seconds 10 --reload-seconds 40 \
  --no-faults --cron '* * * * *'
```

For two faults with unchanged production retry delays, allow at least eleven minutes:

```bash
.venv/bin/python scripts/soak_release.py \
  --inputs artifacts/kpi/smoke-20261007/inputs \
  --output artifacts/kpi/soak-fault-smoke \
  --duration 650 --warmup 0 --sample-seconds 10 --reload-seconds 140 \
  --fault-first 40 --fault-second 330 --cron '* * * * *'
```

A short run verifies instrumentation and guardrails. It does not establish long-term
memory behavior or daily Recorder growth.

The eight-minute 2026-10-07 comparison passed the two planned outage and recovery cycles for both
versions. See the [summary](benchmarks/2026-10-08-ha-duration-v2.6.0-v2.7.0-beta.1.md) and the
adjacent JSON for raw samples. It has no long-term RSS slope.
