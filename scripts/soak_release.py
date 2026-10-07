"""Run two frozen integration versions in disposable HA containers for a bounded soak."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import statistics
import subprocess
import time
from typing import Any
import uuid

import benchmark_release as benchmark

PINNED_IMAGE = 'ghcr.io/home-assistant/home-assistant@sha256:3e6710a7ab2a61311d9d899b719f6c3657791c63e8f4942cec4ebc42401d6b76'


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def slope(samples: list[dict[str, Any]], key: str, *, warmup_hours: float = 1) -> float | None:
    """Return least-squares units/hour only after at least six observed hours."""
    rows = [s for s in samples if s['elapsed_seconds'] >= warmup_hours * 3600 and s['phase'] == 'steady']
    if len(rows) < 3 or rows[-1]['elapsed_seconds'] - rows[0]['elapsed_seconds'] < 6 * 3600:
        return None
    x = [row['elapsed_seconds'] / 3600 for row in rows]
    y = [float(row[key]) for row in rows]
    mx, my = statistics.mean(x), statistics.mean(y)
    denominator = sum((value - mx) ** 2 for value in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / denominator if denominator else None


def summarize(result: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Preserve measured duration and keep short runs out of long-term conclusions."""
    due = result.get('scheduled_callbacks', [])
    lateness = [s['lateness_ms'] for s in due if s.get('fired')]
    lag = sorted(lateness)
    hours = result.get('duration_seconds', 0) / 3600
    common = {'observed_hours': hours, 'status': result['status'], 'failures': result.get('failures', []),
              'rss_mib_per_hour_after_first_hour': slope(samples, 'rss_mib'),
              'scheduled_callbacks': len(due), 'missing_callbacks': sum(not s.get('fired') for s in due),
              'cron_lateness_p95_ms': lag[max(0, math.ceil(len(lag) * .95) - 1)] if lag else None,
              'cron_lateness_max_ms': max(lag) if lag else None,
              'faults': [e for e in result.get('events', []) if e['kind'] == 'fault'],
              'reloads': [e for e in result.get('events', []) if e['kind'] == 'reload']}
    if samples:
        common.update(rss_first_mib=samples[0]['rss_mib'], rss_last_mib=samples[-1]['rss_mib'],
                      rss_sampled_peak_mib=max(s['rss_mib'] for s in samples),
                      recorder_first=samples[0]['rows_by_unique_id'], recorder_last=samples[-1]['rows_by_unique_id'],
                      database_bytes_first=samples[0]['database_bytes'], database_bytes_last=samples[-1]['database_bytes'],
                      wal_bytes_last=samples[-1]['wal_bytes'], availability=samples[-1]['availability'])
    return common


def build_profile(repo: Path, source: Path, profile: Path, args: argparse.Namespace) -> None:
    """Reuse the isolated KPI profile and replace only its measurement implementation."""
    benchmark.prepare_profile(repo, source, profile, argparse.Namespace(repeats=1, reloads=1, idle_seconds=1))
    probe = profile / 'custom_components/ha_kpi_probe'
    shutil.copyfile(repo / 'scripts/ha_kpi_probe.py', probe / 'helpers.py')
    shutil.copyfile(repo / 'scripts/ha_soak_probe.py', probe / '__init__.py')
    faults = [args.fault_first, args.fault_second] if not args.no_faults else []
    config = (profile / 'configuration.yaml').read_text().split('ha_kpi_probe:')[0]
    config += f'''ha_kpi_probe:
  duration_seconds: {args.duration}
  warmup_seconds: {args.warmup}
  sample_seconds: {args.sample_seconds}
  reload_seconds: {args.reload_seconds}
  cron: "{args.cron}"
  fault_offsets_seconds: {json.dumps(faults)}
'''
    (profile / 'configuration.yaml').write_text(config, encoding='utf-8')


def finalize(output: Path, records: list[dict[str, Any]], provenance: dict[str, Any]) -> dict[str, Any]:
    """Write a report after clean shutdown, with common-entity Recorder normalization."""
    report: dict[str, Any] = {'provenance': provenance, 'runs': [], 'scope':
        'Two concurrent real HA processes with captured HTTP replay. Whole-process RSS includes probe overhead. '
        'Fixed upstream prices; no live price freshness, MIMIT uptime, or production disk-write claim.'}
    for record in records:
        profile = Path(record['profile'])
        result_path = profile / 'soak-result.json'
        result = json.loads(result_path.read_text()) if result_path.exists() else {'status': 'failed', 'failures': ['missing_result']}
        samples = [json.loads(line) for line in (profile / 'soak-samples.jsonl').read_text().splitlines()] if (profile / 'soak-samples.jsonl').exists() else []
        if record.get('log_traceback'):
            result.setdefault('failures', []).append('ha_log_traceback')
            result['status'] = 'failed'
        if record.get('shutdown', {}).get('ExitCode') != 0 or record.get('shutdown', {}).get('OOMKilled'):
            result.setdefault('failures', []).append('unclean_shutdown')
            result['status'] = 'failed'
        final_counts = None
        # Read committed Recorder rows after shutdown without altering the samples.
        import sqlite3
        if (profile / 'home-assistant_v2.db').exists():
            with sqlite3.connect(f'file:{profile / "home-assistant_v2.db"}?mode=ro', uri=True) as connection:
                rows = connection.execute('''SELECT sm.entity_id, COUNT(*) FROM states s
                    JOIN states_meta sm ON s.metadata_id=sm.metadata_id GROUP BY sm.entity_id''').fetchall()
            by_id = dict(rows)
            final_counts = {uid: by_id.get(eid, 0) for uid, eid in result.get('identities', {}).items()}
        summary = summarize(result, samples)
        summary['recorder_final_rows_by_unique_id'] = final_counts
        report['runs'].append({'role': record['role'], 'source': record['source'], 'shutdown': record.get('shutdown'),
                               'summary': summary, 'result': result, 'samples': samples})
    identities = [set(run['summary'].get('recorder_final_rows_by_unique_id') or {}) for run in report['runs']]
    shared = set.intersection(*identities) if identities else set()
    report['shared_unique_ids'] = sorted(shared)
    for run in report['runs']:
        counts = run['summary'].get('recorder_final_rows_by_unique_id') or {}
        run['summary']['recorder_rows_common_entities'] = sum(counts[uid] for uid in shared)
    report['status'] = 'completed' if all(r['summary']['status'] == 'completed' for r in report['runs']) else 'failed'
    _write(output / 'report.json', report)
    text = ['# HA duration comparison', '', report['scope'], '', '| Version | Observed hours | RSS first/last MiB | RSS MiB/hour after first hour | Cron callbacks | p95 lateness ms | Common Recorder rows | Result |', '|---|---:|---:|---:|---:|---:|---:|---|']
    for run in report['runs']:
        s = run['summary']
        text.append(f"| {run['source']['version']} | {s['observed_hours']:.3f} | {s.get('rss_first_mib')} / {s.get('rss_last_mib')} | {s['rss_mib_per_hour_after_first_hour']} | {s['scheduled_callbacks']} | {s['cron_lateness_p95_ms']} | {s['recorder_rows_common_entities']} | {s['status']} |")
    for run in report['runs']:
        text.extend(['', f"Failures for {run['role']}: {run['summary']['failures']}"])
    text.extend(['', 'Raw samples, fault recovery, reload durations and resource cleanup are in report.json.', 'RSS slope is descriptive, not proof of a leak. Short runs have no slope. Thresholds require repeated runs.'])
    (output / 'report.md').write_text('\n'.join(text) + '\n', encoding='utf-8')
    return report


def main() -> int:
    """Freeze inputs and sources, supervise bounded containers and retain final evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='v2.6.0')
    parser.add_argument('--candidate', default='working-tree')
    parser.add_argument('--image', default=PINNED_IMAGE)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration', type=float, default=86400)
    parser.add_argument('--warmup', type=float, default=600)
    parser.add_argument('--sample-seconds', type=float, default=60)
    parser.add_argument('--reload-seconds', type=float, default=7200)
    parser.add_argument('--fault-first', type=float, default=3600)
    parser.add_argument('--fault-second', type=float, default=43200)
    parser.add_argument('--no-faults', action='store_true')
    parser.add_argument('--cron', default='*/5 * * * *')
    parser.add_argument('--cpus', type=float, default=1)
    parser.add_argument('--memory', default='1g')
    args = parser.parse_args()
    def terminate(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt('Supervised soak stopped')
    signal.signal(signal.SIGTERM, terminate)
    if min(args.duration, args.sample_seconds, args.reload_seconds, args.cpus) <= 0 or args.warmup < 0:
        parser.error('Durations and resources must be positive; warmup may be zero')
    if not args.no_faults and not (0 <= args.fault_first < args.fault_second < args.duration):
        parser.error('Fault offsets must be ordered and fall within the observation')
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen_repo = output / 'frozen'
    (frozen_repo / 'scripts').mkdir(parents=True)
    for name in ('soak_release.py', 'ha_soak_probe.py', 'ha_kpi_probe.py', 'benchmark_release.py'):
        shutil.copyfile(repo / 'scripts' / name, frozen_repo / 'scripts' / name)
    inputs = output / 'inputs'
    shutil.copytree(args.inputs.resolve(), inputs)
    stations = json.loads((inputs / 'stations.json').read_text())
    snapshot = benchmark.capture_inputs(inputs, list(stations))
    image = json.loads(benchmark.run(['docker', 'image', 'inspect', args.image]))[0]
    records: list[dict[str, Any]] = []
    provenance = {'created_utc': datetime.now(timezone.utc).isoformat(), 'image_id': image['Id'],
                  'image_digests': image.get('RepoDigests'), 'inputs': snapshot,
                  'host_cpu_count': os.cpu_count(), 'host_load_at_start': os.getloadavg(),
                  'settings': {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
                  'scripts_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (frozen_repo / 'scripts').iterdir()}}
    try:
        for role, ref in [('baseline', args.baseline), ('candidate', args.candidate)]:
            source_root = output / f'source-{role}'
            source = benchmark.export_source(repo, ref, source_root)
            profile = output / f'profile-{role}'
            build_profile(frozen_repo, source_root, profile, args)
            name = f'ha-soak-{role}-' + uuid.uuid4().hex[:8]
            record = {'role': role, 'source': source, 'profile': str(profile), 'container': name}
            # Record ownership before launching so cleanup never touches unrelated containers.
            records.append(record)
            _write(output / 'status.json', {'status': 'starting', 'runs': records, 'provenance': provenance})
            print(f"Starting {role} {source['version']} in {name}, planned {args.duration:g}s after warmup", flush=True)
            benchmark.run(['docker', 'run', '-d', '--name', name, '--label', 'osservaprezzi.kpi=soak',
                           '--add-host', 'carburanti.mise.gov.it:127.0.0.1', '--add-host', 'www.mimit.gov.it:127.0.0.1',
                           '--cpus', str(args.cpus), '--memory', args.memory,
                           '-v', f'{profile}:/config', '-v', f'{inputs}:/inputs:ro', image['Id']])
        deadline = time.monotonic() + args.duration + args.warmup + 1200
        while time.monotonic() < deadline:
            progress = []
            complete = True
            for record in records:
                profile = Path(record['profile'])
                path = profile / 'soak-progress.json'
                progress.append({'role': record['role'], 'progress': json.loads(path.read_text()) if path.exists() else None})
                result_path = profile / 'soak-result.json'
                if result_path.exists() and json.loads(result_path.read_text())['status'] != 'completed':
                    raise RuntimeError(f"{record['role']} probe failed; see {result_path}")
                if not result_path.exists():
                    complete = False
                    if benchmark.run(['docker', 'inspect', '--format', '{{.State.Running}}', record['container']]) != 'true':
                        raise RuntimeError(f"{record['role']} exited before producing a result")
            _write(output / 'status.json', {'status': 'running', 'runs': records, 'progress': progress, 'host_load': os.getloadavg(), 'provenance': provenance})
            if complete:
                break
            time.sleep(10)
        else:
            raise TimeoutError('Soak exceeded its duration and startup allowance')
    except BaseException as err:
        _write(output / 'runner-error.json', {'error': f'{type(err).__name__}: {err}'})
        raise
    finally:
        for record in records:
            name = record['container']
            inspected = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
            if inspected.returncode:
                record['shutdown'] = {'ExitCode': -1, 'OOMKilled': False, 'missing_container': True}
                continue
            logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True)
            combined_logs = logs.stdout + logs.stderr
            record['log_traceback'] = 'Traceback (most recent call last)' in combined_logs
            (output / f"{record['role']}.log").write_text(combined_logs, encoding='utf-8')
            stop = subprocess.run(['docker', 'stop', '--time', '30', name], capture_output=True, text=True, timeout=60)
            record['shutdown'] = json.loads(benchmark.run(['docker', 'inspect', name]))[0]['State']
            if stop.returncode:
                subprocess.run(['docker', 'kill', name], capture_output=True, text=True)
            benchmark.run(['docker', 'rm', name])
            benchmark.run(['docker', 'run', '--rm', '--entrypoint', 'chown', '-v', f"{record['profile']}:/config",
                           image['Id'], '-R', f'{os.getuid()}:{os.getgid()}', '/config'])
        report = finalize(output, records, provenance)
        _write(output / 'status.json', {'status': report['status'], 'runs': records, 'provenance': provenance})
    print(f"Soak {report['status']}: {output / 'report.json'}", flush=True)
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
