"""Clock counterexamples are synthetic unit fixtures, never training examples."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.profile_offtrail import clock_diagnostic, statistics, verified_archive


ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'data/research/offtrail_lidar_2025'


def test_statistics_preserves_nonfinite_and_extreme_counts():
    summary = statistics([0, 1, 2, 100, -1, np.nan, np.inf])
    assert summary['count'] == 7 and summary['nonfinite'] == 2
    assert summary['median'] == 1 and summary['max'] == 100
    assert summary['zero'] == summary['negative'] == 1
    json.dumps(summary, allow_nan=False)


def test_rounded_clock_does_not_license_unit_conversion():
    offset = np.arange(1025.)
    gps = np.round((1_690_000_000 + offset) / 128) * 128
    bounds = clock_diagnostic(offset, gps)
    assert bounds['lower'] <= 1 <= bounds['upper']
    assert not bounds['lower'] <= .001 <= bounds['upper']
    # A genuinely millisecond counter must give a different scale.
    ms_bounds = clock_diagnostic(offset * 1000, gps)
    assert ms_bounds['lower'] <= .001 <= ms_bounds['upper']
    assert not ms_bounds['lower'] <= 1 <= ms_bounds['upper']


def test_clock_diagnostic_rejects_shapes_and_reports_contradictions():
    with pytest.raises(ValueError, match='equal-length'):
        clock_diagnostic([0, 1], [0])
    bounds = clock_diagnostic([0, 1000, 2000], [0, 1000, 0])
    assert bounds['compatible_under_assumption'] is False
    assert clock_diagnostic([1], [3])['upper'] is None


def test_changed_source_fails_before_parsing(tmp_path):
    manifest = {'license': 'CC-BY-4.0',
                'files': [{'path': 'trajectories.zip', 'sha256': '0' * 64}]}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    (tmp_path / 'trajectories.zip').write_bytes(b'altered fixture, not training data')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        verified_archive(tmp_path)


def test_published_audit_matches_sources_and_does_not_emit_training_labels():
    report = json.loads((FOLDER / 'quality_report.json').read_text())
    assert report['schema'] == 'offtrail-quality-v1'
    for source in report['source_files']:
        assert hashlib.sha256((FOLDER / source['path']).read_bytes()).hexdigest() == source['sha256']
    assert report['profiler_sha256'] == hashlib.sha256((ROOT / 'pipeline/profile_offtrail.py').read_bytes()).hexdigest()
    assert report['files'] == len(report['tracks']) == 165
    assert report['points'] == sum(t['points'] for t in report['tracks']) == 39962
    assert report['consecutive_pairs'] == 39797
    assert report['crs_counts'] == {'EPSG:26912': 165}
    assert report['offset_deltas']['min'] == report['offset_deltas']['max'] == 1
    assert report['gps_deltas']['zero'] == 39480
    assert report['gps_deltas']['unique_finite'] == 2 and report['gps_deltas']['max'] == 128
    assert report['exact_duplicate_files'] == report['unparsed_filenames'] == []
    assert report['training_rows_emitted'] == 0 and report['trained_bundle_modified'] is False
    assert not report['token_semantics_verified']
    assert not any(gate['passed'] for gate in report['gates'])
    assert all(not t['speed_training_eligible'] and not t['lost_person_training_eligible'] for t in report['tracks'])
    clock = report['clock_diagnostic']
    assert clock['pairs_checked'] == 7086965
    assert not clock['assumption_verified'] and not clock['units_verified']
    assert not clock['milliseconds_compatible_under_assumption']
    assert clock['seconds_compatible_under_assumption']
