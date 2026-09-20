import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from fastapi.testclient import TestClient
from backend import core, main

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,"DB",tmp_path/"test.sqlite3")
    main.render_image.cache_clear()
    with TestClient(main.app) as c: yield c

def current(client):
    mid=client.get("/api/missions").json()[0]["id"]
    return mid,client.get("/api/missions/"+mid).json()

def test_mass_and_cellwise_update():
    grid=np.zeros((512,512));grid[0:16,0:16]=.8/256
    d=core.Distribution(grid,.2).check()
    C=np.zeros_like(grid);C[0,0]=2
    out=core.update(d,C)
    out.check()
    # Same 480m sector, opposite corner must not be attenuated.
    ratio=out.grid/grid.clip(1e-15)
    assert ratio[15,15]>1
    assert ratio[0,0]<ratio[15,15]
    assert out.outside>d.outside
    mix=core.combine([d,d,d]); assert mix.outside==pytest.approx(.2)

def test_zero_intensity_and_invalid_probability():
    d=core.Distribution(np.ones((3,3))*.1,.1).check()
    assert np.allclose(core.update(d,np.zeros((3,3))).grid,d.grid)
    with pytest.raises(ValueError):core.Distribution(np.ones((2,2)),.1).check()
    with pytest.raises(ValueError):core.update(d,-np.ones((3,3)))

def test_boundary_samples_not_clipped():
    d=core.histogram(np.array([-2,0,513,511]),np.array([0,0,500,511]),4)
    assert d.outside==.5
    assert d.grid[0,0]==.25
    assert d.grid.sum()==.5

def test_reproducible_and_safe_recommendations():
    params=dict(lon=126.268,lat=33.397,hours=.25,sigma=50,seed=4)
    a=core.compute(params);b=core.compute(params)
    for x,y in zip(a,b):assert np.array_equal(x.grid,y.grid)
    summary,_,_=core.summarize(a,np.zeros((3,512,512)))
    for f in summary["zones"]["features"]:
        p=f["properties"]
        if p["access"]=="AGENCY_ONLY":assert p["score"]==0

def test_no_time_budget_cannot_complete_cell():
    t=core.terrain(); r,c=t.rc(126.268,33.397)
    d=core.walk(t,126.268,33.397,1/3600,0,42)
    assert d.grid[int(r),int(c)]==1

def test_upload_approval_dedup_and_historical_state(client):
    mid,v0=current(client)
    tr=client.post(f"/api/missions/{mid}/demo-track").json()
    assert client.get(f"/api/missions/{mid}").json()["version"]==0
    assert client.post(f"/api/missions/{mid}/demo-track").status_code==409
    path=f"/api/missions/{mid}/tracks/{tr['id']}/apply"
    assert client.post(path,json={"outcome":"FOUND","expected_version":0}).status_code==422
    assert client.post(path,json={"outcome":"COMPLETED_NO_FIND","expected_version":3}).status_code==409
    response=client.post(path,json={"outcome":"COMPLETED_NO_FIND","expected_version":0})
    assert response.status_code==200
    assert client.post(path,json={"outcome":"COMPLETED_NO_FIND","expected_version":1}).status_code==409
    v1=client.get(f"/api/missions/{mid}").json()
    assert v1["version"]==1 and v1["coverage_km2"]>0
    assert v1["receipt"]["parent_hash"]==v0["receipt"]["hash"]
    assert v1["receipt"]["evidence"]["track_id"]==tr["id"]
    assert "road_distance.npz" in v1["receipt"]["input_files"]
    assert "backend/core.py" in v1["receipt"]["code_files"]
    assert all(f["properties"]["previous_rank"] is not None for f in v1["zones"]["features"])
    assert client.get(f"/api/missions/{mid}?version=0").json()["coverage_km2"]==0
    assert v1["outside_mixed"]+v1["inside_mass"]==pytest.approx(1)
    assert client.get(f"/api/missions/{mid}/layers/shadow.png?version=1").content.startswith(b"\x89PNG")

def test_concurrent_approval_exactly_once(client):
    mid,_=current(client)
    tid=client.post(f"/api/missions/{mid}/demo-track").json()["id"]
    def request():
        return client.post(f"/api/missions/{mid}/tracks/{tid}/apply",json={"outcome":"COMPLETED_NO_FIND","expected_version":0}).status_code
    with ThreadPoolExecutor(2) as pool: codes=list(pool.map(lambda _:request(),range(2)))
    assert sorted(codes)==[200,409]
    assert len(client.get(f"/api/missions/{mid}").json()["versions"])==2

def test_gpx_rejects_gaps_missing_time_and_entity(client):
    mid,s=current(client)
    good=core.demo_gpx(s["params"])
    points,_=core.parse_gpx(good)
    C,stats=core.coverage_for(points)
    assert np.isfinite(C).all() and (C[0]<=C[1]).all() and (C[1]<=C[2]).all()
    assert np.count_nonzero(C[1])<512*512*.05
    with pytest.raises(ValueError):core.parse_gpx(b'<gpx><trk><trkseg><trkpt lat="33" lon="126"/></trkseg></trk></gpx>')
    with pytest.raises(ValueError):core.parse_gpx(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><gpx>&a;</gpx>')
    broken=[[list(p) for p in points[0][:2]]];broken[0][1][2]=broken[0][0][2]+200
    with pytest.raises(ValueError):core.coverage_for(broken)
    response=client.post(f"/api/missions/{mid}/tracks",files={"file":("bad.gpx",b"not xml")})
    assert response.status_code==422

def test_package_integrity_and_tampering(client):
    mid,_=current(client)
    original=client.get(f"/api/missions/{mid}/package.zip").content
    ok=client.post("/api/verify",files={"file":("package.zip",original)})
    assert ok.json()["valid"]
    with zipfile.ZipFile(io.BytesIO(original)) as z: files={n:z.read(n) for n in z.namelist()}
    files["zones.csv"]+=b"\ntampered"
    changed=io.BytesIO()
    with zipfile.ZipFile(changed,"w",zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items():z.writestr(name,raw)
    bad=client.post("/api/verify",files={"file":("package.zip",changed.getvalue())})
    assert bad.json()["valid"] is False
    assert "zones.csv" in bad.json()["failed"]

def test_ledger_never_automatically_updates_map(client):
    mid,_=current(client)
    r=client.post(f"/api/missions/{mid}/ledger",json={"note":"합성 표식 목격 (미확인)","source":"훈련 참여자"})
    assert r.json()["map_changed"] is False
    assert client.get(f"/api/missions/{mid}").json()["version"]==0

def test_local_only_origin_and_sensitive_paths(client):
    mid,_=current(client)
    assert client.post(f"/api/missions/{mid}/demo-track",headers={"Origin":"https://evil.example"}).status_code==403
    assert client.get("/api/base/../runtime/searchproof.sqlite3").status_code==404
    assert client.get("/api/missions",headers={"Host":"evil.example"}).status_code==400

def test_risk_gate_and_report_escaping(client):
    mid,s=current(client)
    risky=next(f["properties"]["id"] for f in s["zones"]["features"] if f["properties"]["access"]=="AGENCY_ONLY")
    assert client.post(f"/api/missions/{mid}/zones/{risky}/approve").status_code==422
    client.post(f"/api/missions/{mid}/ledger",json={"note":"<script>alert(1)</script>","source":"test"})
    report=client.get(f"/api/missions/{mid}/report").text
    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;" in report
