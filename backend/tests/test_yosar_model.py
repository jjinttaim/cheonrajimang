import hashlib
import json
import io
import zipfile
import numpy as np
import pytest
from scipy import stats
from backend import core, ml_models, planner, main
from backend.tests.test_searchproof import client, current
from backend.tests.test_ai import request_for
from pipeline.prepare_yosar import OUT, prepare
from pipeline.train_yosar import interval_logprob, fit, intervals, verify_sources


def test_prepared_sources_and_zero_distance_retention():
    adoption, rows = verify_sources()
    ids = json.loads((OUT/'hiker_object_ids.json').read_text())['objectIds']
    data = json.loads((OUT/'endpoint_links.minimized.json').read_text())
    rebuilt, report = prepare(data, ids)
    assert rows==rebuilt and report==adoption['report']
    assert len(rows)==132 and len({r['case_key'] for r in rows})==132
    zeros = [r for r in rows if r['distance_m']==0]
    assert len(zeros)==18 and all(r['lower_m']==0 and r['upper_m']>0 for r in zeros)
    assert len(report['excluded_rows'])==2
    assert report['paper_service_count_match'] is False
    assert all(set(r)=={'source_oid','case_key','distance_m','uncertainty_sum_m_assumed',
                        'lower_m','upper_m','spatial_block'} for r in rows)
    assert set(adoption['source_selection'])=={'path','url','bytes','sha256'}
    assert 'SubjectC%3D%27Hiker%27' in adoption['source_selection']['url']


def test_interval_math_includes_zero_and_stable_tails():
    mu, sigma = np.log(1200), 1.2
    lower, upper = np.array([0.,10.,100.,1000.,3000.]), np.array([20.,100.,700.,2500.,10000.])
    expected = stats.lognorm.cdf(upper,sigma,scale=np.exp(mu))-stats.lognorm.cdf(lower,sigma,scale=np.exp(mu))
    assert np.allclose(np.exp(interval_logprob((mu,sigma),lower,upper)),expected)
    tail = interval_logprob((0.,1.),[np.exp(12)],[np.exp(12.1)])
    assert np.isfinite(tail).all() and tail[0]<-70
    for lo, hi in [([0.],[np.inf]),([1.],[1.]),([-1.],[3.]),([0.],[np.nan])]:
        with pytest.raises(ValueError):interval_logprob((mu,sigma),lo,hi)


def test_interval_fit_can_recover_synthetic_parameters_without_using_them_in_training():
    # Quantile-generated unit test only. These values never enter trained artifacts.
    distances=stats.lognorm.ppf((np.arange(160)+.5)/160,.7,scale=1500.)
    p, diagnostic=fit(np.maximum(0,distances-2),distances+2)
    assert diagnostic['converged'] and not diagnostic['at_boundary']
    assert p[0]==pytest.approx(np.log(1500),abs=.01)
    assert p[1]==pytest.approx(.7,abs=.03)


def test_model_provenance_folds_default_and_parent_preserved():
    b=ml_models.load_bundle();parent=ml_models.load_bundle(b['manifest']['parent_bundle'])
    assert b['endpoint']['models']['lognorm']==parent['endpoint']['models']['lognorm']
    assert b['endpoint']['reference_model']=='lognorm'
    assert b['searcher_speed']==parent['searcher_speed']
    e=b['evaluation']['yosar_endpoint'];assert e['summary']['cases']==132
    assert e['summary']['zero_distance_retained']==18
    assert e['summary']['license']=='CC-BY-NC-SA-4.0'
    for scheme in ('leave_one_case_out','spatial_group_5fold'):
        seen=[]
        for fold in e[scheme]['folds']:
            assert not set(fold['test_cases'])&set(fold['train_cases'])
            assert not set(fold['test_groups'])&set(fold['train_groups'])
            assert fold['fit']['converged'] and not fold['fit']['at_boundary']
            seen+=fold['test_cases']
        assert len(seen)==len(set(seen))==132
        s=e[scheme]['summary']
        assert np.all(np.asarray(s['coverage_lower'])<=s['coverage_upper'])
        assert len(e[scheme]['predictions'])==132
    assert set(e['uncertainty_sensitivity'])=={'0.5','1.0','2.0'}
    assert e['summary']['loss_difference_bootstrap']['blocks']==27
    for name in ('endpoint.json','evaluation.json','data_inventory.json'):
        content=(ml_models.MODEL_ROOT/b['bundle_id']/name).read_bytes()
        assert hashlib.sha256(content).hexdigest()==b['manifest']['files'][name]


def test_separate_reference_mass_and_old_bundle_compatibility():
    b=ml_models.load_bundle();args=(126.263,33.409,50,42)
    default=planner.endpoint_reference(b['bundle_id'],*args)
    extra=planner.endpoint_reference(b['bundle_id'],*args,'yosar_interval_lognorm')
    default.check();extra.check()
    assert extra.outside>0 and not np.array_equal(extra.grid,default.grid)
    original='4c9c75cef64be5b4de0cfc02a91863729aa0f0ab7497b2fbaa4ebf2834f491b2'
    assert len(ml_models.endpoint_options(ml_models.load_bundle(original)))==1
    assert np.array_equal(default.grid,planner.endpoint_reference(original,*args).grid)
    with pytest.raises(ValueError):planner.endpoint_reference(original,*args,'yosar_interval_lognorm')


def test_plan_selection_frozen_map_license_and_no_time_mixing(client):
    mid,s=current(client);before=s['receipt']['hash']
    data=request_for(s,distribution_mode='ENDPOINT_REFERENCE',endpoint_model='yosar_interval_lognorm')
    response=client.post(f'/api/missions/{mid}/plans',json=data)
    assert response.status_code==200,response.text
    p=response.json();assert p['endpoint_model']=='yosar_interval_lognorm'
    assert p['endpoint_model_info']['license']=='CC-BY-NC-SA-4.0'
    assert p['endpoint_model_info']['uncertainty_units_inferred'] is True
    assert '논문 기반 추론' in p['warning']
    assert main.sha(client.get(p['display_map_url']).content)==p['display_map_sha256']
    assert client.post(f'/api/missions/{mid}/plans',json=data).json()['id']==p['id']
    assert client.post(f'/api/missions/{mid}/plans',json={**data,'endpoint_model':'lognorm'}).status_code==409
    assert client.post(f'/api/missions/{mid}/plans',json={**data,'distribution_mode':'TIME_SCENARIOS'}).status_code==422
    assert client.post(f'/api/missions/{mid}/plans',json={**data,'temporal_projection':True}).status_code==422
    single=client.get(f'/api/missions/{mid}/plans/{p["id"]}').json()
    assert single['inputs']['endpoint_model']=='yosar_interval_lognorm'
    history=client.get(f'/api/missions/{mid}/plans').json()
    assert history[0]['endpoint_model_info']==p['endpoint_model_info']
    assert client.get(f'/api/missions/{mid}').json()['receipt']['hash']==before
    package=client.get(f'/api/missions/{mid}/package.zip')
    assert package.status_code==200
    with zipfile.ZipFile(io.BytesIO(package.content)) as z:
        manifest=json.loads(z.read(f'ai/{data["model_id"]}/manifest.json'))
        assert manifest['yosar_license']['license']=='CC-BY-NC-SA-4.0'
        exported=json.loads(z.read(f'ai/{data["model_id"]}/endpoint.json'))
        assert exported['models']['yosar_interval_lognorm']['cases']==132
        assert json.loads(z.read('ai/plans.json'))[0]['endpoint_model']=='yosar_interval_lognorm'
    assert client.post('/api/verify',files={'file':('yosar.zip',package.content)}).json()['valid']


def test_legacy_idempotent_request_remains_readable(client):
    mid,s=current(client);data=request_for(s)
    original=client.post(f'/api/missions/{mid}/plans',json=data).json()
    with main.connect() as c:
        row=c.execute('SELECT request FROM ai_plans WHERE id=?',(original['id'],)).fetchone()
        legacy=json.loads(row['request']);legacy.pop('endpoint_model')
        encoded=main.canonical(legacy)
        c.execute('UPDATE ai_plans SET request=?,input_hash=? WHERE id=?',(encoded,main.sha(encoded.encode()),original['id']))
    retried=client.post(f'/api/missions/{mid}/plans',json=data)
    assert retried.status_code==200 and retried.json()['id']==original['id']
    changed=client.post(f'/api/missions/{mid}/plans',json={**data,'minutes':45})
    assert changed.status_code==409
