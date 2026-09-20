"""Numerical approximation study, never training or a field-success benchmark."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gc
import hashlib
import json
import time

import numpy as np

from backend import core, daylight, live, ml_models, planner
from backend.trajectory import Scorer, BIN_SECONDS, sample_budgets
from pipeline.collect_detection_sources import canonical, immutable


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'docs/AI-추천안정성-검증계획.md'
SETTINGS = [(60, 8192), (30, 8192), (15, 8192), (60, 4096), (60, 2048),
            (BIN_SECONDS, 'adaptive'), (15, None), (5, None)]
CASES = [('DAY', .5, '2026-09-19T14:00:00+09:00', 600),
         ('DUSK', 2., '2026-09-19T18:00:00+09:00', 900)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(priors, params, clock, seconds, minutes, step, packets):
    end = seconds + minutes * 60
    times = np.arange(seconds, end + 1, step, dtype=float)
    budgets = (sample_budgets([2*np.count_nonzero(d.grid) for d in priors],len(times))
               if packets == 'adaptive' else [packets]*len(priors))
    return [live.project(d, end, params['seed'] + i * 101, behavior=(i == 2),
                         clock=clock, capture_times=times, max_packets=budget)[1]['trajectory']
            for i, (d,budget) in enumerate(zip(priors,budgets))]


def zonal(paths):
    return np.array([np.r_[core.zone_sum(p.distribution(0).grid),
                           p.distribution(0).outside] for p in paths])


def replay(plan, paths):
    scorer = Scorer(paths, planner.WIDTHS)
    gains = np.zeros((len(paths), len(planner.WIDTHS)))
    for a in plan['assignments']:
        added, extra = scorer.score(a['search_cells'], a['search_arrival_seconds'])
        gains += added
        scorer.accept(extra)
    if not np.isfinite(gains).all() or np.any(gains < 0) or np.any(gains > 1 + 1e-12):
        raise ValueError('Invalid cumulative assumed detection gain')
    return {'scenario_width_gains': gains.tolist(),
            'worst_scenario_middle_width': float(gains[:, 1].min())}


def main():
    bundle = ml_models.load_bundle()
    sources = ['backend/core.py', 'backend/live.py', 'backend/trajectory.py', 'backend/main.py',
               'backend/planner.py', 'backend/daylight.py', 'backend/ml_models.py',
               'pipeline/evaluate_plan_stability.py', 'docs/AI-추천안정성-검증계획.md',
               'data/hallim/meta.json']
    code_hashes = {p: digest(ROOT / p) for p in sources}
    result = {'schema': 'plan-numerics-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'model_bundle': bundle['bundle_id'], 'source_hashes': code_hashes,
              'source_text': {p: (ROOT/p).read_text() for p in sources},
              'synthetic_mission_inputs': True, 'field_accuracy_evaluation': False,
              'trained_models_changed': False, 'cases': []}
    for name, hours, base_at, start in CASES:
        params = {'lon': 126.268, 'lat': 33.397, 'sigma': 50, 'seed': 42,
                  'hours': hours, 'analysis_at': base_at, 'daylight_enabled': True,
                  'night_factor': .6}
        params['missing_at'] = (datetime.fromisoformat(base_at) - timedelta(hours=hours)).isoformat()
        minutes = 30
        teams = [dict(name=f'Team {i}', lon=params['lon'], lat=params['lat'], search_type='sweep')
                 for i in (1, 2)]
        priors = core.compute(params)
        clock = daylight.clock_for(params, base_at, start + minutes * 60)
        estimated = (datetime.fromisoformat(base_at) + timedelta(seconds=start)).isoformat()
        team_clock = daylight.clock_for(params, estimated, minutes * 60)
        night = min(float(team_clock.factor(s)) for s in range(0, minutes * 60 + 1, 60))
        runs = []
        for step, packets in SETTINGS:
            began = time.perf_counter()
            paths = capture(priors, params, clock, start, minutes, step, packets)
            scorer = Scorer(paths, planner.WIDTHS)
            plan = planner.plan([p.distribution(0) for p in paths], bundle, teams, minutes,
                                night, dynamic=scorer)
            row = {'step_seconds': step, 'packets': packets,
                   'seconds': time.perf_counter() - began,
                   'unique_paths': [len(p.weights) for p in paths],
                   'path_sha256': [p.signature() for p in paths],
                   'start_zone_mass': zonal(paths).tolist(),
                   'plan': plan}
            runs.append(row)
            print(json.dumps({'case': name, 'step': step, 'packets': packets,
                              'zones': [a['zone_id'] for a in plan['assignments']],
                              'seconds': round(row['seconds'], 3)}), flush=True)
            # Keep only the final, finest reference arrays for cross-evaluation.
            if (step, packets) != SETTINGS[-1]:
                del scorer, paths
                gc.collect()
        reference = runs[-1]
        reference_mass = np.array(reference['start_zone_mass'])
        reference_replay = replay(reference['plan'], paths)
        ref_zones = [a['zone_id'] for a in reference['plan']['assignments']]
        for row in runs:
            row['reference_replay'] = replay(row['plan'], paths)
            row['reference_replay']['difference_from_reference_plan'] = (
                row['reference_replay']['worst_scenario_middle_width']
                - reference_replay['worst_scenario_middle_width'])
            row['start_zone_total_variation'] = (
                .5 * np.abs(np.array(row['start_zone_mass']) - reference_mass).sum(axis=1)).tolist()
            row['same_zone_assignments_as_reference'] = (
                [a['zone_id'] for a in row['plan']['assignments']] == ref_zones)
        result['cases'].append({'name': name, 'parameters': params, 'start_seconds': start,
                                'minutes': minutes, 'teams': teams, 'runs': runs})
        del scorer, paths
        gc.collect()
    # Refuse a result mixing code revisions during a run.
    if code_hashes != {p: digest(ROOT / p) for p in sources}:
        raise ValueError('Source changed during stability evaluation')
    raw = canonical(result)
    path = ROOT / 'artifacts/plan-stability' / (hashlib.sha256(raw).hexdigest() + '.json')
    immutable(path, raw)
    print(json.dumps({'result': str(path), 'field_accuracy_evaluation': False}))


if __name__ == '__main__':
    main()
