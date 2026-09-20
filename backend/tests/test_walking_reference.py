import json
import shutil
import numpy as np
import pytest
from backend import walking_reference as w
from backend.tests.test_searchproof import client,current
from pipeline.train_geolife import fit,score

def test_fitted_model_reproduces_and_splits_are_people_disjoint():
    mid,model,evaluation,inventory=w.load()
    from pipeline.prepare_geolife import OUT
    with np.load(OUT/'walking_samples.npz',allow_pickle=False) as d:rows,person=d['rows'],d['person']
    membership=np.array([inventory['split_by_person'][str(int(p))] for p in person])
    refit=fit(rows[membership=='train'],person[membership=='train'])
    assert refit['transition_prob']==model['transition_prob']
    assert refit['speed_representatives_mps']==model['speed_representatives_mps']
    groups=[set(person[membership==s]) for s in ['train','validation','test']]
    assert all(not a.intersection(b) for i,a in enumerate(groups) for b in groups[i+1:])
    assert evaluation['status']=='NOT_ADOPTED'
    actual=score(model,rows[membership=='test'],person[membership=='test'])
    assert actual==evaluation['splits']['test']

def test_reproducible_closed_loop_and_prefixes():
    _,model,_,_=w.load()
    a=w.simulate(model,60,42);b=w.simulate(model,300,42)
    assert a==w.simulate(model,60,42)
    assert a['mass']==1 and sum(c[2] for c in a['cells'])==pytest.approx(1)
    assert np.array_equal(a['paths'],np.array(b['paths'])[:,:3])
    assert w.simulate(model,0,42)['median_displacement_m']==0
    assert w.simulate(model,60,43)['cells']!=a['cells']
    assert np.max(np.linalg.norm(np.array(b['paths'])[:,-1,:],axis=1))<=1200.01
    with pytest.raises(ValueError):w.simulate(model,301,42)

def test_api_is_experimental_and_does_not_mutate_missions(client):
    mid,before=current(client)
    s=client.get('/api/ai/walking/status');assert s.status_code==200
    status=s.json();assert not status['mission_integration'] and not status['missing_person_validated']
    endpoint='/api/ai/walking/simulate?model_id='+status['model_id']
    r=client.get(endpoint+'&seconds=120');assert r.status_code==200
    assert r.json()['status']=='NOT_ADOPTED' and r.json()['simulation_only']
    assert not r.json()['terrain_applied'] and not r.json()['source_coordinates_exposed']
    assert 'transition_prob' not in r.text and 'transition_prob' not in s.text
    assert client.get(endpoint+'&seconds=301').status_code==422
    assert client.get('/api/ai/walking/simulate?model_id='+'0'*64).status_code==409
    assert client.get('/api/missions/'+mid).json()['receipt']['hash']==before['receipt']['hash']
    # Restricted artifacts must not be in static serving or mission export.
    assert client.get('/data/models/walking_reference/current.json').status_code==404
    import io,zipfile
    package=client.get(f'/api/missions/{mid}/package.zip')
    with zipfile.ZipFile(io.BytesIO(package.content)) as z:
        assert not any('geolife' in n.lower() or 'walking_reference' in n.lower() for n in z.namelist())

def test_tampered_artifact_refused(tmp_path,monkeypatch):
    root=tmp_path/'model';shutil.copytree(w.MODELS,root)
    monkeypatch.setattr(w,'MODELS',root)
    mid=json.loads((root/'current.json').read_text())['model_id']
    with (root/mid/'model.json').open('ab') as f:f.write(b' ')
    with pytest.raises(ValueError,match='hash mismatch'):w.load()
