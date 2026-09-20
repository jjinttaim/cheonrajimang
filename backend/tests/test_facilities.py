import io
import zipfile
from types import SimpleNamespace
import numpy as np
import pytest
from backend import core, facilities, live
from backend.tests.test_searchproof import client, current
from backend.tests.test_live import flat_terrain, point_mass
from pipeline.prepare_facilities import make_potential


def test_barrier_aware_facility_field_does_not_cross_water():
    n=9
    t=SimpleNamespace(water=np.zeros((n,n),bool),slope=np.zeros((n,n)),dem=np.zeros((n,n)),
                      lc=np.ones((n,n)),speed=np.ones((n,n)),rc=lambda lon,lat:(lat,lon))
    t.water[:,4]=True
    features=[{"geometry":{"coordinates":[2,4]},"properties":{"access":"unknown"}}]
    field=make_potential(t,features)
    assert field[4,2]==1 and field[4,3]>0
    assert np.count_nonzero(field[:,4:])==0
    assert features[0]["properties"]["model_eligible"] is True
    features[0]["properties"]["access"]="private"
    assert not make_potential(t,features).any()


def test_facility_bias_bounded_and_outside_unbiased():
    field=np.tile(np.linspace(0,1,512),(512,1))
    r=np.array([200,200,200]);c=np.array([200,200,511])
    b=facilities.bias(field,1,r,c,r,np.array([201,199,512]))
    assert b[0]>0 and b[1]<0 and b[2]==0
    assert np.max(abs(b))<=facilities.MAX_BIAS
    assert facilities.bias(None,1,r,c,r,c)==0


def test_facilities_only_change_behavior_not_other_scenarios():
    meta=facilities.latest()
    assert meta["available"]
    _,geo,_=facilities.snapshot(meta["snapshot"])
    p=next(f for f in geo["features"] if f["properties"]["category"]=="supplies" and f["properties"]["model_eligible"])
    lon,lat=p["geometry"]["coordinates"]
    params=dict(lon=lon,lat=lat,hours=.25,sigma=50,seed=42)
    before=core.compute(params)
    changed=core.compute({**params,"facility_influence":True,"facility_strength":1,"facility_snapshot":meta["snapshot"]})
    assert np.array_equal(before[0].grid,changed[0].grid)
    assert np.array_equal(before[1].grid,changed[1].grid)
    assert not np.array_equal(before[2].grid,changed[2].grid)
    old_live,_,_,_=live.forecast(before,np.zeros((3,512,512)),900,params)
    poi_live,_,_,_=live.forecast(before,np.zeros((3,512,512)),900,
        {**params,"facility_influence":True,"facility_strength":1,"facility_snapshot":meta["snapshot"]})
    assert np.array_equal(old_live[0].grid,poi_live[0].grid)
    assert np.array_equal(old_live[1].grid,poi_live[1].grid)
    assert not np.array_equal(old_live[2].grid,poi_live[2].grid)
    zero=core.compute({**params,"facility_influence":True,"facility_strength":0,"facility_snapshot":meta["snapshot"]})
    assert np.array_equal(before[2].grid,zero[2].grid)
    for d in changed:d.check()


def test_live_facility_effect_and_no_repeated_negative_evidence():
    params={"seed":42,"facility_influence":True,"facility_strength":1,"facility_snapshot":facilities.latest()["snapshot"]}
    d=point_mass();C=np.zeros((3,512,512));C[:,255:258,255:258]=.3
    out,baseline,_,_=live.forecast([d,d,d],C,0,params)
    for a,b in zip(out,baseline): assert np.allclose(a.grid,b.grid)
    field=np.tile(np.linspace(0,1,512),(512,1))
    a,_=live.project(d,1800,42,True,terrain=flat_terrain(),facility_field=field,facility_strength=1)
    a.check()
    water=flat_terrain();water.water[256,256]=True
    still,_=live.project(d,1800,42,True,terrain=water,facility_field=field,facility_strength=1)
    assert np.array_equal(still.grid,d.grid)


def test_facility_api_freezes_inputs_and_preserves_old_mission(client):
    mid,old=current(client)
    assert not old["params"]["facility_influence"]
    response=client.post("/api/missions",json={"name":"시설 테스트","hours":.25,"facility_influence":True})
    assert response.status_code==200
    new=client.get("/api/missions/"+response.json()["id"]).json()
    digest=new["params"]["facility_snapshot"]
    assert new["receipt"]["facility_snapshot"]==digest
    assert client.get("/api/missions/"+mid).json()["receipt"]["hash"]==old["receipt"]["hash"]
    geo=client.get("/api/facilities?snapshot="+digest).json()
    assert len(geo["features"])==geo["metadata"]["count"]
    assert client.get("/api/facilities?snapshot=../anything").status_code==422
    assert client.post("/api/missions",json={"facility_strength":1.5}).status_code==422
    z=zipfile.ZipFile(io.BytesIO(client.get("/api/missions/"+new["id"]+"/package.zip").content))
    assert "facilities/facilities.geojson" in z.namelist()
    assert "facilities/potential.npy" in z.namelist()


def test_missing_facility_snapshot_fails_explicitly():
    with pytest.raises(ValueError):facilities.resolve({"facility_influence":True,"facility_strength":.6})
    with pytest.raises(ValueError):facilities.resolve({"facility_influence":True,"facility_strength":float("nan")})
