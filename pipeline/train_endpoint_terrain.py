"""Fit and evaluate terrain-conditioned endpoints under a frozen protocol.

Produces an immutable research run; never overwrites models/current.json.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import tempfile

import numpy as np
import scipy

from pipeline.terrain_endpoint_model import fit, radial_fit, evaluate_one, split_indices

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/endpoint_terrain_2026'
PROTOCOL = ROOT / 'docs/AI-지형끝점-사전검증계획.md'
SEED = 20260919


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def load_data():
    data_id = json.loads((OUT/'prepared.json').read_text())['data_id']
    if len(data_id) != 64 or any(c not in '0123456789abcdef' for c in data_id):
        raise ValueError('Invalid data identity')
    folder = OUT/data_id
    raw = (folder/'manifest.json').read_bytes()
    if sha(raw) != data_id:
        raise ValueError('Prepared manifest hash mismatch')
    manifest = json.loads(raw)
    checks = [
        (ROOT/'data/research/prepared/incident_endpoints.csv', manifest['source_sha256']),
        (folder/'features.npz', manifest['features_sha256']),
        (PROTOCOL, manifest['protocol_sha256']),
        (ROOT/'pipeline/prepare_endpoint_terrain.py', manifest['prepare_code_sha256']),
        (ROOT/'pipeline/terrain_endpoint_model.py', manifest['model_code_sha256']),
    ]
    for path, expected in checks:
        if sha(path.read_bytes()) != expected:
            raise ValueError('Input/protocol/code hash mismatch: ' + path.name)
    for name, item in zip(('tile-list', 'license', 'provider-readme'), manifest['provider_files'], strict=True):
        if sha((OUT/'assets'/name/'source').read_bytes()) != item['sha256']:
            raise ValueError('Provider documentation hash mismatch: ' + name)
    # Verify archived tiles too; values are not fetched during training.
    for tile in manifest['tiles']:
        if sha((OUT/'assets'/tile['name']/'source').read_bytes()) != tile['sha256']:
            raise ValueError('Archived tile hash mismatch')
    with np.load(folder/'features.npz', allow_pickle=False) as arrays:
        features = arrays['features']
    if list(features.shape) != manifest['shape'] or not np.isfinite(features).all():
        raise ValueError('Invalid prepared features')
    return data_id, manifest, features


def evaluate(cases, features, spatial):
    lat = np.array([c['ipp_lat'] for c in cases])
    lon = np.array([c['ipp_lon'] for c in cases])
    labels = np.array([c['target_cell'] for c in cases])
    distances = np.array([c['distance_m'] for c in cases])
    groups = np.array([c['spatial_group'] if spatial else c['incident_index'] for c in cases])
    predictions, folds, failures = [], [], []
    for group in sorted(set(groups)):
        train, test = split_indices(lat, lon, groups, group, buffered=spatial)
        info = {'test_group': group, 'train_ids': [cases[i]['incident_index'] for i in train],
                'test_ids': [cases[i]['incident_index'] for i in test],
                'buffer_excluded_ids': [c['incident_index'] for i, c in enumerate(cases) if i not in train and i not in test]}
        if len(train) < 20:
            failures.append({**info, 'reason': 'FEWER_THAN_20_TRAINING_CASES'})
            continue
        try:
            model = fit(features[train], labels[train], distances[train])
        except ValueError as error:
            failures.append({**info, 'reason': str(error)})
            continue
        folds.append({**info, 'model': model})
        for i in test:
            baseline = evaluate_one(features[i], labels[i], {'radial': model['radial']})
            candidate = evaluate_one(features[i], labels[i], model)
            predictions.append({'incident_index': cases[i]['incident_index'],
                                'spatial_group': cases[i]['spatial_group'], 'test_group': group,
                                'target_cell': int(labels[i]), 'baseline': baseline,
                                'candidate': candidate, 'delta_nll': candidate['nll'] - baseline['nll']})
        print(('Spatial' if spatial else 'Case'), group, 'train', len(train), 'test', len(test), flush=True)
    predictions.sort(key=lambda r: r['incident_index'])
    if predictions:
        summary = {'cases': len(predictions), 'successful_folds': len(folds), 'failed_folds': len(failures),
                   'baseline_nll': float(np.mean([p['baseline']['nll'] for p in predictions])),
                   'candidate_nll': float(np.mean([p['candidate']['nll'] for p in predictions])),
                   'delta_nll_mean': float(np.mean([p['delta_nll'] for p in predictions])),
                   'delta_nll_median': float(np.median([p['delta_nll'] for p in predictions])),
                   'baseline_area_hits': np.mean([p['baseline']['area_hits'] for p in predictions], axis=0).tolist(),
                   'candidate_area_hits': np.mean([p['candidate']['area_hits'] for p in predictions], axis=0).tolist(),
                   'minimum_training_cases': min(len(f['train_ids']) for f in folds)}
    else:
        summary = {'cases': 0, 'successful_folds': 0, 'failed_folds': len(failures)}
    return {'summary': summary, 'folds': folds, 'failures': failures, 'predictions': predictions}


def bootstrap(predictions):
    if not predictions:
        return {'groups': 0, 'interval': None}
    grouped = {}
    for row in predictions:
        grouped.setdefault(row['spatial_group'], []).append(row['delta_nll'])
    groups = sorted(grouped)
    sums = np.array([sum(grouped[g]) for g in groups])
    counts = np.array([len(grouped[g]) for g in groups])
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(groups), size=(2000, len(groups)))
    delta = sums[indices].sum(axis=1) / counts[indices].sum(axis=1)
    return {'groups': len(groups), 'replicates': 2000, 'seed': SEED,
            'interval': np.quantile(delta, [.025, .975]).tolist(),
            'scope': 'EXPLORATORY_CLUSTER_RESAMPLE_NOT_EXTERNAL_CONFIRMATION'}


def adoption_gate(primary, interval, expected_cases):
    summary = primary['summary']
    conditions = {
        'all_cases_evaluated': summary['cases'] == expected_cases and not primary['failures'],
        'mean_nll_improved': summary.get('delta_nll_mean', float('inf')) < 0,
        'bootstrap_upper_below_zero': interval['interval'] is not None and interval['interval'][1] < 0,
        'area20_not_worse': (summary['cases'] > 0 and
                            summary['candidate_area_hits'][1] >= summary['baseline_area_hits'][1]),
    }
    return {'passed': all(conditions.values()), 'conditions': conditions,
            'production_model_changed': False,
            'status': 'RESEARCH_COMPARISON_CANDIDATE' if all(conditions.values()) else 'NOT_ADOPTED'}


def main():
    data_id, manifest, features = load_data()
    cases = manifest['cases']
    primary = evaluate(cases, features, spatial=True)
    secondary = evaluate(cases, features, spatial=False)
    interval = bootstrap(primary['predictions'])
    gate = adoption_gate(primary, interval, len(cases))
    model = fit(features, np.array([c['target_cell'] for c in cases]), np.array([c['distance_m'] for c in cases]))
    record = {'schema': 'terrain-endpoint-run-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'data_id': data_id, 'protocol_sha256': manifest['protocol_sha256'],
              'training_code_sha256': sha(Path(__file__).read_bytes()),
              'model_code_sha256': manifest['model_code_sha256'],
              'dependencies': {'numpy': np.__version__, 'scipy': scipy.__version__},
              'model': model, 'primary_spatial40km': primary, 'secondary_case_out': secondary,
              'bootstrap_delta': interval, 'adoption': gate,
              'limitations': ['Selected US hiking endpoints, not trajectories or present-time locations',
                              'DSM date and terrain-at-incident date match unknown',
                              'Polar cell center approximation, not 30m validated precision',
                              'Exploratory overlapping training folds; no domestic or field validation',
                              'No learned night, facilities, roads or POD effects'],
              'attribution_file': str((OUT/'NOTICE.md').relative_to(ROOT))}
    raw = canonical(record)
    run_id = sha(raw)
    folder = OUT/'runs'
    folder.mkdir(exist_ok=True)
    path = folder / (run_id + '.json')
    with path.open('xb') as output:
        output.write(raw)
    fd, temp = tempfile.mkstemp(prefix='.latest-', dir=OUT)
    with os.fdopen(fd, 'wb') as output:
        output.write(canonical({'run_id': run_id, 'data_id': data_id, 'adoption': gate}))
    os.replace(temp, OUT/'latest_run.json')
    print(json.dumps({'run_id': run_id, 'primary': primary['summary'],
                      'bootstrap': interval, 'adoption': gate}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
