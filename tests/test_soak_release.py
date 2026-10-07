"""Keep duration claims and Recorder normalization grounded in observed evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture
def soak(monkeypatch):
    scripts = Path(__file__).parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('soak_release', scripts / 'soak_release.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_short_observation_cannot_claim_memory_slope(soak):
    samples = [{'elapsed_seconds': i * 60, 'phase': 'steady', 'rss_mib': 100 + i} for i in range(120)]
    assert soak.slope(samples, 'rss_mib') is None
    report = soak.summarize({'status': 'completed', 'duration_seconds': 90}, [])
    assert report['observed_hours'] == 90 / 3600
    assert report['cron_lateness_p95_ms'] is None
    assert report['rss_mib_per_hour_after_first_hour'] is None


def test_memory_slope_excludes_startup_and_fault_spikes(soak):
    samples = [{'elapsed_seconds': i * 3600, 'phase': 'steady', 'rss_mib': 100 + 2 * i} for i in range(9)]
    samples[0]['rss_mib'] = 999
    samples.insert(4, {'elapsed_seconds': 3.5 * 3600, 'phase': 'outage', 'rss_mib': 999})
    assert soak.slope(samples, 'rss_mib') == pytest.approx(2)


def test_failed_fault_and_missing_callback_are_preserved(soak):
    result = {'status': 'failed', 'duration_seconds': 24 * 3600, 'failures': ['recovery_network_or_fuel_state'],
              'scheduled_callbacks': [{'fired': False}, {'fired': True, 'lateness_ms': 15}],
              'events': [{'kind': 'fault', 'recovery_seconds': 80}, {'kind': 'reload', 'duration_seconds': 9}]}
    summary = soak.summarize(result, [])
    assert summary['status'] == 'failed'
    assert summary['missing_callbacks'] == 1
    assert summary['cron_lateness_p95_ms'] == 15
    assert summary['faults'][0]['recovery_seconds'] == 80
    assert summary['reloads'][0]['duration_seconds'] == 9
    assert summary['failures'] == result['failures']


def test_final_report_normalizes_common_unique_ids_and_rejects_unclean_shutdown(soak, tmp_path):
    records = []
    for role, identities, rows in [('baseline', {'price': 'sensor.old'}, ['sensor.old'] * 2),
                                   ('candidate', {'price': 'sensor.new', 'button': 'button.refresh'},
                                    ['sensor.new'] * 3 + ['button.refresh'] * 4)]:
        profile = tmp_path / role
        profile.mkdir()
        (profile / 'soak-result.json').write_text(json.dumps({'status': 'completed', 'identities': identities,
                                                           'duration_seconds': 86400}))
        with sqlite3.connect(profile / 'home-assistant_v2.db') as connection:
            connection.executescript('CREATE TABLE states (metadata_id INTEGER); CREATE TABLE states_meta (metadata_id INTEGER, entity_id TEXT);')
            for mid, eid in enumerate(identities.values()):
                connection.execute('INSERT INTO states_meta VALUES (?, ?)', (mid, eid))
                connection.executemany('INSERT INTO states VALUES (?)', [(mid,)] * rows.count(eid))
        records.append({'role': role, 'source': {'version': role}, 'profile': str(profile),
                        'log_traceback': role == 'candidate',
                        'shutdown': {'ExitCode': 0 if role == 'baseline' else 137, 'OOMKilled': role == 'candidate'}})
    report = soak.finalize(tmp_path, records, {})
    assert report['shared_unique_ids'] == ['price']
    assert [r['summary']['recorder_rows_common_entities'] for r in report['runs']] == [2, 3]
    assert report['status'] == 'failed'
    assert 'unclean_shutdown' in report['runs'][1]['summary']['failures']
    assert 'ha_log_traceback' in report['runs'][1]['summary']['failures']
    assert (tmp_path / 'report.json').exists()
