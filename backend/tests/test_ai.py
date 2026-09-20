import json
import shutil
import uuid
import io
import zipfile
import numpy as np
import pytest
from backend import core,ml_models,planner,main
from backend.tests.test_searchproof import client,current


def request_for(state,**overrides):
    return {'base_version':state['version'],'model_id':ml_models.current_id(),'minutes':30,
            'teams':[{'name':'1팀','lon':state['params']['lon'],'lat':state['params']['lat'],'search_type':'sweep'}],
            'idempotency_key':uuid.uuid4().hex,**overrides}


def test_actual_training_provenance_and_exports():
    b=ml_models.load_bundle();e=b['evaluation']
    assert e['endpoint']['summary']['cases']==65
    assert e['searcher_speed']['summary']['tracks']==61
    assert e['searcher_speed']['summary']['json_export_max_error']<1e-10
    assert e['searcher_speed']['summary']['used_segments']+sum(e['searcher_speed']['summary']['excluded'].values())==45141
    for scheme in e['endpoint']['details']['lognorm'].values():
        for row in scheme['cases']:assert row['test_incident'] not in row['train_incidents']
    for fold in e['searcher_speed']['folds']:assert fold['test_team'] not in fold['train_teams']
    assert b['exercise_validation']['summary']['exercises']==1
    roles={x['role'] for x in b['data_inventory']['originals']}
    assert 'EXCLUDED_FITTED_ON_TARGET_LEAKAGE' in roles
    speeds=ml_models.speed(b,[0,.1,.3,.5],'sweep',engine='random_forest')
    assert np.ptp(speeds)>0 and np.isfinite(speeds).all()
    with pytest.raises(ValueError):ml_models.speed(b,[float('nan')])
    with pytest.raises(ValueError):ml_models.speed(b,[2])


def test_corrupt_model_fails_closed(tmp_path,monkeypatch):
    bid=ml_models.current_id();shutil.copytree(ml_models.MODEL_ROOT/bid,tmp_path/bid)
    (tmp_path/'current.json').write_text(json.dumps({'bundle_id':bid}))
    monkeypatch.setattr(ml_models,'MODEL_ROOT',tmp_path)
    assert ml_models.public_status()['available']
    with (tmp_path/bid/'endpoint.json').open('ab') as f:f.write(b' ')
    assert not ml_models.public_status()['available']
    with pytest.raises(ValueError):ml_models.load_bundle('../other')


def test_plan_respects_paths_and_time_and_not_observation(client):
    mid,s=current(client);before=s['receipt']['hash']
    payload=request_for(s)
    r=client.post(f'/api/missions/{mid}/plans',json=payload)
    assert r.status_code==200,r.text
    p=r.json();assert p['assignments']
    assert p['status']=='PROPOSED' and p['model_id']==payload['model_id']
    t=core.terrain()
    for a in p['assignments']:
        assert a['total_minutes']<=payload['minutes']+1e-8
        assert a['travel_minutes']+a['search_minutes']+a['return_minutes']==pytest.approx(a['total_minutes'])
        cells=np.array(a['search_cells']);assert not t.hazard.ravel()[cells].any()
        assert len(cells)<256 # Partial route never discounts the whole sector.
    for feature in p['routes']['features']:
        coords=np.array(feature['geometry']['coordinates']);rr,cc=t.rc(coords[:,0],coords[:,1])
        assert not t.hazard[rr.astype(int),cc.astype(int)].any()
        # Every path edge is a safe four-neighbor step; no straight-line shortcuts.
        dr,dc=np.diff(rr),np.diff(cc)
        assert np.allclose(np.abs(dr)+np.abs(dc),1)
    assert client.get(f'/api/missions/{mid}').json()['receipt']['hash']==before
    assert client.post(f'/api/missions/{mid}/plans',json=payload).json()['id']==p['id']
    assert client.post(f'/api/missions/{mid}/plans',json={**payload,'minutes':45}).status_code==409
    approve=f'/api/missions/{mid}/plans/{p["id"]}/approve'
    assert client.post(approve,json={'expected_version':0}).status_code==200
    assert client.post(approve,json={'expected_version':0}).status_code==200
    after=client.get(f'/api/missions/{mid}').json()
    assert after['receipt']['hash']==before and after['coverage_km2']==s['coverage_km2']
    assert len([x for x in after['ledger'] if x['kind']=='AI_PLAN'])==1
    package=client.get(f'/api/missions/{mid}/package.zip')
    assert package.status_code==200
    with zipfile.ZipFile(io.BytesIO(package.content)) as z:
        assert json.loads(z.read('ai/plans.json'))[0]['status']=='APPROVED'
        assert f'ai/{payload["model_id"]}/searcher_speed.json' in z.namelist()
        assert main.sha(z.read(f'ai/maps/{p["id"]}.png'))==p['display_map_sha256']
    assert client.post('/api/verify',files={'file':('ai.zip',package.content)}).json()['valid']


def test_new_observation_invalidates_plan_and_recomputes(client):
    mid,s=current(client)
    old=client.post(f'/api/missions/{mid}/plans',json=request_for(s)).json()
    track=client.post(f'/api/missions/{mid}/demo-track').json()
    client.post(f'/api/missions/{mid}/tracks/{track["id"]}/apply',json={'outcome':'COMPLETED_NO_FIND','expected_version':0})
    assert client.post(f'/api/missions/{mid}/plans/{old["id"]}/approve',json={'expected_version':0}).status_code==409
    assert client.post(f'/api/missions/{mid}/plans',json=request_for(s)).status_code==409
    new=client.get(f'/api/missions/{mid}').json()
    p=client.post(f'/api/missions/{mid}/plans',json=request_for(new)).json()
    assert p['base_hash']!=old['base_hash'] and p['base_version']==1
    assert p['assignments']!=old['assignments']


def test_plan_expiration_risk_and_endpoint_mode(client):
    mid,s=current(client)
    p=client.post(f'/api/missions/{mid}/plans',json=request_for(s)).json()
    with main.connect() as c:c.execute("UPDATE ai_plans SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(p['id'],))
    assert client.post(f'/api/missions/{mid}/plans/{p["id"]}/approve',json={'expected_version':0}).status_code==409
    t=core.terrain();r,c=np.argwhere(t.hazard)[len(np.argwhere(t.hazard))//2];lon,lat=t.lonlat(r+.5,c+.5)
    r=client.post(f'/api/missions/{mid}/plans',json=request_for(s,teams=[dict(name='위험팀',lon=lon,lat=lat,search_type='sweep')]))
    assert r.status_code==422
    data=request_for(s,distribution_mode='ENDPOINT_REFERENCE')
    p=client.post(f'/api/missions/{mid}/plans',json=data)
    assert p.status_code==200,p.text
    assert '현재 시점 존재확률이 아닙니다' in p.json()['warning']
    assert client.post(f'/api/missions/{mid}/plans',json={**data,'projection_seconds':60}).status_code==422
    d=planner.endpoint_reference(ml_models.current_id(),s['params']['lon'],s['params']['lat'],50,42)
    d.check();assert d.outside>0


def test_multiple_teams_are_incremental_and_time_changes(client):
    mid,s=current(client);one=request_for(s)
    two={**one,'idempotency_key':uuid.uuid4().hex,'teams':one['teams']+[{**one['teams'][0],'name':'2팀'}]}
    p=client.post(f'/api/missions/{mid}/plans',json=two).json()
    assert len(p['assignments'])==2
    a,b=p['assignments']
    if a['zone_id']==b['zone_id']:
        assert b['assumed_incremental_detection_range'][1]<=a['assumed_incremental_detection_range'][1]
    future=client.post(f'/api/missions/{mid}/plans',json=request_for(s,projection_seconds=60))
    assert future.status_code==200,future.text
    assert future.json()['estimated_at']!=p['estimated_at']
    assert client.get(f'/api/missions/{mid}').json()['version']==0


def test_saved_map_history_and_superseded_approval(client):
    mid,s=current(client)
    first=client.post(f'/api/missions/{mid}/plans',json=request_for(s,distribution_mode='ENDPOINT_REFERENCE')).json()
    image=client.get(first['display_map_url'])
    assert image.status_code==200 and main.sha(image.content)==first['display_map_sha256']
    display=first['display_summary']
    assert display['inside_mass']+display['outside_mixed']==pytest.approx(1)
    single=client.get(f'/api/missions/{mid}/plans/{first["id"]}').json()
    assert single['display_summary']==display and single['inputs']['distribution_mode']=='ENDPOINT_REFERENCE'
    assert client.post(f'/api/missions/{mid}/plans/{first["id"]}/approve',json={'expected_version':0}).status_code==200
    second=client.post(f'/api/missions/{mid}/plans',json=request_for(s)).json()
    assert client.post(f'/api/missions/{mid}/plans/{second["id"]}/approve',json={'expected_version':0}).status_code==200
    assert client.post(f'/api/missions/{mid}/plans/{first["id"]}/approve',json={'expected_version':0}).status_code==409
    history=client.get(f'/api/missions/{mid}/plans').json()
    assert history[0]['id']==second['id'] and history[0]['status']=='APPROVED'
    assert all('display_summary' not in h for h in history) # Compact history, full plan by id.
    assert client.get(first['display_map_url']).content==image.content
