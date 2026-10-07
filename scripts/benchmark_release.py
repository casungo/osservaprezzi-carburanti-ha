"""Compare two integration sources inside pinned, isolated Home Assistant containers."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import tarfile
import time
from typing import Any
import urllib.request
import uuid

DOMAIN = 'osservaprezzi_carburanti'
CSV_URL = 'https://www.mimit.gov.it/images/exportCSV/anagrafica_impianti_attivi.csv'
API_URL = 'https://carburanti.mise.gov.it/ospzApi/registry/servicearea/'
DEFAULT_IMAGE = 'ghcr.io/home-assistant/home-assistant:stable'


def run(command: list[str], *, timeout: int = 120) -> str:
    """Run a command, preserving errors without interpolating shell text."""
    return subprocess.run(command, check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def source_hash(root: Path) -> str:
    """Hash relative paths and bytes of the measured integration source."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b'\0')
            digest.update(path.read_bytes())
    return digest.hexdigest()


def export_source(repo: Path, ref: str, destination: Path) -> dict[str, Any]:
    """Snapshot a Git ref or current integration files, excluding bytecode."""
    relative = f'custom_components/{DOMAIN}'
    if ref == 'working-tree':
        shutil.copytree(repo / relative, destination / relative,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        sha = run(['git', '-C', str(repo), 'rev-parse', 'HEAD'])
    else:
        sha = run(['git', '-C', str(repo), 'rev-parse', f'{ref}^{{commit}}'])
        archive = subprocess.run(['git', '-C', str(repo), 'archive', sha, relative],
                                 check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(destination, filter='data')
    root = destination / relative
    return {'ref': ref, 'commit': sha, 'source_sha256': source_hash(root),
            'version': json.loads((root / 'manifest.json').read_text())['version']}


def capture_inputs(destination: Path, station_ids: list[str]) -> dict[str, Any]:
    """Download each real input once and retain bytes and provenance."""
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['station_ids'] != station_ids:
            raise ValueError('Snapshot station IDs differ from requested station IDs')
        for name, expected in manifest['sha256'].items():
            if hashlib.sha256((destination / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f'Snapshot hash mismatch: {name}')
        return manifest
    provenance = []
    def fetch(url: str) -> bytes:
        request = urllib.request.Request(url, headers={'User-Agent': 'Osservaprezzi-KPI/1.0'})
        with urllib.request.urlopen(request, timeout=90) as response:
            body = response.read()
            provenance.append({'url': url, 'status': response.status, 'bytes': len(body),
                'etag': response.headers.get('ETag'),
                'last_modified': response.headers.get('Last-Modified')})
            return body
    (destination / 'registry.csv').write_bytes(fetch(CSV_URL))
    stations = {}
    for station_id in station_ids:
        station = json.loads(fetch(API_URL + station_id))
        if str(station.get('id')) != station_id or not station.get('fuels'):
            raise ValueError(f'Station {station_id} does not contain the required real prices')
        stations[station_id] = station
        time.sleep(2)
    (destination / 'stations.json').write_text(json.dumps(stations, ensure_ascii=False), encoding='utf-8')
    manifest = {'captured_at': datetime.now(timezone.utc).isoformat(),
                'station_ids': station_ids, 'sources': provenance,
                'sha256': {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                           for name in ('registry.csv', 'stations.json')}}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def input_quality(inputs: Path, captured_at: str) -> dict[str, Any]:
    """Check source row coverage and price ages independently of integration parsing."""
    lines = (inputs / 'registry.csv').read_text(encoding='utf-8-sig').splitlines()
    header = next(index for index, line in enumerate(lines) if 'idImpianto' in line)
    delimiter = max(';|,', key=lambda candidate: len(lines[header].split(candidate)))
    rows = list(csv.DictReader(io.StringIO('\n'.join(lines[header:])), delimiter=delimiter))
    ids = Counter(row['idImpianto'].strip() for row in rows)
    usable = set()
    invalid = 0
    for row in rows:
        try:
            latitude = float(row['Latitudine'].strip().replace(',', '.'))
            longitude = float(row['Longitudine'].strip().replace(',', '.'))
            valid = (math.isfinite(latitude) and math.isfinite(longitude)
                     and -90 <= latitude <= 90 and -180 <= longitude <= 180)
        except (ValueError, TypeError):
            valid = False
        if valid and row['idImpianto'].strip():
            usable.add(row['idImpianto'].strip())
        else:
            invalid += 1
    stations = json.loads((inputs / 'stations.json').read_text())
    reference = datetime.fromisoformat(captured_at)
    ages: dict[str, list[float]] = {}
    unknown_timestamps = 0
    for station_id, station in stations.items():
        ages[station_id] = []
        for fuel in station['fuels']:
            try:
                published = datetime.fromisoformat(fuel['insertDate'].replace('Z', '+00:00'))
                if published.tzinfo is None:
                    raise ValueError('Price publication timestamp has no timezone')
            except (KeyError, AttributeError, TypeError, ValueError):
                unknown_timestamps += 1
                continue
            ages[station_id].append((reference - published).total_seconds() / 3600)
    return {'registry_rows': len(rows), 'distinct_station_ids': len(ids),
            'duplicate_ids': sum(count - 1 for count in ids.values() if count > 1),
            'invalid_coordinate_or_id_rows': invalid, 'usable_station_ids': len(usable),
            'fuel_count': sum(len(station['fuels']) for station in stations.values()),
            'price_ages_hours_at_capture': ages, 'unknown_price_timestamps': unknown_timestamps,
            'source_prices_older_than_24h': sum(age > 24 for values in ages.values() for age in values),
            'capture_reference': captured_at}


def prepare_profile(repo: Path, source: Path, profile: Path, args: argparse.Namespace,
                    persisted: Path | None = None) -> None:
    """Create a disposable HA profile; disable restored entries until replay is ready."""
    if persisted:
        shutil.copytree(persisted, profile)
        entries_path = profile / '.storage/core.config_entries'
        content = json.loads(entries_path.read_text())
        for entry in content['data']['entries']:
            if entry['domain'] == DOMAIN:
                entry['disabled_by'] = 'user'
        entries_path.write_text(json.dumps(content), encoding='utf-8')
        shutil.rmtree(profile / 'custom_components', ignore_errors=True)
        for name in ('kpi-result.json', 'home-assistant.log', 'home-assistant.log.1'):
            (profile / name).unlink(missing_ok=True)
    else:
        profile.mkdir(parents=True)
    integration = profile / f'custom_components/{DOMAIN}'
    shutil.copytree(source / f'custom_components/{DOMAIN}', integration)
    probe = profile / 'custom_components/ha_kpi_probe'
    probe.mkdir()
    shutil.copyfile(repo / 'scripts/ha_kpi_probe.py', probe / '__init__.py')
    (probe / 'manifest.json').write_text(json.dumps({
        'domain': 'ha_kpi_probe', 'name': 'Release KPI probe', 'version': '1.0.0',
        'integration_type': 'helper', 'dependencies': [DOMAIN], 'requirements': [],
        'documentation': 'https://github.com/casungo/osservaprezzi-carburanti-ha',
        'codeowners': ['@casungo']}), encoding='utf-8')
    (profile / 'configuration.yaml').write_text(f'''homeassistant:
  name: Isolated KPI comparison
  latitude: 41.9028
  longitude: 12.4964
  time_zone: Europe/Rome
logger:
  default: warning
recorder:
  commit_interval: 1
  purge_keep_days: 30
{DOMAIN}:
ha_kpi_probe:
  repeats: {args.repeats}
  reloads: {args.reloads}
  idle_seconds: {args.idle_seconds}
''', encoding='utf-8')


def recorder_counts(profile: Path) -> dict[str, int]:
    """Count persisted integration rows only, after a clean shutdown."""
    with sqlite3.connect(profile / 'home-assistant_v2.db') as connection:
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        rows = connection.execute('''SELECT sm.entity_id, COUNT(*) FROM states s
            JOIN states_meta sm ON s.metadata_id=sm.metadata_id GROUP BY sm.entity_id''').fetchall()
    identities = json.loads((profile / 'kpi-result.json').read_text()).get('identities', {})
    return {entity_id: count for entity_id, count in rows if entity_id in identities.values()}


def measure(image_id: str, source: Path, profile: Path, inputs: Path,
            args: argparse.Namespace, repo: Path, persisted: Path | None = None) -> dict[str, Any]:
    """Boot and stop only a uniquely named disposable measurement container."""
    prepare_profile(repo, source, profile, args, persisted)
    name = 'ha-kpi-' + uuid.uuid4().hex[:10]
    result_path = profile / 'kpi-result.json'
    boot_started = time.monotonic()
    recorder_before = 0
    if persisted:
        with sqlite3.connect(profile / 'home-assistant_v2.db') as connection:
            recorder_before = connection.execute('SELECT COUNT(*) FROM states').fetchone()[0]
    run(['docker', 'run', '-d', '--name', name, '--add-host', 'carburanti.mise.gov.it:127.0.0.1',
         '--add-host', 'www.mimit.gov.it:127.0.0.1',
         '--cpus', str(args.cpus), '--memory', args.memory,
         '-v', f'{profile}:/config', '-v', f'{inputs}:/inputs:ro', image_id])
    try:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if result_path.exists():
                result = json.loads(result_path.read_text())
                break
            if run(['docker', 'inspect', '--format', '{{.State.Running}}', name]) != 'true':
                raise RuntimeError('HA measurement container exited before producing a result')
            time.sleep(1)
        else:
            raise TimeoutError(f'No KPI result within {args.timeout}s')
        result['boot_to_report_seconds'] = time.monotonic() - boot_started
    finally:
        logs = run(['docker', 'logs', name])
        # Docker writes HA logs to stderr; capture both streams for diagnosis.
        logged = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, check=True)
        (profile.parent / f'{profile.name}.log').write_text(logs + logged.stderr, encoding='utf-8')
        stopped = subprocess.run(['docker', 'stop', '--time', '30', name], capture_output=True,
                                 text=True, timeout=45)
        inspection = json.loads(run(['docker', 'inspect', name]))[0]['State']
        if stopped.returncode != 0 or inspection['ExitCode'] != 0 or inspection['OOMKilled']:
            run(['docker', 'rm', '-f', name])
            raise RuntimeError(f'HA did not shut down cleanly: {inspection}')
        run(['docker', 'rm', name])
    run(['docker', 'run', '--rm', '--entrypoint', 'chown',
         '-v', f'{profile}:/config', image_id, '-R', f'{os.getuid()}:{os.getgid()}', '/config'])
    (profile.parent / f'{profile.name}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result['status'] != 'passed':
        raise RuntimeError(f'KPI probe failed: {result}')
    counts = recorder_counts(profile)
    result['recorder_rows_by_entity'] = counts
    result['recorder_rows_total'] = sum(counts.values())
    result['recorder_rows_before_boot_all_entities'] = recorder_before
    result['database_bytes'] = (profile / 'home-assistant_v2.db').stat().st_size
    add_memory_metrics(result)
    return result


def add_memory_metrics(result: dict[str, Any]) -> None:
    """Expose measured post-refresh RSS and reload growth without claiming a soak result."""
    refresh = result.get('rss_after_refresh_mib', [])
    reloads = result.get('rss_after_reload_mib', [])
    result['rss_steady_mib'] = refresh[-1] if refresh else None
    result['rss_reload_growth_mib'] = reloads[-1] - reloads[0] if len(reloads) > 1 else None


def add_entity_scope_metrics(report: dict[str, Any]) -> None:
    """Compare Recorder cost on the same unique IDs as well as the total feature set."""
    runs = report['runs']
    common = set.intersection(*(set(run['result']['identities']) for run in runs))
    report['common_entity_count'] = len(common)
    for run in runs:
        result = run['result']
        counts = result['recorder_rows_by_entity']
        result['recorder_rows_common_entities'] = sum(
            counts.get(result['identities'][unique_id], 0) for unique_id in common)


METRICS = {
    'setup_to_entities_ms': ('ms', 20.0),
    'registry_cache_bytes': ('bytes', 0.0),
    'registry_parse_ms.median': ('ms', 5.0),
    'registry_conditional_update_ms.median': ('ms', 5.0),
    'search_service_ms.median': ('ms', 5.0),
    'nearby_search_ms.median': ('ms', 5.0),
    'compare_service_ms.median': ('ms', 1.0),
    'next_change_read_pairs_1000_ms.median': ('ms', 1.0),
    'refresh_ms.median': ('ms', 100.0),
    'refresh_cpu_ms.median': ('ms CPU', 5.0),
    'reload_ms.median': ('ms', 100.0),
    'process_lifetime_peak_mib': ('MiB', 5.0),
    'rss_steady_mib': ('MiB', 5.0),
    'rss_reload_growth_mib': ('MiB per reload sequence', 5.0),
    'event_loop_lag_ms.refresh.p95': ('ms', 5.0),
    'event_loop_lag_ms.parse.p95': ('ms', 5.0),
    'refresh_station_requests': ('requests', 0.0),
    'refresh_csv_requests': ('requests', 0.0),
    'station_cache_writes': ('writes', 0.0),
    'refresh_state_events': ('events', 0.0),
    'recorder_rows_total': ('rows', 0.0),
    'recorder_rows_common_entities': ('rows', 0.0),
    'database_bytes': ('bytes', 4096.0),
}


def metric_value(result: dict[str, Any], path: str) -> float | None:
    """Read a nested metric without substituting zero for unavailable data."""
    value: Any = result
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return float(value) if isinstance(value, (int, float)) else None


def compare(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag provisional regressions beyond relative, absolute and observed baseline noise."""
    rows = []
    for path, (unit, absolute) in METRICS.items():
        samples = {role: [v for r in runs if r['role'] == role and
                         (v := metric_value(r['result'], path)) is not None]
                   for role in ('baseline', 'candidate')}
        if not all(samples.values()):
            rows.append({'metric': path, 'unit': unit, 'status': 'not_measured'})
            continue
        old, new = [statistics.median(samples[role]) for role in ('baseline', 'candidate')]
        noise = max(samples['baseline']) - min(samples['baseline'])
        delta = new - old
        rows.append({'metric': path, 'unit': unit, 'baseline': old, 'candidate': new,
            'delta': delta, 'delta_percent': delta / old * 100 if old > 0 else None,
            'baseline_run_range': noise, 'threshold_absolute': max(absolute, old * .15, noise),
            'status': 'investigate' if delta > max(absolute, old * .15, noise) else 'within_guardrail',
            'run_values': samples})
    return rows


def guardrails(result: dict[str, Any], repeats: int, stations: int) -> list[str]:
    """Validate correctness independently from noisy performance observations."""
    failures = []
    for key in ('fuel_mismatches', 'identity_changes_after_reload',
                'identity_changes_from_persisted', 'tasks_after_unload',
                'timers_after_unload', 'listeners_after_unload'):
        if key not in result or result[key]:
            failures.append(key)
    if result.get('fuel_availability_end_fraction') != 1:
        failures.append('fuel_availability_end_fraction')
    if result.get('refresh_station_requests') != repeats * stations:
        failures.append('refresh_station_requests')
    if result.get('refresh_successful_network_requests') != repeats * stations:
        failures.append('refresh_successful_network_requests')
    for phase in ('refresh', 'idle'):
        if result.get('fuel_availability_samples', {}).get(phase, {}).get('fraction') != 1:
            failures.append(f'fuel_availability_{phase}')
    if result.get('refresh_csv_requests') != 0:
        failures.append('refresh_csv_requests')
    if result.get('csv_304_count') != repeats or result.get('csv_conditional_bytes') != 0:
        failures.append('conditional_csv_download')
    listeners = result.get('listeners_after_reloads', [])
    if listeners and any(value != listeners[0] for value in listeners):
        failures.append('listener_growth')
    timers = result.get('timers_after_reloads', [])
    if timers and any(value != timers[0] for value in timers):
        failures.append('timer_growth')
    return failures


def write_report(output: Path, report: dict[str, Any]) -> None:
    """Write machine-readable evidence and a compact human-readable comparison."""
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = ['# Release KPI comparison', '',
        f"Baseline: `{report['sources']['baseline']['version']}`. Candidate: `{report['sources']['candidate']['version']}`.",
        '', f"HA image ID: `{report['image_id']}`.", '',
        'Real HA, real HTTP and Recorder, replayed real MIMIT snapshot. No live upstream latency claim.',
        'Performance flags are provisional: 15% plus an absolute floor and observed baseline range.',
        'Run-level ranges are not confidence intervals; short runs do not establish absence of memory leaks.', '',
        '| Metric | Baseline | Candidate | Change | Status |', '|---|---:|---:|---:|---|']
    for row in report['comparison']:
        if row['status'] == 'not_measured':
            lines.append(f"| {row['metric']} | — | — | — | not measured |")
        else:
            percent = row['delta_percent']
            change = f'{percent:+.1f}%' if percent is not None else f"{row['delta']:+.2f}"
            lines.append(f"| {row['metric']} [{row['unit']}] | {row['baseline']:.3f} | {row['candidate']:.3f} | {change} | {row['status']} |")
    if 'input_quality' in report:
        quality = report['input_quality']
        lines += ['', f"Input quality: {quality['registry_rows']} rows, {quality['usable_station_ids']} usable station IDs, "
                  f"{quality['invalid_coordinate_or_id_rows']} rows without usable coordinates/ID. "
                  f"{quality['source_prices_older_than_24h']}/{quality['fuel_count']} source prices already older than 24h at capture."]
    if 'common_entity_count' in report:
        lines += ['', f"Recorder comparison on common entities uses {report['common_entity_count']} shared unique IDs. "
                  'Total rows also include entities introduced by the candidate.']
    lines += ['', 'Correctness guardrails: ' + json.dumps(report['guardrail_failures']), '',
              'Not measured in this run: ' + '; '.join(report['not_measured']) + '.', '',
              'Raw per-run samples, identities, request counts, memory trajectories, event-loop delay by phase,',
              'and Recorder counts are in report.json. Container logs and profiles are retained beside it.']
    (output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> None:
    """Capture evidence and compare stable and candidate code without touching live HA."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='previous-stable',
                        help='Git ref, or previous-stable to resolve the nearest stable tag before HEAD')
    parser.add_argument('--candidate', default='working-tree')
    parser.add_argument('--image', default=DEFAULT_IMAGE)
    parser.add_argument('--output', type=Path, default=Path('artifacts/kpi') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--station-ids', nargs='+', default=['54233', '54234', '54404', '54235'])
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--repeats', type=int, default=10)
    parser.add_argument('--reloads', type=int, default=5)
    parser.add_argument('--idle-seconds', type=int, default=60)
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--cpus', type=float, default=2)
    parser.add_argument('--memory', default='1g')
    parser.add_argument('--fail-on-performance', action='store_true')
    args = parser.parse_args()
    if min(args.rounds, args.repeats, args.reloads, args.idle_seconds, args.timeout) < 1:
        parser.error('Counts and durations must be positive')
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    image = json.loads(run(['docker', 'image', 'inspect', args.image]))[0]
    if args.baseline == 'previous-stable':
        args.baseline = run(['git', '-C', str(repo), 'describe', '--tags',
                             '--match', 'v[0-9]*', '--exclude', '*-*', '--abbrev=0', 'HEAD^'])
    (output / 'scripts').mkdir()
    shutil.copyfile(repo / 'scripts/ha_kpi_probe.py', output / 'scripts/ha_kpi_probe.py')
    shutil.copyfile(Path(__file__), output / 'scripts/benchmark_release.py')
    sources = {}
    for role, ref in [('baseline', args.baseline), ('candidate', args.candidate)]:
        sources[role] = export_source(repo, ref, output / f'source-{role}')
    inputs = (args.inputs or output / 'inputs').resolve()
    print('Capturing/verifying upstream snapshot', flush=True)
    snapshot = capture_inputs(inputs, args.station_ids)
    report: dict[str, Any] = {
        'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'sources': sources, 'image_id': image['Id'], 'image_digests': image.get('RepoDigests'),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'input_quality': input_quality(inputs, snapshot['captured_at']),
        'inputs': snapshot, 'probe_sha256': hashlib.sha256((output / 'scripts/ha_kpi_probe.py').read_bytes()).hexdigest(),
        'settings': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        'runs': [], 'guardrail_failures': {},
        'not_measured': ['24–72 hour memory slope', 'production availability over time',
            'live upstream latency and freshness', 'full retry/backoff and outage recovery',
            '1/10/50 station scaling', 'cron and opening-hours boundary delay',
            'independent geographic search oracle', 'OS-level disk write bytes'],
    }
    (output / 'provenance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    for round_index in range(args.rounds):
        order = ('baseline', 'candidate') if round_index % 2 == 0 else ('candidate', 'baseline')
        for role in order:
            name = f'round-{round_index + 1}-{role}'
            print(f'Measuring {name}', flush=True)
            result = measure(image['Id'], output / f'source-{role}', output / name, inputs, args, output)
            report['runs'].append({'role': role, 'round': round_index + 1, 'result': result})
            report['guardrail_failures'][name] = guardrails(result, args.repeats, len(args.station_ids))
            (output / 'partial-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Measuring stable-to-candidate upgrade with preserved HA storage', flush=True)
    upgraded = measure(image['Id'], output / 'source-candidate', output / 'upgrade', inputs,
                       args, output, output / 'round-1-baseline')
    report['upgrade'] = upgraded
    report['guardrail_failures']['upgrade'] = guardrails(upgraded, args.repeats, len(args.station_ids))
    baseline_identities = report['runs'][0]['result']['identities']
    report['cross_version_identity_changes'] = sum(
        upgraded['identities'].get(uid) != eid for uid, eid in baseline_identities.items())
    if report['cross_version_identity_changes']:
        report['guardrail_failures']['upgrade'].append('cross_version_identity_changes')
    for measurement in report['runs'] + [{'role': 'upgrade', 'result': upgraded}]:
        if measurement['result']['registry_station_count'] != report['input_quality']['usable_station_ids']:
            report['guardrail_failures'].setdefault('registry', []).append('source_registry_coverage')
    hashes = {r['result']['registry_content_sha256'] for r in report['runs']}
    if len(hashes) != 1 or upgraded['registry_content_sha256'] not in hashes:
        report['guardrail_failures'].setdefault('registry', []).append('decoded_registry_changed')
    add_entity_scope_metrics(report)
    report['comparison'] = compare(report['runs'])
    write_report(output, report)
    print(f'Report: {output / "report.md"}', flush=True)
    if any(report['guardrail_failures'].values()):
        raise SystemExit(1)
    if args.fail_on_performance and any(r['status'] == 'investigate' for r in report['comparison']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
