import json
import shutil
import numpy as np
import pytest
from backend import walking_reference as w
from backend.tests.test_searchproof import client,current
from pipeline.train_geolife import fit,score

# GeoLife(MSR-LA) 파생물은 재배포 금지라 깃허브 저장소에 없다(LICENSES.md §2·§6).
# 로컬 학습 산출물이 있는 컴퓨터에서만 아래 재현 검사를 실행하고, 없으면 건너뛴다.
from pipeline.prepare_geolife import OUT as GEOLIFE_OUT
needs_model=pytest.mark.skipif(not (w.MODELS/'current.json').exists(),reason='로컬 전용 GeoLife 모델 없음(저장소 미포함)')
needs_local=pytest.mark.skipif(not ((w.MODELS/'current.json').exists() and (GEOLIFE_OUT/'walking_samples.npz').exists()),reason='로컬 전용 GeoLife 모델·정제 자료 없음(저장소 미포함)')

@needs_local
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

@needs_model
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

@needs_model
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

@needs_model
def test_tampered_artifact_refused(tmp_path,monkeypatch):
    root=tmp_path/'model';shutil.copytree(w.MODELS,root)
    monkeypatch.setattr(w,'MODELS',root)
    mid=json.loads((root/'current.json').read_text())['model_id']
    with (root/mid/'model.json').open('ab') as f:f.write(b' ')
    with pytest.raises(ValueError,match='hash mismatch'):w.load()

def test_missing_local_model_is_503_and_leaves_missions_untouched(client,tmp_path,monkeypatch):
    # 새로 클론한 컴퓨터에는 이 모델이 없다. 500이 아니라 503으로 설명하고 임무는 건드리지 않아야 한다.
    monkeypatch.setattr(w,'MODELS',tmp_path/'absent')
    mid,before=current(client)
    # 배포 프로필 차단(503)과 구분하기 위해 사유 문구까지 확인한다.
    s=client.get('/api/ai/walking/status');assert s.status_code==503 and 'unavailable' in s.json()['detail']
    r=client.get('/api/ai/walking/simulate?model_id='+'0'*64);assert r.status_code==503 and 'unavailable' in r.json()['detail']
    assert client.get('/api/missions/'+mid).json()['receipt']['hash']==before['receipt']['hash']
