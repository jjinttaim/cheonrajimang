import hashlib
import json
import numpy as np
import pytest
from backend.walking_reference import load,simulate,rollout
from pipeline.evaluate_geolife_horizons import windows,energy_score,ballistic
from backend import walking_evaluation as evaluation_api
from backend.tests.test_searchproof import client,current

def test_kernel_refactor_preserves_shipped_demo_exactly():
    expected={0:'a7ebcedd4eae2c15e7a3e72229f8af8071c8412223a4339646f15ba6f59bc693',
              30:'d2ddca22815da38ca6219a34dce2cac80654e9ecf4c53f17843bab9536059c2d',
              120:'df6623641f9fc4a8473bf894e48cdd3fd871879d83104260bb9b14942bf7f3e4',
              300:'3c8a32713e0dc55d778b159a6bf114c1a78dc1eb001bd7c365c4e561957e8425'}
    _,model,_,_=load()
    for t,hash_value in expected.items():
        raw=json.dumps(simulate(model,t,42),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        assert hashlib.sha256(raw).hexdigest()==hash_value

def test_es_known_points_and_invalid_dimensions():
    x=np.array([[0.,0.]])
    y=np.array([[3.,4.],[0.,0.]])
    assert np.array_equal(energy_score(x,x,y),[5.,0.])
    # A paired estimate need not be clipped to zero; use the defined estimator.
    with pytest.raises(ValueError):energy_score(np.zeros((2,3)),np.zeros((2,3)),y)

def chain(n):
    r=np.zeros((n,9));r[:,0]=np.arange(n)*30;r[:,5:7]=[30,0];r[:,7:9]=[30,0]
    return r

def test_truth_windows_do_not_cross_gaps_tracks_or_inconsistent_vectors():
    r=chain(20);p=np.zeros(20,int);track=np.zeros(20,int)
    y,people,keys=windows(r,p,track,{'0':'test'})
    assert y.shape==(2,3,2) and len(keys)==2
    assert np.array_equal(y[0],[[30,0],[120,0],[300,0]])
    broken=r.copy();broken[5,5:7]=[29,0]
    assert len(windows(broken,p,track,{'0':'test'})[0])==1
    broken=r.copy();broken[5:,0]+=1
    assert len(windows(broken,p,track,{'0':'test'})[0])==1
    track[5:]=1
    assert len(windows(r,p,track,{'0':'test'})[0])==1
    with pytest.raises(ValueError):windows(r,p,track,{'0':'train'})

def test_sampling_and_baseline_use_no_actual_target_inputs():
    _,m,_,_=load()
    a=rollout(m,120,42,32768)[0];b=rollout(m,120,42,32768)[0]
    assert np.array_equal(a,b) and a.shape==(32768,2)
    assert np.max(np.linalg.norm(a,axis=1))<=480
    x=ballistic(m,30,42,128);y=ballistic(m,300,42,128)
    assert np.allclose(y,10*x)

def test_evaluation_api_reveals_aggregates_only_and_preserves_mission(client):
    mission,before=current(client);mid=load()[0]
    response=client.get('/api/ai/walking/horizon-evaluation?model_id='+mid)
    assert response.status_code==200
    result=response.json()
    assert result['people']==5 and result['windows']==214
    assert not result['long_horizon_validated'] and not result['missing_person_validated']
    assert result['horizons'][-1]['coverage_80_macro']==pytest.approx(.7258870093701555)
    assert 'window_keys_local_only' not in response.text and 'per_person' not in response.text
    assert client.get('/api/ai/walking/horizon-evaluation?model_id='+'0'*64).status_code==503
    assert client.get('/api/missions/'+mission).json()['receipt']['hash']==before['receipt']['hash']

def test_tampered_evaluation_is_not_exposed(tmp_path,monkeypatch):
    import shutil
    for f in ('horizon_evaluation.json','horizon_receipt.json'):shutil.copy2(evaluation_api.DATA/f,tmp_path/f)
    with (tmp_path/'horizon_evaluation.json').open('ab') as f:f.write(b' ')
    monkeypatch.setattr(evaluation_api,'DATA',tmp_path)
    with pytest.raises(ValueError,match='무결성'):evaluation_api.public_report(load()[0])
