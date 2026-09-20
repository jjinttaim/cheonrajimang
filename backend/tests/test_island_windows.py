"""Island-wide master grid: every mission computes on a 512×512 window cut around its last-seen point."""
import numpy as np
import pytest
from backend import core, facilities, ml_models
from backend.tests.test_searchproof import client, current


def test_master_is_lattice_aligned_and_contains_legacy_window():
    m=core.master()
    assert m.meta["region"]=="제주도" and m.width>core.N and m.height>core.N
    assert (m.height-core.N)%core.LATTICE==0 and (m.width-core.N)%core.LATTICE==0
    legacy=core.terrain()
    assert (legacy.row0,legacy.col0)==core.LEGACY_WINDOW and legacy.window["legacy"]
    # The original 한림읍 origin (EPSG:32652) is preserved exactly.
    assert legacy.meta["x0"]==237930 and legacy.meta["y0"]==3706110
    assert legacy.dem.shape==(core.N,core.N) and legacy.hazard.dtype==bool


def test_window_follows_the_point_and_rejects_off_grid_points():
    m=core.master()
    seongsan=m.window_origin(126.92,33.46)          # 성산
    hallim=m.window_origin(126.268,33.397)          # default mission
    assert hallim==core.LEGACY_WINDOW and seongsan!=hallim
    t=core.terrain(*seongsan)
    r,c=t.rc(126.92,33.46)
    assert 224<=r<=288 and 224<=c<=288            # point within ±32 cells of the window centre
    with pytest.raises(ValueError): m.window_origin(127.5,33.4)
    with pytest.raises(ValueError): m.window_info(1,0)


def test_missions_far_from_hallim_compute_plan_and_hand_over(client):
    r=client.post("/api/missions",json={"name":"성산 훈련","lon":126.92,"lat":33.46,"hours":1,"facility_influence":True})
    assert r.status_code==200,r.text
    mid=r.json()["id"]; s=client.get("/api/missions/"+mid).json()
    assert not s["window"]["legacy"] and s["params"]["window"]["row0"]==s["window"]["row0"]
    lons=[p[0] for f in s["zones"]["features"] for p in f["geometry"]["coordinates"][0]]
    assert min(lons)>126.8 and max(lons)<127.05 and s["inside_mass"]>.5
    assert s["receipt"]["analysis_window"]["row0"]==s["window"]["row0"]
    # Facilities are filtered to the window and the potential is window-local.
    geo=client.get("/api/facilities?mission="+mid).json()
    assert 0<len(geo["features"])<geo["metadata"]["count"]
    field=facilities.window_field(s["params"]["facility_snapshot"],s["window"]["row0"],s["window"]["col0"])
    assert field.shape==(core.N,core.N) and field.max()<=1 and field.max()>0
    # Synthetic track, live forecast, AI plan and basemap all use the same window.
    tr=client.post("/api/missions/"+mid+"/demo-track",json={"team_size":4,"spacing_m":15}).json()
    assert tr["summary"]["coverage_km2"]>0
    assert client.post("/api/missions/"+mid+"/tracks/"+tr["id"]+"/apply",json={"outcome":"COMPLETED_NO_FIND","expected_version":0}).status_code==200
    live=client.get("/api/missions/"+mid+"/forecast?version=1&seconds=900").json()
    assert live["inside_mass"]>0 and live["speed"]["mean_kmh"]>0
    plan=client.post("/api/missions/"+mid+"/plans",json={"base_version":1,"model_id":ml_models.current_id(),
        "teams":[{"name":"1팀","lon":126.92,"lat":33.46,"search_type":"sweep","team_size":4,"spacing_m":15}],"minutes":60,"idempotency_key":"island-plan-1"})
    assert plan.status_code==200,plan.text
    routes=plan.json()["routes"]["features"]
    assert plan.json()["assignments"] and all(126.8<x<127.05 for f in routes for x,_ in f["geometry"]["coordinates"])
    png=client.get("/api/missions/"+mid+"/basemap.png"); assert png.status_code==200 and png.content[:4]==b"\x89PNG"
    z=client.get("/api/missions/"+mid+"/package.zip"); assert z.status_code==200


def test_legacy_missions_without_window_keep_the_hallim_window(client):
    mid,s=current(client)
    params={k:v for k,v in s["params"].items() if k!="window"}
    assert core.window_of(params)==core.LEGACY_WINDOW
    assert core.terrain_for(params) is core.terrain()
    assert client.get("/api/missions/"+mid).json()["window"]["corners"]==core.terrain().window["corners"]


def test_team_start_outside_window_is_rejected(client):
    mid,s=current(client)
    plan=client.post("/api/missions/"+mid+"/plans",json={"base_version":s["version"],"model_id":ml_models.current_id(),
        "teams":[{"name":"1팀","lon":126.92,"lat":33.46,"search_type":"sweep"}],"minutes":30,"idempotency_key":"island-plan-2"})
    assert plan.status_code==422 and "분석 영역 밖" in plan.json()["detail"]
