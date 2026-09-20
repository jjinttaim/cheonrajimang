from datetime import datetime, timezone, timedelta
import numpy as np
import pytest
from backend import core, live, main, temporal
from backend.tests.test_live import point_mass, flat_terrain
from backend.tests.test_searchproof import client


def event(at, cells, intensity=1.):
    indices=np.sort(np.array([r*core.N+c for r,c in cells]))
    return temporal.Observation(at,indices,np.full(len(indices),intensity))


def test_observation_sparse_lookup_and_t0_equal_static_update():
    d=point_mass();e=event(0,[(256,256)],1.2)
    assert np.array_equal(e.at(np.array([256,-1,512,256]),np.array([256,0,0,257])),[1.2,0,0,0])
    p,_=live.project(d,0,42,terrain=flat_terrain(),observations=[e])
    C=np.zeros_like(d.grid);C[256,256]=1.2
    q=core.update(d,C)
    assert np.allclose(p.grid,q.grid) and p.outside==pytest.approx(q.outside)


def test_observed_time_not_final_position_future_excluded_and_order_stable():
    d=point_mass();t=flat_terrain()
    early=event(1,[(256,256)],2.)
    late=event(300,[(256,256)],2.)
    a,_=live.project(d,600,7,terrain=t,observations=[early])
    b,_=live.project(d,600,7,terrain=t,observations=[late])
    assert a.outside>b.outside # Search after departure cannot erase past occupancy.
    a.check();b.check()
    before,_=live.project(d,60,7,terrain=t,observations=[late])
    plain,_=live.project(d,60,7,terrain=t)
    assert np.array_equal(before.grid,plain.grid) and before.outside==plain.outside
    first,_=live.project(d,600,7,terrain=t,observations=[early,late])
    reverse,_=live.project(d,600,7,terrain=t,observations=[late,early])
    assert np.array_equal(first.grid,reverse.grid) and first.outside==reverse.outside


def test_terminal_and_outside_mass_observation_applied_once():
    t=flat_terrain();t.water[256,256]=True;d=point_mass()
    events=[event(20,[(256,256)],.7),event(40,[(256,256)],.9)]
    p,s=live.project(d,600,42,terrain=t,observations=events)
    C=np.zeros_like(d.grid);C[256,256]=1.6
    q=core.update(d,C)
    assert np.allclose(p.grid,q.grid) and p.outside==pytest.approx(q.outside)
    assert s['mean_kmh']==0 and s['observation_bins']==2


def gpx(start,offset=0):
    t=core.terrain();r,c=t.rc(126.268,33.397)
    pts=[]
    for i in range(5):
        lon,lat=t.lonlat(r+offset,c+i*.5)
        at=(start+timedelta(seconds=i*30)).isoformat()
        pts.append(f'<trkpt lon="{lon}" lat="{lat}"><time>{at}</time></trkpt>')
    return ('<gpx><trk><trkseg>'+''.join(pts)+'</trkseg></trk></gpx>').encode()


def test_event_binning_preserves_coverage_and_rejects_prebaseline():
    base=datetime(2026,9,19,tzinfo=timezone.utc)
    raw=gpx(base+timedelta(seconds=1))
    events=temporal.observations([raw],base.isoformat(),3600)
    assert [e.seconds for e in events]==[60,120,180]
    expected=core.coverage_for(core.parse_gpx(raw)[0])[0][1].ravel()
    total=np.zeros_like(expected)
    for e in events:total[e.indices]+=e.values
    assert np.allclose(total,expected,atol=1e-14)
    with pytest.raises(ValueError,match='이전에'):
        temporal.observations([raw],(base+timedelta(minutes=10)).isoformat(),3600)


def test_api_late_upload_order_independence_and_old_versions_unchanged(client):
    base=datetime(2026,9,19,tzinfo=timezone.utc)
    data={'missing_at':(base-timedelta(minutes=30)).isoformat(),'analysis_at':base.isoformat(),'hours':.5}
    ids=[client.post('/api/missions',json=data).json()['id'] for _ in range(2)]
    raws=[gpx(base+timedelta(seconds=1)),gpx(base+timedelta(minutes=5),offset=1)]
    originals=[]
    for mid,ordered in zip(ids,[raws,list(reversed(raws))]):
        originals.append(client.get(f'/api/missions/{mid}').json()['receipt']['hash'])
        for version,raw in enumerate(ordered):
            tid=client.post(f'/api/missions/{mid}/tracks',files={'file':('sample.gpx',raw)}).json()['id']
            result=client.post(f'/api/missions/{mid}/tracks/{tid}/apply',json={'expected_version':version,'outcome':'COMPLETED_NO_FIND'})
            assert result.status_code==200,result.text
    results=[client.get(f'/api/missions/{mid}/forecast?version=2&seconds=600').json() for mid in ids]
    assert results[0]['zones']==results[1]['zones']
    assert results[0]['outside']==results[1]['outside']
    assert results[0]['speed']['observation_bins']==5 # 60,120,180,360,420 seconds.
    assert results[0]['base_at']==base.isoformat()
    for mid,old in zip(ids,originals):
        assert client.get(f'/api/missions/{mid}?version=0').json()['receipt']['hash']==old
        assert client.get(f'/api/missions/{mid}/forecast?version=2&seconds=0').json()['speed']['observation_bins']==0
