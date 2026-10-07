"""Protect KPI decisions from missing evidence and misleading percentage changes."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope='module')
def benchmark():
    """Load the standalone runner without launching any containers."""
    path = Path(__file__).parents[1] / 'scripts/benchmark_release.py'
    spec = importlib.util.spec_from_file_location('benchmark_release', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_metrics_are_not_zero(benchmark):
    rows = benchmark.compare([{'role': 'baseline', 'result': {'registry_cache_bytes': 100}},
                              {'role': 'candidate', 'result': {}}])
    assert all(row['status'] == 'not_measured' for row in rows)
    assert benchmark.metric_value({'x': {'y': 0}}, 'x.y') == 0
    assert benchmark.metric_value({'x': {'y': None}}, 'x.y') is None


def test_flags_require_absolute_relative_and_noise_thresholds(benchmark):
    def row(old, new):
        results = [{'role': 'baseline', 'result': {'setup_to_entities_ms': v}} for v in old]
        results += [{'role': 'candidate', 'result': {'setup_to_entities_ms': v}} for v in new]
        return benchmark.compare(results)[0]
    assert row([10, 10], [15, 15])['status'] == 'within_guardrail'
    assert row([100, 200], [170, 170])['status'] == 'within_guardrail'
    assert row([100, 101], [200, 201])['status'] == 'investigate'
    assert row([100, 100], [70, 70])['delta_percent'] == -30


def test_correctness_guardrails_do_not_accept_hidden_cache_success(benchmark):
    result = {'fuel_availability_end_fraction': 1, 'refresh_station_requests': 8,
              'refresh_csv_requests': 0, 'refresh_successful_network_requests': 8,
              'fuel_availability_samples': {'refresh': {'fraction': 1}, 'idle': {'fraction': 1}},
              'csv_304_count': 2,
              'csv_conditional_bytes': 0, 'listeners_after_reloads': [12, 12],
              'fuel_mismatches': [], 'identity_changes_after_reload': 0,
              'identity_changes_from_persisted': 0, 'tasks_after_unload': [],
              'timers_after_unload': 0, 'listeners_after_unload': 0}
    assert benchmark.guardrails(result, 2, 4) == []
    result['refresh_successful_network_requests'] = 0
    result['fuel_mismatches'] = ['54233_Benzina_self']
    result['listeners_after_reloads'] = [12, 14]
    failures = benchmark.guardrails(result, 2, 4)
    assert 'fuel_mismatches' in failures
    assert 'listener_growth' in failures
    assert 'refresh_successful_network_requests' in failures


def test_snapshot_reuse_verifies_actual_bytes(benchmark, tmp_path):
    import hashlib
    import json
    (tmp_path / 'registry.csv').write_bytes(b'first')
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'station_ids': ['1'], 'sha256': {'registry.csv': hashlib.sha256(b'first').hexdigest()}}))
    assert benchmark.capture_inputs(tmp_path, ['1'])['station_ids'] == ['1']
    (tmp_path / 'registry.csv').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Snapshot hash mismatch'):
        benchmark.capture_inputs(tmp_path, ['1'])


def test_export_isolated_from_current_changes(benchmark, tmp_path):
    repo = Path(__file__).parents[1]
    result = benchmark.export_source(repo, 'v2.6.0', tmp_path / 'stable')
    assert result['version'] == '2.6.0'
    assert result['commit'] == '119610b0f34cbafff382217c179286b2d17b99e8'
    assert len(result['source_sha256']) == 64


def test_independent_source_coverage_and_freshness(benchmark, tmp_path):
    import json
    (tmp_path / 'registry.csv').write_text(
        '2026-10-06\n'
        'idImpianto;Latitudine;Longitudine\n'
        '1;41,9;12,5\n'
        '1;41.9;12.5\n'
        '2;nan;12.5\n'
        '3;91;12.5\n'
    )
    (tmp_path / 'stations.json').write_text(json.dumps({'1': {'fuels': [
        {'insertDate': '2026-10-05T00:00:00Z'},
        {'insertDate': '2026-10-06T20:00:00Z'},
    ]}}))
    quality = benchmark.input_quality(tmp_path, '2026-10-07T00:00:00+00:00')
    assert quality['registry_rows'] == 4
    assert quality['distinct_station_ids'] == 3
    assert quality['duplicate_ids'] == 1
    assert quality['usable_station_ids'] == 1
    assert quality['invalid_coordinate_or_id_rows'] == 2
    assert quality['price_ages_hours_at_capture']['1'] == [48, 4]
    assert quality['source_prices_older_than_24h'] == 1



def test_unknown_price_age_is_not_reported_as_fresh(benchmark, tmp_path):
    import json
    (tmp_path / 'registry.csv').write_text('idImpianto;Latitudine;Longitudine\n1;41;12\n')
    (tmp_path / 'stations.json').write_text(json.dumps({'1': {'fuels': [
        {}, {'insertDate': None}, {'insertDate': '2026-10-06T00:00:00'},
    ]}}))
    quality = benchmark.input_quality(tmp_path, '2026-10-07T00:00:00+00:00')
    assert quality['unknown_price_timestamps'] == 3
    assert quality['price_ages_hours_at_capture']['1'] == []
    assert quality['fuel_count'] == 3



def test_memory_growth_uses_observed_samples_and_preserves_missingness(benchmark):
    result = {'rss_after_refresh_mib': [300, 310], 'rss_after_reload_mib': [310, 311, 312]}
    benchmark.add_memory_metrics(result)
    assert result['rss_steady_mib'] == 310
    assert result['rss_reload_growth_mib'] == 2
    missing = {}
    benchmark.add_memory_metrics(missing)
    assert missing == {'rss_steady_mib': None, 'rss_reload_growth_mib': None}



def test_negative_memory_growth_has_no_misleading_percentage(benchmark):
    rows = benchmark.compare([
        {'role': 'baseline', 'result': {'rss_reload_growth_mib': -1}},
        {'role': 'candidate', 'result': {'rss_reload_growth_mib': 1}},
    ])
    growth = next(row for row in rows if row['metric'] == 'rss_reload_growth_mib')
    assert growth['delta'] == 2
    assert growth['delta_percent'] is None



def test_recorder_comparison_matches_unique_ids_across_fresh_names(benchmark):
    report = {'runs': [
        {'result': {'identities': {'shared': 'sensor.old'},
                    'recorder_rows_by_entity': {'sensor.old': 10}}},
        {'result': {'identities': {'shared': 'sensor.new', 'added': 'button.new'},
                    'recorder_rows_by_entity': {'sensor.new': 11, 'button.new': 50}}},
    ]}
    benchmark.add_entity_scope_metrics(report)
    assert report['common_entity_count'] == 1
    assert [run['result']['recorder_rows_common_entities'] for run in report['runs']] == [10, 11]
