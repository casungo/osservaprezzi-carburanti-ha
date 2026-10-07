# Release KPI monitoring

`make kpi` compares the current integration with the nearest stable Git tag before
`HEAD`. The runner resolves that tag once and records its commit. An explicit baseline
avoids ambiguity when comparing a historical release:

```bash
make kpi KPI_BASELINE=v2.6.0
```

The `release-kpis` CI job runs on pushes, pull requests and manual dispatch. It saves
reports, input snapshots and container logs for 90 days, and adds the comparison to
the workflow summary. Correctness failures fail the
job. Performance flags require investigation and appear in the report; they do not
fail CI until thresholds have been calibrated. No changes are pushed by the runner.

## Reproducible execution

The runner uses Python's standard library and a running Docker daemon. Pull the
measurement image if it is not already installed:

```bash
docker pull ghcr.io/home-assistant/home-assistant:stable
.venv/bin/python scripts/benchmark_release.py \
  --baseline v2.6.0 \
  --candidate working-tree \
  --output artifacts/kpi/2.6-vs-2.7
```

It records the image ID and repository digest, then uses the same image ID throughout
all runs. Defaults are two CPUs, 1 GiB RAM, four real stations, ten repetitions per
operation, five reloads and sixty idle seconds. Runs alternate baseline/candidate and
candidate/baseline. Each run gets an empty HA profile; the final upgrade run clones the
first baseline profile and replaces only the integration source and measurement probe.
Restored entries are temporarily disabled before boot so the probe can install replay
endpoints before enabling and loading them.

MIMIT's real national registry and station responses are downloaded once. Their
capture time, URLs, HTTP metadata and SHA-256 hashes are retained. Both versions receive
those same responses from an HTTP server on loopback inside HA. Production request
pacing remains enabled. Conditional CSV requests receive a controlled `304` response
when the integration sends the replay ETag. This measures its conditional-update
behavior, not how often the real upstream produces a `304`.

HA config flows, platforms, state machine, executor, HTTP session, SQLite Recorder,
atomic cache writes, reload and unload all run in the actual HA container. No ports are
published and no live HA configuration is mounted. MIMIT hostnames are mapped to
loopback as an additional guard against accidental live requests from the replay run.
Source files, the runner and the probe are snapshotted before measurement, so edits during a run
do not change the measured versions.

Reuse the exact inputs and image for a later comparison:

```bash
.venv/bin/python scripts/benchmark_release.py \
  --baseline v2.6.0 --candidate working-tree \
  --inputs artifacts/kpi/2.6-vs-2.7/inputs \
  --image ghcr.io/home-assistant/home-assistant@sha256:IMAGE_DIGEST \
  --output artifacts/kpi/recheck
```

Use the actual digest from `report.json`. Output directories must be new. When
`--inputs` points outside the output directory, retain that directory too. Snapshot
reuse verifies hashes and station IDs instead of silently downloading newer data.
Disposable profiles contain HA-generated storage and authentication material; keep
profiles local. CI uploads reports, public upstream snapshots and logs, not profiles.

## Definitions and guardrails

| Measurement | Definition and scope |
|---|---|
| Setup to entities | Config-flow or restored-entry load through populated states and completion of initial refresh. Excludes HA boot. |
| Registry parse | Executor submission through parsed national registry. Includes executor queue delay. |
| Conditional registry update | Conditional HTTP response through atomic cache persistence. |
| Registry bytes | Actual JSON file size; decoded registry hashes must agree across versions. |
| Search service | HA service call through response for Roma/RM, limit 20. |
| Nearby search | Executor submission through local 10 km search around central Rome, limit 20. |
| Compare service | HA service response for all configured stations. |
| Next-change properties | 1,000 actual state/attribute read pairs per loaded next-change entity, using its real captured schedule. Refresh costs are included separately in full refresh CPU/time. |
| Refresh | Sequential coordinator refreshes for all configured stations, plus a fixed 100 ms allowance for state callbacks. Uses `async_refresh` to avoid debouncer coalescing. |
| Refresh CPU | Process CPU time over the same interval, including HA, Recorder and measurement overhead. |
| HTTP requests | Requests received by the replay server, split by phase, endpoint, status and body bytes. |
| Station cache writes | Completed calls to the real station Store save method during refresh. Does not measure OS-level bytes. |
| State events | Actual `state_changed` events for registered integration entities, grouped by phase. |
| Recorder rows | Committed SQLite rows for integration entity IDs, counted after clean shutdown. Fresh profiles exclude synthetic aged history. Both total rows and rows on shared unique IDs are compared, so new entities do not hide or imitate increased writes on existing entities. |
| Reload | One entry reload through restored entity states and completed initial refresh. |
| Memory | Process RSS sampled every 500 ms; refresh and reload trajectories are retained. Peak sampling can miss shorter allocation spikes. Post-refresh RSS and the final-minus-initial RSS change across the reload sequence are compared separately. The kernel lifetime high-water mark supplies the peak comparison; sampled peaks are retained as supporting observations. |
| Event loop | Delay of a 20 ms asyncio heartbeat, with separate distributions for each phase. Includes interference from other HA work. |
| Listeners | Coordinator listener counts before and after each reload. Weak references retain visibility into surviving old coordinators without keeping them alive for memory measurements. |
| Timers | Balance of observed integration timer registrations and cancellations. The probe delegates to the real HA scheduling helpers. |
| Tasks after unload | Unfinished asyncio tasks whose names contain the integration domain. Unnamed tasks are outside this check. |
| Price correctness | State, unit, self/served flag and source publication timestamp checked against captured payloads. |
| Fuel availability | End-of-session fraction plus valid fuel observations sampled by the 20 ms heartbeat, split by phase. Reload downtime is recorded separately; these samples are not production uptime. |
| Identity stability | Existing unique ID to entity ID mappings preserved across reload and upgrade. |

Request counts must equal one station request per station per measured refresh, with
no CSV downloads during that phase. Each measured station refresh must receive an HTTP
200; a successful cache fallback does not count as a network success. Fuel availability
samples must remain valid during refresh and idle; reload interruptions are reported
without treating expected downtime as a correctness failure. Conditional registry updates must return `304`
with no response body. Fuel mismatches, involuntary ID changes, growing listener/timer
counts, or residual measured timers/listeners/tasks after complete unload fail the run.
The number of loaded registry stations must match an independent CSV count of
distinct IDs with finite coordinates inside the geographic bounds. Source row counts,
invalid-coordinate rows, duplicate IDs and price ages at capture are recorded as input
quality, so upstream missing coordinates or old prices are not presented as code regressions.

New entities added intentionally by a version are permitted; existing IDs must remain.

`report.json` retains raw operation samples and nearest-rank p95/max, input and source
hashes, memory observations and request histories. `report.md` compares the medians of
the per-run medians. Single-value counters use their run medians. A metric is flagged
when its increase exceeds **all** of these provisional thresholds:

- 15% of the baseline median.
- The metric's absolute floor, defined in `METRICS` in the runner.
- The observed range across baseline runs.

These are starting guardrails, not statistically established acceptance limits. Two
runs do not provide a confidence interval. Inspect raw samples and repeat flagged
measurements with the same snapshot and more rounds before interpreting small timing
changes. Exact correctness counters have separate rules. Use
`--fail-on-performance` to make unresolved performance flags return exit code 2.
Correctness failures return exit code 1.

## Coverage limits and cadence

Run the default comparison after integration changes and before releases, alongside
`make check` and `scripts/ha_docker_regression.py`. The existing Docker regression
continues to cover fresh, lived, upgrade, live-network outage and recovery behavior.
Replay results do not replace those checks.

The report explicitly marks unmeasured areas. This short comparison does not establish
production availability, price propagation from live MIMIT, Retry-After behavior,
24–72 hour memory slope, opening-hours/cron boundary delays, scaling to 10/50 entries,
an independent geographic correctness oracle, or physical disk write volume. A minute
of idle observation does not prove absence of leaks. State-event and Recorder counts also depend on crossings of minute boundaries,
because price-age attributes can change while the source price stays constant.
Inspect phase counts and repeat runs before attributing a small row-count difference
to a code change. Recorder totals from short runs
must not be extrapolated to rows/hour or database growth/day.

The two existing synthetic JSON and schedule microbenchmarks remain useful for
isolating a change. Their output is separate from this report's actual version-to-version
HA measurements. A release decision should include the quantitative report, test and
Docker results, investigation of performance flags, and a statement of unmeasured scope.

For a 24-hour observation of real cron, Recorder, RSS, reloads and controlled HTTP
failure recovery, see [HA duration measurements](soak-monitoring.md). The duration
runner complements these short comparisons and retains independent progress samples.
