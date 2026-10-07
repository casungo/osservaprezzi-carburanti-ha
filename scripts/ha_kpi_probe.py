"""Measure the integration inside HA using captured upstream HTTP responses."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from functools import partial
import inspect
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import resource
import statistics
import time
import weakref
from typing import Any

from aiohttp import web

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED, __version__
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
from custom_components.osservaprezzi_carburanti.sensor import StationNextChangeSensor
from homeassistant.util import dt as dt_util

import custom_components.osservaprezzi_carburanti as integration
from custom_components.osservaprezzi_carburanti import api, csv_manager, entity
from custom_components.osservaprezzi_carburanti.const import DOMAIN
from custom_components.osservaprezzi_carburanti.discovery import find_nearby_stations

_LOGGER = logging.getLogger(__name__)
_ACTIVE_TIMERS: set[object] = set()


def summary(values: list[float]) -> dict[str, Any]:
    """Return nearest-rank percentiles and all observations, in milliseconds."""
    values = list(values)
    ordered = sorted(values)
    return {
        "count": len(values),
        "median": statistics.median(values) if values else None,
        "p95": ordered[math.ceil(len(values) * .95) - 1] if values else None,
        "max": max(values) if values else None,
        "samples": values,
    }


def _memory() -> float:
    """Read current process RSS in MiB, in an executor thread."""
    fields = Path('/proc/self/statm').read_text().split()
    return int(fields[1]) * os.sysconf('SC_PAGE_SIZE') / 1048576


def _load_inputs() -> tuple[bytes, dict[str, Any]]:
    root = Path('/inputs')
    return (root / 'registry.csv').read_bytes(), json.loads((root / 'stations.json').read_text())


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Install a local HTTP replay server and start the measurement task."""
    _instrument_timers()
    settings = config['ha_kpi_probe']
    csv_bytes, stations = await hass.async_add_executor_job(_load_inputs)
    requests: list[dict[str, Any]] = []
    mode = {'value': 'online'}

    async def serve(request: web.Request) -> web.Response:
        is_csv = request.path == '/registry.csv'
        status = 200
        headers = {}
        body = b''
        if mode['value'] == 'offline':
            status = 503
        elif is_csv:
            headers = {'ETag': '"kpi-snapshot"'}
            if request.headers.get('If-None-Match') == headers['ETag']:
                status = 304
            else:
                body = csv_bytes
        else:
            station = stations.get(request.match_info['station_id'])
            if station is None:
                status = 404
            else:
                body = json.dumps(station, ensure_ascii=False).encode()
        requests.append({'kind': 'csv' if is_csv else 'station', 'status': status,
                         'bytes': len(body), 'at': time.monotonic()})
        return web.Response(body=body, status=status, headers=headers,
                            content_type='text/csv' if is_csv else 'application/json')

    app = web.Application()
    app.router.add_get('/registry.csv', serve)
    app.router.add_get('/registry/servicearea/{station_id}', serve)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 18765).start()
    api.BASE_URL = 'http://127.0.0.1:18765'
    csv_manager.CSV_URL = 'http://127.0.0.1:18765/registry.csv'
    hass.async_create_task(_run(hass, settings, stations, requests, mode, runner),
                           name='ha_kpi_measurements')
    return True


async def _started(hass: HomeAssistant) -> None:
    if str(hass.state).lower().endswith('running'):
        return
    event = asyncio.Event()
    @callback
    def done(_event: Any) -> None:
        event.set()
    unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, done)
    try:
        await asyncio.wait_for(event.wait(), 120)
    finally:
        unsub()


def _identities(hass: HomeAssistant) -> dict[str, str]:
    return {str(e.unique_id): e.entity_id for e in er.async_get(hass).entities.values()
            if e.platform == DOMAIN}


def _coordinators(hass: HomeAssistant) -> list[Any]:
    return [d['coordinator'] for d in hass.data.get(DOMAIN, {}).values()
            if isinstance(d, dict) and 'coordinator' in d]


async def _ready(hass: HomeAssistant, station_count: int) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        coordinators = _coordinators(hass)
        identities = _identities(hass)
        if (len(coordinators) == station_count and all(c.data for c in coordinators)
                and identities and all(hass.states.get(eid) for eid in identities.values())):
            tasks = [d.get('initial_refresh_task') for d in hass.data.get(DOMAIN, {}).values()
                     if isinstance(d, dict)]
            pending = [task for task in tasks if isinstance(task, asyncio.Task) and not task.done()]
            if pending:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=120)
            return
        await asyncio.sleep(.05)
    raise TimeoutError('Configured station entities did not become ready')


def _fuel_errors(hass: HomeAssistant, stations: dict[str, Any]) -> list[str]:
    errors = []
    identities = _identities(hass)
    for station_id, station in stations.items():
        for fuel in station['fuels']:
            suffix = 'self' if fuel['isSelf'] else 'servito'
            unique_id = f"{station_id}_{fuel['name']}_{suffix}"
            entity_id = identities.get(unique_id)
            state = hass.states.get(entity_id) if entity_id else None
            try:
                correct = (state is not None and float(state.state) == float(fuel['price'])
                           and state.attributes.get('unit_of_measurement') == '€/L'
                           and state.attributes.get('is_self_service') == fuel['isSelf']
                           and dt_util.parse_datetime(state.attributes.get('last_update', ''))
                               == dt_util.parse_datetime(fuel['insertDate']))
            except ValueError:
                correct = False
            if not correct:
                errors.append(unique_id)
    return errors


def _instrument_timers() -> None:
    """Observe timer registration/cancellation without replacing HA scheduling."""
    for module in (integration, entity):
        for name in ('async_track_point_in_utc_time', 'async_track_time_interval'):
            if not hasattr(module, name):
                continue
            original = getattr(module, name)
            def track(hass: HomeAssistant, action: Any, *args: Any,
                      original: Any = original, name: str = name, **kwargs: Any) -> Any:
                token = object()
                one_shot = name == 'async_track_point_in_utc_time'
                if inspect.iscoroutinefunction(action):
                    async def wrapped(*values: Any) -> Any:
                        if one_shot:
                            _ACTIVE_TIMERS.discard(token)
                        return await action(*values)
                else:
                    @callback
                    def wrapped(*values: Any) -> Any:
                        if one_shot:
                            _ACTIVE_TIMERS.discard(token)
                        return action(*values)
                unsubscribe = original(hass, wrapped if one_shot else action, *args, **kwargs)
                _ACTIVE_TIMERS.add(token)
                @callback
                def cancel() -> None:
                    unsubscribe()
                    _ACTIVE_TIMERS.discard(token)
                return cancel
            setattr(module, name, track)


def _scheduled() -> int:
    """Return the balance of observed integration timer registrations."""
    return len(_ACTIVE_TIMERS)


def _listeners(hass: HomeAssistant) -> int:
    return sum(len(c._listeners) for c in _coordinators(hass))


async def _measure(hass: HomeAssistant, settings: dict[str, Any], stations: dict[str, Any],
                   requests: list[dict[str, Any]], mode: dict[str, str]) -> dict[str, Any]:
    repeats = int(settings['repeats'])
    station_ids = list(stations)
    await _started(hass)
    rss_start = await hass.async_add_executor_job(_memory)
    lag: dict[str, list[float]] = {}
    phase = {'value': 'setup'}
    keep_sampling = True
    rss_samples = []
    watched_fuels: list[str] = []
    fuel_samples: dict[str, Counter[str]] = {}

    async def heartbeat() -> None:
        while keep_sampling:
            target = asyncio.get_running_loop().time() + .02
            await asyncio.sleep(.02)
            lag.setdefault(phase['value'], []).append(
                max(0, asyncio.get_running_loop().time() - target) * 1000)
            if watched_fuels and phase['value'] not in {'setup', 'unload'}:
                counts = fuel_samples.setdefault(phase['value'], Counter())
                counts['observations'] += len(watched_fuels)
                counts['valid'] += sum(
                    (state := hass.states.get(eid)) is not None
                    and state.state not in {'unknown', 'unavailable'} for eid in watched_fuels)

    async def memory_sampler() -> None:
        while keep_sampling:
            rss_samples.append(await hass.async_add_executor_job(_memory))
            await asyncio.sleep(.5)

    heartbeat_task = asyncio.create_task(heartbeat())
    memory_task = asyncio.create_task(memory_sampler())
    state_events: Counter[str] = Counter()
    @callback
    def state_changed(event: Any) -> None:
        entity_id = event.data.get('entity_id')
        if entity_id in _identities(hass).values():
            state_events[phase['value']] += 1
    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)
    before = _identities(hass)
    setup_started = time.perf_counter()
    try:
        existing = hass.config_entries.async_entries(DOMAIN)
        if existing:
            for entry in existing:
                if not await hass.config_entries.async_set_disabled_by(entry.entry_id, None):
                    raise AssertionError('Restored config entry could not be enabled')
        else:
            for station_id in station_ids:
                flow = await hass.config_entries.flow.async_init(DOMAIN, context={'source': SOURCE_USER})
                flow = await hass.config_entries.flow.async_configure(flow['flow_id'],
                                                                    {'next_step_id': 'station_id'})
                flow = await hass.config_entries.flow.async_configure(flow['flow_id'],
                                                                    {'station_id': station_id})
                if str(flow['type']) != 'create_entry':
                    raise AssertionError(f'Config flow failed: {flow}')
        await _ready(hass, len(station_ids))
        setup_ms = (time.perf_counter() - setup_started) * 1000
        identities = _identities(hass)
        expected_fuels = [f"{sid}_{fuel['name']}_{'self' if fuel['isSelf'] else 'servito'}"
                          for sid, station in stations.items() for fuel in station['fuels']]
        watched_fuels.extend(identities[uid] for uid in expected_fuels if uid in identities)
        observed_coordinators = weakref.WeakSet(_coordinators(hass))
        fuel_errors = _fuel_errors(hass, stations)
        manager = _coordinators(hass)[0].csv_manager
        registry_count = len(manager._stations_cache)
        registry_hash = await hass.async_add_executor_job(
            lambda: hashlib.sha256(json.dumps(manager._stations_cache, sort_keys=True,
                                               ensure_ascii=False).encode()).hexdigest())
        setup_requests = list(requests)
        parse_times = []
        csv_content = (await hass.async_add_executor_job(_load_inputs))[0].decode('utf-8-sig')
        phase['value'] = 'parse'
        for _ in range(repeats):
            started = time.perf_counter()
            success, _, parsed = await hass.async_add_executor_job(manager._parse_csv_content_to_cache,
                                                                  csv_content)
            if not success or len(parsed) != registry_count:
                raise AssertionError('CSV parse was inconsistent')
            parse_times.append((time.perf_counter() - started) * 1000)
        phase['value'] = 'registry'
        registry_times = []
        csv_before = len([r for r in requests if r['kind'] == 'csv'])
        for _ in range(repeats):
            manager._last_update = dt_util.now() - timedelta(days=2)
            started = time.perf_counter()
            if not await manager.async_update_csv_data():
                raise AssertionError('Conditional CSV update failed')
            registry_times.append((time.perf_counter() - started) * 1000)
        csv_requests = [r for r in requests if r['kind'] == 'csv'][csv_before:]
        cache_bytes = await hass.async_add_executor_job(lambda: Path(manager._cache_path).stat().st_size)
        snapshot = await manager.async_ensure_registry()
        phase['value'] = 'search'
        search_times = []
        nearby_times = []
        nearby_result = None
        for _ in range(repeats):
            started = time.perf_counter()
            response = await hass.services.async_call(DOMAIN, 'search_registry',
                {'municipality': 'Roma', 'province': 'RM', 'limit': 20},
                blocking=True, return_response=True)
            search_times.append((time.perf_counter() - started) * 1000)
            if response['result_count'] < 1:
                raise AssertionError('Registry search returned no stations')
            started = time.perf_counter()
            nearby_result = await hass.async_add_executor_job(partial(find_nearby_stations,
                snapshot.stations, latitude=41.9028, longitude=12.4964, radius_km=10, limit=20))
            nearby_times.append((time.perf_counter() - started) * 1000)
        phase['value'] = 'compare'
        compare_times = []
        for _ in range(repeats):
            started = time.perf_counter()
            response = await hass.services.async_call(DOMAIN, 'compare_stations', {},
                                                       blocking=True, return_response=True)
            compare_times.append((time.perf_counter() - started) * 1000)
            if len(response['stations']) != len(station_ids):
                raise AssertionError('Comparison omitted a configured station')
        phase['value'] = 'properties'
        component = hass.data[DATA_INSTANCES]['sensor']
        next_entities = [e for e in component.entities if isinstance(e, StationNextChangeSensor)]
        next_read_times = []
        for _ in range(repeats):
            if not next_entities:
                break
            started = time.perf_counter()
            for _pair in range(1000):
                for next_entity in next_entities:
                    _ = next_entity.native_value, next_entity.extra_state_attributes
            next_read_times.append((time.perf_counter() - started) * 1000 / len(next_entities))
            await asyncio.sleep(0)
        phase['value'] = 'refresh'
        request_start = len(requests)
        refresh_times = []
        cpu_times = []
        rss_refresh = []
        refresh_state_events = state_events['refresh']
        station_writes = 0
        originals = []
        for coordinator in _coordinators(hass):
            original = coordinator._store.async_save
            originals.append((coordinator._store, original))
            async def save(data: Any, original: Any = original) -> None:
                nonlocal station_writes
                await original(data)
                station_writes += 1
            coordinator._store.async_save = save
        try:
            for _ in range(repeats):
                cpu = time.process_time()
                started = time.perf_counter()
                for coordinator in _coordinators(hass):
                    await coordinator.async_refresh()
                await asyncio.sleep(.1)
                refresh_times.append((time.perf_counter() - started) * 1000)
                cpu_times.append((time.process_time() - cpu) * 1000)
                rss_refresh.append(await hass.async_add_executor_job(_memory))
                fuel_errors.extend(_fuel_errors(hass, stations))
        finally:
            for store, original in originals:
                store.async_save = original
        refresh_requests = requests[request_start:]
        phase['value'] = 'reload'
        reload_times = []
        listener_counts = [_listeners(hass)]
        timer_counts = [_scheduled()]
        rss_reload = [await hass.async_add_executor_job(_memory)]
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        for _ in range(int(settings['reloads'])):
            started = time.perf_counter()
            if not await hass.config_entries.async_reload(entry.entry_id):
                raise AssertionError('Reload failed')
            await _ready(hass, len(station_ids))
            # Wait for deferred initial refresh rather than racing it on beta versions.
            for coordinator in _coordinators(hass):
                if not coordinator.last_update_success:
                    await coordinator.async_refresh()
            await asyncio.sleep(.2)
            reload_times.append((time.perf_counter() - started) * 1000)
            observed_coordinators.update(_coordinators(hass))
            listener_counts.append(_listeners(hass))
            timer_counts.append(_scheduled())
            rss_reload.append(await hass.async_add_executor_job(_memory))
        changed_ids = sum(_identities(hass).get(uid) != eid for uid, eid in identities.items())
        phase['value'] = 'idle'
        idle_started = time.monotonic()
        await asyncio.sleep(int(settings['idle_seconds']))
        idle_seconds = time.monotonic() - idle_started
        availability = sum(
            (state := hass.states.get(identities[uid])) is not None
            and state.state not in {'unknown', 'unavailable'}
            for uid in expected_fuels if uid in identities) / len(expected_fuels)
        phase['value'] = 'unload'
        entries = list(hass.config_entries.async_entries(DOMAIN))
        for entry in entries:
            if not await hass.config_entries.async_unload(entry.entry_id):
                raise AssertionError('Unload failed')
        await asyncio.sleep(.2)
        remaining_tasks = [task.get_name() for task in asyncio.all_tasks()
                           if not task.done() and DOMAIN in task.get_name()]
        return {
            'status': 'passed', 'ha_version': __version__,
            'station_count': len(station_ids), 'entity_count': len(identities),
            'setup_to_entities_ms': setup_ms, 'registry_station_count': registry_count,
            'registry_content_sha256': registry_hash, 'setup_requests': setup_requests,
            'registry_cache_bytes': cache_bytes, 'registry_parse_ms': summary(parse_times),
            'registry_conditional_update_ms': summary(registry_times),
            'csv_conditional_requests': len(csv_requests),
            'csv_304_count': sum(r['status'] == 304 for r in csv_requests),
            'csv_conditional_bytes': sum(r['bytes'] for r in csv_requests),
            'search_service_ms': summary(search_times), 'nearby_search_ms': summary(nearby_times),
            'nearby_station_ids': [c.station_id for c in nearby_result or ()],
            'compare_service_ms': summary(compare_times), 'refresh_ms': summary(refresh_times),
            'next_change_read_pairs_1000_ms': summary(next_read_times),
            'next_change_entity_count': len(next_entities),
            'refresh_cpu_ms': summary(cpu_times), 'refresh_station_requests':
                sum(r['kind'] == 'station' for r in refresh_requests),
            'refresh_csv_requests': sum(r['kind'] == 'csv' for r in refresh_requests),
            'refresh_successful_network_requests': sum(
                r['kind'] == 'station' and r['status'] == 200 for r in refresh_requests),
            'station_cache_writes': station_writes,
            'refresh_state_events': state_events['refresh'] - refresh_state_events,
            'state_events_by_phase': dict(state_events),
            'idle_seconds': idle_seconds, 'reload_ms': summary(reload_times),
            'listeners_after_reloads': listener_counts, 'timers_after_reloads': timer_counts,
            'rss_after_refresh_mib': rss_refresh, 'rss_after_reload_mib': rss_reload,
            'rss_start_mib': rss_start, 'rss_observed_peak_mib': max(rss_samples),
            'process_lifetime_peak_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            'event_loop_lag_ms': {k: summary(v) for k, v in lag.items()},
            'fuel_mismatches': fuel_errors, 'identity_changes_after_reload': changed_ids,
            'identities': identities, 'identity_changes_from_persisted':
                sum(identities.get(uid) != eid for uid, eid in before.items()),
            'fuel_availability_end_fraction': availability,
            'tasks_after_unload': remaining_tasks, 'timers_after_unload': _scheduled(),
            'listeners_after_unload': sum(len(c._listeners) for c in observed_coordinators),
            'fuel_availability_samples': {k: {**dict(v), 'fraction': v['valid'] / v['observations']}
                                         for k, v in fuel_samples.items()},
            'requests': requests,
            'scope': 'Real HA/Recorder/HTTP, replayed real upstream snapshot; no live latency or soak claim',
        }
    finally:
        keep_sampling = False
        unsubscribe()
        await heartbeat_task
        await memory_task


async def _run(hass: HomeAssistant, settings: dict[str, Any], stations: dict[str, Any],
               requests: list[dict[str, Any]], mode: dict[str, str], runner: web.AppRunner) -> None:
    try:
        result = await _measure(hass, settings, stations, requests, mode)
    except Exception as err:
        _LOGGER.exception('KPI measurement failed')
        result = {'status': 'failed', 'error': f'{type(err).__name__}: {err}'}
    finally:
        await runner.cleanup()
    await hass.async_add_executor_job(Path(hass.config.path('kpi-result.json')).write_text,
                                     json.dumps(result, indent=2), 'utf-8')
