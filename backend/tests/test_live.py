from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import numpy as np
import pytest
from backend import core, live, main
from backend.tests.test_searchproof import client, current


def flat_terrain(speed=1.0):
    return SimpleNamespace(dem=np.zeros((512,512)),speed=np.full((512,512),speed),
                           roads=np.zeros((512,512)),water=np.zeros((512,512),dtype=bool),
                           slope=np.zeros((512,512)))


def point_mass(row=256,col=256,outside=.1):
    grid=np.zeros((512,512));grid[row,col]=1-outside
    return core.Distribution(grid,outside).check()


def test_live_zero_time_is_exact_and_reproducible():
    d=point_mass()
    result,_=live.project(d,0,42,terrain=flat_terrain())
    assert np.array_equal(result.grid,d.grid)
    assert result.outside==d.outside
    a,_=live.project(d,600,42,terrain=flat_terrain())
    b,_=live.project(d,600,42,terrain=flat_terrain())
    assert np.array_equal(a.grid,b.grid)
    assert not np.array_equal(a.grid,d.grid)
    a.check()


def test_live_clock_conserves_mass_and_respects_speed():
    t=flat_terrain()
    d=point_mass()
    speed=1.667*np.exp(-3.5*.05)*core.ASSUMPTIONS["walking_mobility"]
    for seconds in (1,15,300,1800):
        p,s=live.project(d,seconds,7,terrain=t)
        p.check()
        assert s["distance_m"]==pytest.approx(speed*seconds)
        assert p.outside>=d.outside
    _,slow=live.project(d,600,7,terrain=flat_terrain(.3))
    _,fast=live.project(d,600,7,terrain=t)
    assert slow["distance_m"]<fast["distance_m"]*.31


def test_live_terminal_water_not_erased_and_outside_not_folded():
    t=flat_terrain()
    t.water[256,256]=True
    d=point_mass()
    out,s=live.project(d,1800,42,terrain=t)
    assert np.array_equal(out.grid,d.grid)
    assert out.outside==d.outside
    assert s["mean_kmh"]==0
    edge,_=live.project(point_mass(0,0),1800,42,terrain=flat_terrain())
    edge.check()
    assert edge.outside>.1


def test_live_directional_slope_changes_speed():
    t=flat_terrain()
    t.dem[255,256]=12
    t.dem[257,256]=-12
    up,_=live.walking_speed(t,np.array([256]),np.array([256]),np.array([255]),np.array([256]),np.array([30]))
    flat,_=live.walking_speed(t,np.array([256]),np.array([256]),np.array([256]),np.array([257]),np.array([30]))
    down,_=live.walking_speed(t,np.array([256]),np.array([256]),np.array([257]),np.array([256]),np.array([30]))
    assert up[0]<flat[0]
    assert up[0]<down[0]


def test_live_no_find_evidence_applied_once():
    params=dict(lon=126.268,lat=33.397,hours=.25,sigma=50,seed=42)
    priors=core.compute(params)
    C=np.zeros((3,512,512));C[:,250:275,250:275]=1.1
    projections,baseline,_,_=live.forecast(priors,C,0,params)
    for initial,expected,projected in zip(priors,baseline,projections):
        correct=core.update(initial,C[1])
        assert np.allclose(correct.grid,expected.grid)
        assert np.allclose(projected.grid,correct.grid)
        assert projected.outside==pytest.approx(correct.outside)


def test_live_api_does_not_write_and_reports_traffic_disconnected(client):
    mid,initial=current(client)
    response=client.get(f"/api/missions/{mid}/forecast?version=0&seconds=1800")
    assert response.status_code==200
    p=response.json()
    assert p["saved"] is False
    assert p["speed"]["traffic_connected"] is False
    assert p["projection_seconds"]==1800
    assert p["inside_mass"]+p["outside_mixed"]==pytest.approx(1)
    after=client.get(f"/api/missions/{mid}").json()
    assert after["receipt"]["hash"]==initial["receipt"]["hash"]
    assert len(after["versions"])==1
    png=client.get(f"/api/missions/{mid}/forecast/combined.png?version=0&seconds=1800")
    assert png.content.startswith(b"\x89PNG")
    assert client.get(f"/api/missions/{mid}/forecast?seconds=-1").status_code==422
    assert client.get(f"/api/missions/{mid}/forecast?seconds=21601").status_code==422


def test_live_server_clock_and_expiry_are_explicit(client):
    mid,_=current(client)
    with main.connect() as c:
        stamp=(datetime.now(timezone.utc)-timedelta(seconds=80)).isoformat()
        c.execute("UPDATE versions SET created_at=? WHERE mission=? AND number=0",(stamp,mid))
    p=client.get(f"/api/missions/{mid}/forecast").json()
    assert 75<=p["projection_seconds"]<=90
    assert p["expired"] is False
    with main.connect() as c:
        stamp=(datetime.now(timezone.utc)-timedelta(hours=7)).isoformat()
        c.execute("UPDATE versions SET created_at=? WHERE mission=? AND number=0",(stamp,mid))
    p=client.get(f"/api/missions/{mid}/forecast").json()
    assert p["expired"] is True
    assert p["projection_seconds"]==21600

