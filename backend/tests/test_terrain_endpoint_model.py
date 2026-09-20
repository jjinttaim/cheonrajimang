"""Synthetic fixtures test algebra only; training scripts use observed cases."""
import numpy as np
import hashlib
import json
from scipy.optimize._numdiff import approx_derivative

from pipeline.terrain_endpoint_model import (
    AREA, CELLS, GEOD, base_mass, distribution, objective, radial_fit,
    split_indices, target_cell,
)
from pipeline.prepare_endpoint_terrain import tile_keys, tile_name, STEP
from pipeline.train_endpoint_terrain import OUT, adoption_gate, bootstrap, load_data


def test_zero_tilt_exact_baseline_and_outside_mass_preserved():
    radial = radial_fit([100., 500., 1000., 2000., 4000., 20000.])
    base, outside = base_mass(radial)
    x = np.random.default_rng(21).normal(size=(2, CELLS, 2))
    mass, r = distribution(x, radial, [0., 0.])
    assert np.allclose(mass, base, atol=1e-14) and r == outside
    tilted, r2 = distribution(x, radial, [-.2, .5])
    assert r2 == outside and np.allclose(tilted.sum(axis=1) + r2, 1.)
    assert not np.allclose(tilted, mass)
    assert np.isclose(AREA.sum(), np.pi * 20000**2)


def test_gradient_matches_difference_with_outside_target():
    rng = np.random.default_rng(9)
    x = rng.normal(size=(3, CELLS, 2))
    y = np.array([20, 200, CELLS])
    radial = radial_fit([100., 1000., 5000.])
    beta = np.array([.1, -.3])
    loss, grad = objective(beta, x, y, radial)
    numerical = approx_derivative(lambda b: objective(b, x, y, radial)[0], beta)
    assert np.isfinite(loss) and np.allclose(grad, numerical.ravel(), atol=1e-7)


def test_geodesic_target_bins_and_outside():
    lon, lat, _ = GEOD.fwd(-100., 40., 12., 1500.)
    index, distance = target_cell(40., -100., lat, lon)
    assert abs(distance - 1500) < 1e-5
    # Use a non-boundary radius to avoid floating-point ties.
    lon, lat, _ = GEOD.fwd(-100., 40., 12., 1600.)
    assert target_cell(40., -100., lat, lon)[0] == 6 * 72 + 2
    lon, lat, _ = GEOD.fwd(-100., 40., 12., 21000.)
    assert target_cell(40., -100., lat, lon)[0] == CELLS


def test_spatial_buffer_excludes_nearby_training_cases():
    train, test = split_indices([40., 40.1, 41.], [-100., -100., -100.], ['a', 'b', 'c'], 'a')
    assert train.tolist() == [2] and test.tolist() == [0]


def test_cog_pixel_center_tile_edges_are_not_naive_floor():
    keys = tile_keys(np.array([-119., -119.-STEP]), np.array([36., 36.+STEP]))
    assert keys.tolist() == [[35, -119], [36, -120]]
    assert tile_name(36, -119) == 'Copernicus_DSM_COG_30_N36_00_W119_00_DEM'


def test_failed_evaluation_cannot_be_adopted_by_mean_improvement_alone():
    primary = {'summary': {'cases': 65, 'delta_nll_mean': -.1,
                           'candidate_area_hits': [.1, .2, .3], 'baseline_area_hits': [.1, .2, .3]},
               'failures': []}
    assert not adoption_gate(primary, {'interval': [-.2, .01]}, 65)['passed']
    assert adoption_gate(primary, {'interval': [-.2, -.01]}, 65)['passed']
    primary['failures'] = ['missing case']
    assert not adoption_gate(primary, {'interval': [-.2, -.01]}, 65)['passed']


def test_actual_research_artifact_replays_all_held_out_cases():
    from pipeline.terrain_endpoint_model import evaluate_one
    pointer = json.loads((OUT/'latest_run.json').read_text())
    raw = (OUT/'runs'/(pointer['run_id']+'.json')).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pointer['run_id']
    run = json.loads(raw)
    data_id, metadata, features = load_data()
    assert data_id == run['data_id']
    lookup = {c['incident_index']: i for i, c in enumerate(metadata['cases'])}
    for scheme in ('primary_spatial40km', 'secondary_case_out'):
        result = run[scheme]
        predictions = {p['incident_index']: p for p in result['predictions']}
        seen = []
        for fold in result['folds']:
            assert set(fold['train_ids']).isdisjoint(fold['test_ids'])
            assert len(fold['train_ids']) >= 20
            for case_id in fold['test_ids']:
                i = lookup[case_id]
                case = metadata['cases'][i]
                seen.append(case_id)
                for kind, model in [('candidate', fold['model']), ('baseline', {'radial': fold['model']['radial']})]:
                    prediction = evaluate_one(features[i], case['target_cell'], model)
                    assert np.isclose(prediction['nll'], predictions[case_id][kind]['nll'], rtol=0, atol=1e-12)
                    assert prediction['area_hits'] == predictions[case_id][kind]['area_hits']
                if scheme == 'primary_spatial40km':
                    for other_id in fold['train_ids']:
                        other = metadata['cases'][lookup[other_id]]
                        distance = GEOD.inv(case['ipp_lon'], case['ipp_lat'], other['ipp_lon'], other['ipp_lat'])[2]
                        assert distance > 40000
        assert len(seen) == len(set(seen)) == 65
        assert result['failures'] == []
    assert bootstrap(run['primary_spatial40km']['predictions']) == run['bootstrap_delta']
    assert adoption_gate(run['primary_spatial40km'], run['bootstrap_delta'], 65) == run['adoption']
    assert run['adoption']['production_model_changed'] is False
