"""Separate, interval-observed YOSAR lognormal with predeclared evaluation.

.venv-ml/bin/python -m pipeline.train_yosar
Does not fit paths, speed, day/night, facilities, or detection probabilities.
"""
from datetime import datetime, timezone
from pathlib import Path
import copy
import hashlib
import json
import os
import tempfile
import numpy as np
from scipy import optimize, special, stats
from backend import ml_models

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/research/yosar_endpoints_2000_2010'
PROTOCOL = ROOT/'docs/AI-YOSAR-사전검증계획.md'
MODEL = 'yosar_interval_lognorm'
SEED = 20260919
BOUNDS = [(float(np.log(10)), float(np.log(100000))), (.15, 3.)]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def interval_logprob(params, lower, upper):
    """Log interval mass, using survival differences in the upper tail."""
    mu, sigma = params
    lower, upper = np.asarray(lower, float), np.asarray(upper, float)
    if (sigma <= 0 or not np.isfinite([mu, sigma]).all() or lower.shape!=upper.shape or
            not np.isfinite(lower).all() or not np.isfinite(upper).all() or
            np.any(lower < 0) or np.any(upper <= lower)):
        raise ValueError('Invalid distribution or interval')
    with np.errstate(divide='ignore'):
        zl = (np.log(lower)-mu)/sigma
    zu = (np.log(upper)-mu)/sigma
    upper_tail = zl > 0
    a = np.where(upper_tail, special.log_ndtr(-zl), special.log_ndtr(zu))
    b = np.where(upper_tail, special.log_ndtr(-zu), special.log_ndtr(zl))
    with np.errstate(divide='ignore', invalid='ignore'):
        result = a+np.log(-np.expm1(np.minimum(0., b-a)))
    return result


def fit(lower, upper):
    center = float(np.log(np.median((np.asarray(lower)+upper)/2)))
    objective = lambda p: float(-interval_logprob(p, lower, upper).mean())
    runs = [optimize.minimize(objective, [np.clip(center+offset, *BOUNDS[0]), sigma],
                              method='L-BFGS-B', bounds=BOUNDS, options={'maxiter': 1000, 'ftol': 1e-12})
            for offset in (-.5, 0., .5) for sigma in (.6, 1., 1.6)]
    good = [r for r in runs if r.success and np.isfinite(r.fun)]
    if not good:
        raise ValueError('Interval fit did not converge')
    result = min(good, key=lambda r: r.fun)
    boundary = any(abs(result.x[i]-edge)<1e-5 for i, bounds in enumerate(BOUNDS) for edge in bounds)
    return result.x, {'converged': True, 'at_boundary': boundary, 'converged_starts': len(good),
                       'nll': float(result.fun), 'params_mu_sigma': result.x.tolist()}


def intervals(rows, scale=1.):
    d = np.array([r['distance_m'] for r in rows])
    u = np.array([r['uncertainty_sum_m_assumed'] for r in rows])*scale
    return np.maximum(0., d-u), d+u


def summarize(predictions):
    return {'cases': len(predictions),
            'interval_nll': float(np.mean([r['nll'] for r in predictions])),
            'fixed_65case_interval_nll': float(np.mean([r['baseline_nll'] for r in predictions])),
            'mean_loss_change_new_minus_baseline': float(np.mean([r['nll']-r['baseline_nll'] for r in predictions])),
            'coverage_lower': np.mean([r['definitely_inside'] for r in predictions], axis=0).tolist(),
            'coverage_upper': np.mean([r['possibly_inside'] for r in predictions], axis=0).tolist(),
            'mean_radius_m': np.mean([r['radius_m'] for r in predictions], axis=0).tolist()}


def evaluate(rows, baseline, spatial=True, scale=1.):
    from sklearn.model_selection import GroupKFold
    lower, upper = intervals(rows, scale)
    groups = np.array([r['spatial_block'] if spatial else r['case_key'] for r in rows])
    folds = GroupKFold(n_splits=5 if spatial else len(rows)).split(lower, groups=groups)
    predictions, audit = [], []
    for train, test in folds:
        assert not set(groups[train]) & set(groups[test])
        p, diagnostics = fit(lower[train], upper[train])
        radii = stats.lognorm.ppf([.5, .8, .95], p[1], scale=np.exp(p[0]))
        loss = -interval_logprob(p, lower[test], upper[test])
        base_loss = -interval_logprob(baseline, lower[test], upper[test])
        audit.append({'train_cases': [rows[i]['case_key'] for i in train],
                      'test_cases': [rows[i]['case_key'] for i in test],
                      'train_groups': sorted(set(groups[train])), 'test_groups': sorted(set(groups[test])),
                      'fit': diagnostics})
        for j, i in enumerate(test):
            predictions.append({'case_key': rows[i]['case_key'], 'spatial_block': rows[i]['spatial_block'],
                                'lower_m': float(lower[i]), 'upper_m': float(upper[i]),
                                'nll': float(loss[j]), 'baseline_nll': float(base_loss[j]),
                                'radius_m': radii.tolist(), 'definitely_inside': (upper[i]<=radii).tolist(),
                                'possibly_inside': (lower[i]<=radii).tolist()})
    return {'summary': summarize(predictions), 'predictions': predictions, 'folds': audit}


def bootstrap_blocks(predictions):
    groups = sorted({r['spatial_block'] for r in predictions})
    values = [np.array([r['nll']-r['baseline_nll'] for r in predictions if r['spatial_block']==g]) for g in groups]
    sums, counts = np.array([v.sum() for v in values]), np.array([len(v) for v in values])
    choices = np.random.default_rng(SEED).integers(0, len(groups), (2000, len(groups)))
    means = sums[choices].sum(axis=1)/counts[choices].sum(axis=1)
    return {'blocks': len(groups), 'resamples': 2000, 'percentile_95': np.quantile(means, [.025, .975]).tolist(),
            'interpretation': 'EXPLORATORY_SPATIAL_BLOCK_BOOTSTRAP_NOT_EXTERNAL_PERFORMANCE'}


def verify_sources():
    adoption = json.loads((OUT/'hiker_adoption.json').read_text())
    for path, expected in [(OUT/'endpoint_links.minimized.json', adoption['source_sha256']),
                           (OUT/'hiker_intervals.json', adoption['prepared_sha256']),
                           (OUT/'hiker_object_ids.json', adoption['source_selection']['sha256']),
                           (PROTOCOL, adoption['protocol_sha256']),
                           (ROOT/'pipeline/prepare_yosar.py', adoption['code_sha256'])]:
        if sha(path.read_bytes()) != expected:
            raise ValueError('Frozen input changed: '+str(path))
    return adoption, json.loads((OUT/'hiker_intervals.json').read_text())['rows']


def main():
    adoption, rows = verify_sources()
    base = ml_models.load_bundle()
    old = base['endpoint']['models']['lognorm']['params']
    baseline = (float(np.log(old[2])), float(old[0]))
    loo = evaluate(rows, baseline, spatial=False)
    print('Leave-one-case-out completed', flush=True)
    spatial = evaluate(rows, baseline)
    effect = bootstrap_blocks(spatial['predictions'])
    sensitivity = {}
    for scale in (.5, 1., 2.):
        p, diagnostics = fit(*intervals(rows, scale))
        checked = spatial if scale==1. else evaluate(rows, baseline, scale=scale)
        sensitivity[str(scale)] = {'fit': diagnostics, 'spatial_evaluation': checked,
                                  'radius_m': stats.lognorm.ppf([.5, .8, .95], p[1], scale=np.exp(p[0])).tolist()}
    p, diagnostics = fit(*intervals(rows))
    if diagnostics['at_boundary'] or any(f['fit']['at_boundary'] for e in (loo, spatial) for f in e['folds']):
        raise ValueError('Boundary solution; do not publish as an operational reference')
    observed = np.array([r['distance_m'] for r in rows])
    widths = np.array([r['upper_m']-r['lower_m'] for r in rows])
    summary = {'cases': len(rows), 'zero_distance_retained': adoption['report']['zero_distance_retained'],
               'observed_distance_m': {'mean':float(observed.mean()), 'median':float(np.median(observed)),
                                       'min':float(observed.min()), 'max':float(observed.max()),
                                       'q25_q75':np.quantile(observed,[.25,.75]).tolist()},
               'interval_width_m': {'mean':float(widths.mean()), 'median':float(np.median(widths)),
                                    'max':float(widths.max())},
               'scope': 'YOSAR_SINGLE_HIKER_ENDPOINT_DISTANCE_NOT_TIME_POSITION',
               'paper_service_count_match': False, 'license': adoption['license'], 'quantiles': [.5, .8, .95],
               'uncertainty_units': adoption['report']['uncertainty_units'],
               'leave_one_case_out': loo['summary'], 'spatial_group_5fold': spatial['summary'],
               'loss_difference_bootstrap': effect,
               'uncertainty_scale_radius_m': {k: v['radius_m'] for k, v in sensitivity.items()},
               'default_model_changed': False}
    endpoint = copy.deepcopy(base['endpoint'])
    endpoint['models'][MODEL] = {'params': [float(p[1]), 0., float(np.exp(p[0]))],
                                 'label': 'YOSAR 하이커 · 좌표 오차 구간',
                                 'cases': len(rows), 'license': adoption['license'],
                                 'attribution': adoption['attribution'], 'license_url': adoption['license_url'],
                                 'uncertainty_units_inferred': True, 'fit': diagnostics}
    evaluation = copy.deepcopy(base['evaluation'])
    evaluation['yosar_endpoint'] = {'summary': summary, 'leave_one_case_out': loo, 'spatial_group_5fold': spatial,
                                   'uncertainty_sensitivity': sensitivity}
    inventory = copy.deepcopy(base['data_inventory'])
    inventory['yosar_endpoint'] = adoption
    content = {'endpoint.json': endpoint, 'searcher_speed.json': base['searcher_speed'],
               'evaluation.json': evaluation, 'data_inventory.json': inventory,
               'exercise_validation.json': base['exercise_validation']}
    manifest = copy.deepcopy(base['manifest'])
    manifest.update({'parent_bundle': base['bundle_id'], 'trained_at': datetime.now(timezone.utc).isoformat(),
                     'changes': 'Added separate YOSAR uncertain endpoint model; original default and speed unchanged.',
                     'yosar_license': {k: adoption[k] for k in ('license', 'license_url', 'attribution', 'changes')},
                     'code': {**manifest['code'], **{name: sha((ROOT/name).read_bytes()) for name in
                              ('pipeline/train_yosar.py', 'pipeline/prepare_yosar.py', 'docs/AI-YOSAR-사전검증계획.md')}}})
    limits = ['YOSAR는 별도 NC-SA 비상업적 참고 모델. 기존 자료와 합치지 않았고 국내 성능은 미검증.',
              'YOSAR 좌표 오차 미터 단위는 논문 기반 추론. 서비스와 논문 표본 수가 달라 재현 검증이 아님.']
    manifest['limitations'] = list(dict.fromkeys(manifest['limitations']+limits))
    folder = Path(tempfile.mkdtemp(prefix='.yosar-training-', dir=ml_models.MODEL_ROOT))
    hashes = {}
    for name, value in content.items():
        raw = canonical(value); (folder/name).write_bytes(raw); hashes[name] = sha(raw)
    manifest['files'] = hashes
    raw = canonical(manifest); bundle_id = sha(raw); (folder/'manifest.json').write_bytes(raw)
    os.replace(folder, ml_models.MODEL_ROOT/bundle_id)
    # Load and validate all files before atomically selecting the new bundle.
    ml_models.load_bundle(bundle_id)
    fd, temporary = tempfile.mkstemp(prefix='.current-', dir=ml_models.MODEL_ROOT)
    with os.fdopen(fd, 'wb') as f:
        f.write(canonical({'bundle_id': bundle_id}))
    os.replace(temporary, ml_models.MODEL_ROOT/'current.json')
    print(json.dumps({'bundle_id': bundle_id, 'summary': summary}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
