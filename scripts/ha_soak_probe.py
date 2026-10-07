"""Observe real HA for a bounded soak using captured HTTP responses."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import inspect
import json
import logging
from pathlib import Path
import time
import weakref
from typing import Any

from aiohttp import web
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, __version__
from homeassistant.core import CoreState, HomeAssistant, callback

from . import helpers as kpi

_LOGGER = logging.getLogger(__name__)


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def _append(path: Path, value: Any) -> None:
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(value) + '\n')


def _recorder(profile: Path, identities: dict[str, str]) -> dict[str, Any]:
    import sqlite3
    database = profile / 'home-assistant_v2.db'
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=10) as connection:
        rows = connection.execute('''SELECT sm.entity_id, COUNT(*) FROM states s
            JOIN states_meta sm ON s.metadata_id=sm.metadata_id GROUP BY sm.entity_id''').fetchall()
    counts = {eid: count for eid, count in rows}
    return {'rows_by_unique_id': {uid: counts.get(eid, 0) for uid, eid in identities.items()},
            'database_bytes': database.stat().st_size,
            'wal_bytes': (wal.stat().st_size if (wal := Path(str(database) + '-wal')).exists() else 0)}


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Start a local replay and a task that writes progress throughout the soak."""
    settings = config['ha_kpi_probe']
    csv_bytes, stations = await hass.async_add_executor_job(kpi._load_inputs)
    requests: list[dict[str, Any]] = []
    mode = {'value': 'online'}
    schedules: list[dict[str, Any]] = []
    kpi._instrument_timers()
    original_track = kpi.integration.async_track_point_in_utc_time

    def track(hass: HomeAssistant, action: Any, target: datetime) -> Any:
        if not inspect.iscoroutinefunction(action):
            return original_track(hass, action, target)
        observation = {'due_epoch': target.timestamp(), 'cancelled': False, 'fired': False}
        schedules.append(observation)
        async def fired(now: datetime) -> None:
            observation.update(fired=True, lateness_ms=max(0, time.time() - target.timestamp()) * 1000)
            started = time.monotonic()
            try:
                await action(now)
            finally:
                observation['completion_ms'] = (time.monotonic() - started) * 1000
        cancel_original = original_track(hass, fired, target)
        def cancel() -> None:
            if not observation['fired']:
                observation['cancelled'] = True
            cancel_original()
        return cancel
    kpi.integration.async_track_point_in_utc_time = track

    async def serve(request: web.Request) -> web.Response:
        is_csv = request.path == '/registry.csv'
        headers = {'ETag': '"soak-snapshot"'} if is_csv else {}
        status, body = 200, b''
        if mode['value'] == 'offline' and not is_csv:
            status = 503
        elif is_csv:
            if request.headers.get('If-None-Match') == headers['ETag']:
                status = 304
            else:
                body = csv_bytes
        elif (station := stations.get(request.match_info['station_id'])) is not None:
            body = json.dumps(station, ensure_ascii=False).encode()
        else:
            status = 404
        requests.append({'at_epoch': time.time(), 'kind': 'csv' if is_csv else 'station',
                         'station_id': request.match_info.get('station_id'), 'status': status,
                         'bytes': len(body), 'mode': mode['value']})
        return web.Response(body=body, status=status, headers=headers,
                            content_type='text/csv' if is_csv else 'application/json')
    app = web.Application()
    app.router.add_get('/registry.csv', serve)
    app.router.add_get('/registry/servicearea/{station_id}', serve)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 18765).start()
    kpi.api.BASE_URL = 'http://127.0.0.1:18765'
    kpi.csv_manager.CSV_URL = 'http://127.0.0.1:18765/registry.csv'
    hass.async_create_background_task(_run(hass, settings, stations, requests, schedules, mode, runner),
                                      name='ha_soak_measurements')
    return True


async def _run(hass: HomeAssistant, settings: dict[str, Any], stations: dict[str, Any],
               requests: list[dict[str, Any]], schedules: list[dict[str, Any]],
               mode: dict[str, str], runner: web.AppRunner) -> None:
    profile = Path(hass.config.path())
    phase = {'value': 'setup'}
    sampling = True
    availability: dict[str, Counter[str]] = {}
    lag: list[float] = []
    observer: asyncio.Task | None = None
    sampler: asyncio.Task | None = None
    sampler_stop = asyncio.Event()
    failures: list[str] = []
    events: list[dict[str, Any]] = []
    identities: dict[str, str] = {}
    initial_timers = initial_listeners = 0
    observed: weakref.WeakSet = weakref.WeakSet()
    started = time.monotonic()
    start_epoch = time.time()

    async def monitor() -> None:
        next_availability = 0.0
        while sampling:
            target = time.monotonic() + .1
            await asyncio.sleep(.1)
            lag.append(max(0, time.monotonic() - target) * 1000)
            if time.monotonic() >= next_availability:
                next_availability = time.monotonic() + 1
                counts = availability.setdefault(phase['value'], Counter())
                errors = kpi._fuel_errors(hass, stations)
                counts['observations'] += sum(len(s['fuels']) for s in stations.values())
                counts['invalid'] += len(errors)

    async def checkpoint(status: str = 'running') -> dict[str, Any]:
        elapsed = time.monotonic() - started
        memory = await hass.async_add_executor_job(kpi._memory)
        recorder = await hass.async_add_executor_job(_recorder, profile, identities)
        timing = kpi.summary(lag)
        timing.pop('samples')
        lag.clear()
        sample = {'at_utc': datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': elapsed,
                  'phase': phase['value'], 'rss_mib': memory, 'cpu_seconds': time.process_time(),
                  'timers': kpi._scheduled(), 'listeners': kpi._listeners(hass),
                  'event_loop_lag_ms': timing, 'requests': len(requests),
                  'http_counts': dict(Counter(str(r['status']) for r in requests)),
                  'availability': {key: dict(value) for key, value in availability.items()},
                  **recorder}
        await hass.async_add_executor_job(_append, profile / 'soak-samples.jsonl', sample)
        value = {'status': status, 'ha_version': __version__, 'ha_running': hass.state is CoreState.running,
                 'start_epoch': start_epoch,
                 'planned_seconds': float(settings['duration_seconds']), 'sample': sample,
                 'failures': sorted(set(failures)), 'events': events, 'identities': identities,
                 'cron': settings['cron'], 'initial_timers': initial_timers,
                 'initial_listeners': initial_listeners}
        await hass.async_add_executor_job(_write, profile / 'soak-progress.json', value)
        return value

    async def sample_loop() -> None:
        while not sampler_stop.is_set():
            await checkpoint()
            try:
                await asyncio.wait_for(sampler_stop.wait(), float(settings['sample_seconds']))
            except TimeoutError:
                pass

    try:
        if hass.state is not CoreState.running:
            ready = asyncio.Event()
            @callback
            def started_event(_event: Any) -> None:
                ready.set()
            unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started_event)
            try:
                await asyncio.wait_for(ready.wait(), 120)
            finally:
                if not ready.is_set():
                    unsub()
        for sid in stations:
            flow = await hass.config_entries.flow.async_init(kpi.DOMAIN, context={'source': SOURCE_USER})
            flow = await hass.config_entries.flow.async_configure(flow['flow_id'], {'next_step_id': 'station_id'})
            flow = await hass.config_entries.flow.async_configure(flow['flow_id'], {'station_id': sid})
            if str(flow['type']) != 'create_entry':
                raise AssertionError(f'Config flow failed for {sid}')
        await kpi._ready(hass, len(stations))
        for entry in hass.config_entries.async_entries(kpi.DOMAIN):
            hass.config_entries.async_update_entry(entry, options={**entry.options, 'cron_expression': settings['cron']})
            await hass.async_block_till_done()
            await kpi._ready(hass, len(stations))
        if kpi._fuel_errors(hass, stations):
            raise AssertionError('Initial fuel states differ from snapshot')
        identities.update(kpi._identities(hass))
        initial_timers, initial_listeners = kpi._scheduled(), kpi._listeners(hass)
        observed.update(kpi._coordinators(hass))
        await asyncio.sleep(float(settings['warmup_seconds']))
        started, start_epoch = time.monotonic(), time.time()
        phase['value'] = 'steady'
        observer = asyncio.create_task(monitor())
        duration = float(settings['duration_seconds'])
        deadline = started + duration
        sampler = asyncio.create_task(sample_loop())
        next_reload = started + float(settings['reload_seconds'])
        fault_offsets = [float(value) for value in settings['fault_offsets_seconds']]
        fault_index = 0
        reload_index = 0
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - started
            if fault_index < len(fault_offsets) and elapsed >= fault_offsets[fault_index]:
                phase['value'] = 'outage'
                mode['value'] = 'offline'
                fault_started = time.monotonic()
                request_offset = len(requests)
                await asyncio.wait_for(asyncio.gather(*(c.async_refresh() for c in kpi._coordinators(hass))), 360)
                coordinators = kpi._coordinators(hass)
                failed_requests = requests[request_offset:]
                def from_cache(coordinator: Any) -> bool:
                    marker = getattr(coordinator, 'last_refresh_from_cache', None)
                    if marker is not None:
                        return bool(marker)
                    sid = str(coordinator.config_entry.data['station_id'])
                    attempts = [r for r in failed_requests if r['station_id'] == sid]
                    return bool(coordinator.last_update_success and attempts
                                and all(r['status'] != 200 for r in attempts))
                cached = sum(from_cache(c) for c in coordinators)
                invalid = kpi._fuel_errors(hass, stations)
                outage_seconds = time.monotonic() - fault_started
                if cached != len(stations) or invalid:
                    failures.append('outage_cache_or_fuel_state')
                phase['value'] = 'recovery'
                mode['value'] = 'online'
                recovered_at = time.monotonic()
                before_recovery = len(requests)
                await asyncio.wait_for(asyncio.gather(*(c.async_refresh() for c in coordinators)), 120)
                recovered = all(c.last_update_success and not getattr(c, 'last_refresh_from_cache', False) for c in coordinators)
                network_ok = sum(r['kind'] == 'station' and r['status'] == 200 for r in requests[before_recovery:])
                if not recovered or network_ok < len(stations) or kpi._fuel_errors(hass, stations):
                    failures.append('recovery_network_or_fuel_state')
                events.append({'kind': 'fault', 'elapsed_seconds': elapsed,
                               'outage_seconds': outage_seconds, 'cached_stations': cached,
                               'recovery_seconds': time.monotonic() - recovered_at,
                               'network_successes': network_ok, 'requests': requests[request_offset:]})
                del coordinators
                fault_index += 1
                phase['value'] = 'steady'
            if time.monotonic() >= next_reload and time.monotonic() < deadline:
                phase['value'] = 'reload'
                entries = hass.config_entries.async_entries(kpi.DOMAIN)
                entry = entries[reload_index % len(entries)]
                reload_started = time.monotonic()
                if not await hass.config_entries.async_reload(entry.entry_id):
                    failures.append('reload_failed')
                await kpi._ready(hass, len(stations))
                observed.update(kpi._coordinators(hass))
                if kpi._identities(hass) != identities:
                    failures.append('identity_changed')
                if kpi._scheduled() != initial_timers or kpi._listeners(hass) != initial_listeners:
                    failures.append('reload_timer_or_listener_growth')
                events.append({'kind': 'reload', 'elapsed_seconds': time.monotonic() - started,
                               'duration_seconds': time.monotonic() - reload_started,
                               'timers': kpi._scheduled(), 'listeners': kpi._listeners(hass)})
                reload_index += 1
                next_reload += float(settings['reload_seconds'])
                phase['value'] = 'steady'
            await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
        sampling = False
        sampler_stop.set()
        if sampler:
            await sampler
        if observer:
            await observer
        for name, counts in availability.items():
            if name != 'reload' and counts['invalid']:
                failures.append(f'invalid_fuel_observations_{name}')
        completed_epoch = time.time()
        due = [s for s in schedules if start_epoch <= s['due_epoch'] <= completed_epoch - 2
               and not s['cancelled']]
        if any(not s['fired'] for s in due):
            failures.append('missed_cron_callback')
        if not due:
            failures.append('cron_not_observed')
        if fault_index != len(fault_offsets):
            failures.append('planned_fault_not_exercised')
        progress = await checkpoint('completed' if not failures else 'failed')
        for entry in hass.config_entries.async_entries(kpi.DOMAIN):
            if not await hass.config_entries.async_unload(entry.entry_id):
                failures.append('unload_failed')
        await asyncio.sleep(.5)
        remaining = [task.get_name() for task in asyncio.all_tasks() if not task.done() and kpi.DOMAIN in task.get_name()]
        listeners_left = sum(len(c._listeners) for c in observed)
        if remaining or listeners_left or kpi._scheduled():
            failures.append('resources_after_unload')
        result = {**progress, 'status': 'completed' if not failures else 'failed',
                  'failures': sorted(set(failures)), 'duration_seconds': completed_epoch - start_epoch,
                  'scheduled_callbacks': due, 'all_schedule_observations': schedules,
                  'requests': requests, 'after_unload': {'tasks': remaining, 'listeners': listeners_left,
                  'timers': kpi._scheduled()},
                  'scope': 'Bounded real HA process, Recorder and cron; captured HTTP replay, unchanged prices; no live MIMIT uptime claim'}
    except Exception as err:
        _LOGGER.exception('Soak failed')
        result = {'status': 'failed', 'error': f'{type(err).__name__}: {err}', 'events': events,
                  'failures': sorted(set(failures)), 'duration_seconds': time.monotonic() - started}
    finally:
        sampling = False
        sampler_stop.set()
        if sampler:
            await sampler
        if observer:
            await observer
        mode['value'] = 'online'
        await runner.cleanup()
    await hass.async_add_executor_job(_write, profile / 'soak-result.json', result)
