"""Rest-of-world share, learned distance prior and geometric-mean consensus."""
import numpy as np
import pytest
from backend import core, main, ml_models, planner, walking_reference
from backend.tests.test_searchproof import client, current


def test_with_row_conserves_mass_and_validates():
    grid=np.zeros((core.N,core.N));grid[10,10]=.9
    d=core.Distribution(grid,.1).check()
    r=core.with_row(d,.2)
    assert r.grid.sum()==pytest.approx(.72) and r.outside==pytest.approx(.28)
    assert core.with_row(d,0).outside==pytest.approx(.1)
    for bad in (-.01,.51,float("nan")):
        with pytest.raises(ValueError): core.with_row(d,bad)
    # Negative evidence inside the map raises the reserved share, never lowers it.
    C=np.zeros_like(grid);C[10,10]=3
    assert core.update(r,C).outside>r.outside


def test_consensus_is_geometric_mean_with_zero_veto():
    m=np.array([[.04,0.,.01],[.01,.05,.01],[.01,.05,.01]])
    c=core.consensus(m)
    assert c[0]==pytest.approx((.04*.01*.01)**(1/3)) and c[1]==0 and c[2]==pytest.approx(.01)
    assert np.all(c<=m.max(axis=0)+1e-12) and np.all(c>=np.where(m.min(axis=0)>0,m.min(axis=0),0)-1e-12)
    with pytest.raises(ValueError): core.consensus(np.array([[-1.,0.]]))


def test_learned_radial_respects_reach_cap_and_seed():
    t=core.terrain();prm=ml_models.load_bundle()["endpoint"]["models"]["lognorm"]["params"]
    lon,lat=126.268,33.397
    quarter=core.learned_radial(t,lon,lat,.25,5,3,prm)
    again=core.learned_radial(t,lon,lat,.25,5,3,prm)
    assert np.array_equal(quarter.grid,again.grid)
    r0,c0=t.rc(lon,lat);rows,cols=np.nonzero(quarter.grid)
    radius=core.CELL*np.hypot(rows+.5-r0,cols+.5-c0)
    reach=core.ASSUMPTIONS["reach_speed_kmh"]*1000*.25
    assert radius.max()<=reach+3*5+2*core.CELL # cap plus position noise and cell size
    three=core.learned_radial(t,lon,lat,3,5,3,prm)
    assert np.count_nonzero(three.grid)>np.count_nonzero(quarter.grid)
    with pytest.raises(ValueError): core.learned_radial(t,lon,lat,3,5,3,[0,0,1])


def test_new_mission_records_learned_model_row_and_agreement(client):
    r=client.post("/api/missions",json={"name":"row","lon":126.268,"lat":33.397,"hours":3,"sigma":50,"seed":9})
    assert r.status_code==200,r.text
    s=client.get("/api/missions/"+r.json()["id"]).json()
    used=s["params"]["distance_model_used"]
    assert used["used"]=="learned_lognormal" and used["cases"]==65 and used["bundle_id"]==ml_models.current_id()
    assert s["params"]["row_share"]==pytest.approx(.1)
    assert all(o>=.1-1e-9 for o in s["outside"]) and s["outside_mixed"]>=.1-1e-9
    assert s["inside_mass"]+s["outside_mixed"]==pytest.approx(1)
    for f in s["zones"]["features"][:20]: assert 0<=f["properties"]["agreement"]<=1
    # Receipt keeps the exact parameters that produced the map.
    assert s["receipt"]["parameters"]["distance_model_used"]["params"]==used["params"]
    # Not-found evidence grows the reserved share.
    tr=client.post("/api/missions/"+s["id"]+"/demo-track").json()
    assert client.post(f"/api/missions/{s['id']}/tracks/{tr['id']}/apply",json={"outcome":"COMPLETED_NO_FIND","expected_version":0}).status_code==200
    v1=client.get("/api/missions/"+s["id"]).json()
    assert v1["outside_mixed"]>s["outside_mixed"]


def test_assumed_model_and_zero_row_reproduce_legacy_behaviour(client):
    r=client.post("/api/missions",json={"name":"legacy","lon":126.268,"lat":33.397,"hours":3,"sigma":50,"seed":9,"distance_model":"assumed_sqrt_time","row_share":0})
    s=client.get("/api/missions/"+r.json()["id"]).json()
    assert s["params"]["distance_model_used"]["used"]=="assumed_sqrt_time"
    assert s["outside_mixed"]==pytest.approx(0,abs=1e-6)
    assert client.post("/api/missions",json={"name":"bad","row_share":.9}).status_code==422
    assert client.post("/api/missions",json={"name":"bad","distance_model":"magic"}).status_code==422


def test_endpoint_reference_applies_row():
    b=ml_models.load_bundle()
    plain=planner.endpoint_reference(b["bundle_id"],126.268,33.397,50,1,"lognorm",0.)
    reserved=planner.endpoint_reference(b["bundle_id"],126.268,33.397,50,1,"lognorm",.25)
    assert reserved.outside==pytest.approx(.25+.75*plain.outside)
    assert np.allclose(reserved.grid,.75*plain.grid)


def test_status_lists_learned_and_assumed_components(client):
    st=client.get("/api/ai/status").json()
    keys={c["key"]:c["status"] for c in st["components"]}
    assert keys["distance_prior"]=="LEARNED" and keys["searcher_speed"]=="LEARNED"
    assert keys["terrain_endpoint"]=="NOT_ADOPTED"
    # GeoLife 모델은 로컬 전용(저장소 미포함). 파일이 없는 컴퓨터에서는 구성요소 표가 UNKNOWN을 보여야 한다.
    walking_local=(walking_reference.MODELS/"current.json").exists()
    assert keys["walking_reference"]==("NOT_ADOPTED" if walking_local else "UNKNOWN")
    assert keys["detection"]=="ASSUMPTION" and keys["row"]=="ASSUMPTION"
    assert st["default_distance_model"]=="learned_lognormal"
    base=client.get("/api/base").json()
    assert base["deploy_profile"] in ("research","commercial-ready") and base["row_share_default"]==pytest.approx(.1)
