"""Searcher-line (team size × spacing) effort bands in coverage and planning."""
import uuid
import numpy as np
import pytest
from backend import core, main, planner, ml_models
from backend.trajectory import Paths, Scorer, exposure
from backend.tests.test_searchproof import client, current
from backend.tests.test_ai import request_for


def demo_segments():
    params={"lon":126.268,"lat":33.397}
    segments,_=core.parse_gpx(core.demo_gpx(params))
    return segments


def test_team_band_validation_and_geometry():
    single=core.team_band(1,15)
    assert single["band_width_m"]==0 and single["band_rows"]==1 and single["effort_multiplier"]==1
    six=core.team_band(6,15)
    assert six["band_width_m"]==75 and six["band_rows"]==2 and six["effort_multiplier"]==6
    assert core.team_band(4,10)["band_rows"]==1 # 30 m band fits one row
    for bad in ((0,15),(13,15),(3,2),(3,61),(2,float("nan"))):
        with pytest.raises(ValueError): core.team_band(*bad)


def test_single_observer_matches_original_and_line_scales_total_effort():
    segments=demo_segments()
    base,summary=core.coverage_for(segments)
    same,_=core.coverage_for(segments,1,15.)
    assert np.array_equal(base,same) and summary["team"]["team_size"]==1
    line,line_summary=core.coverage_for(segments,6,15.)
    # n searchers contribute n sweep widths of effort (kernel is normalised).
    assert line[1].sum()==pytest.approx(6*base[1].sum(),rel=1e-6)
    assert line_summary["team"]["band_width_m"]==75
    # Effort is spread laterally: the footprint grows and the peak grows less than 6x.
    assert np.count_nonzero(line[1]>.01)>np.count_nonzero(base[1]>.01)
    assert line[1].max()<6*base[1].max()
    for k in range(3): assert (line[k]>=0).all() and np.isfinite(line[k]).all()


def test_serpentine_bands_are_connected_and_cover_the_zone():
    original=planner.serpentine(500,1)[0][0]
    for k in (1,2,3,4,6,16):
        (walk,covered),(back,covered_back)=planner.serpentine(500,k)
        r,c=np.divmod(walk,core.N)
        assert np.all(np.abs(np.diff(r))+np.abs(np.diff(c))==1)
        assert covered.shape==(len(walk),k) and len(np.unique(covered[covered>=0]))==256
        assert np.array_equal(back,walk[::-1]) and np.array_equal(covered_back,covered[::-1])
        zr,zc=np.divmod(covered[covered>=0]//core.N,32)[0]//1,0
    # k=1 keeps the historical single-observer order (every cell walked once).
    assert len(original)==256 and len(np.unique(original))==256


def test_exposure_band_matches_effort_scaling():
    cells=np.array([0,1,2],dtype=np.int32)
    p=Paths(np.arange(3)*60.,np.tile(cells,(3,1)),np.tile(cells,(3,1)),np.zeros((3,3)),np.full(3,.3),.1).check()
    single=exposure(p,cells,[0.,60.,120.])
    band=np.column_stack([cells,cells+core.N])
    doubled=exposure(p,cells,[0.,60.,120.],band=band,scale=2/2)
    # Same effort per swept cell when two searchers cover two rows.
    assert np.allclose(single,doubled)
    stronger=exposure(p,cells,[0.,60.,120.],band=band,scale=6/2)
    assert np.allclose(stronger,3*single)
    with pytest.raises(ValueError): exposure(p,cells,[0.,60.,120.],scale=0)


def test_plan_line_team_sweeps_more_area_and_reports_geometry(client):
    mid,s=current(client)
    def run(n,spacing):
        req=request_for(s,minutes=60,teams=[{"name":"1팀","lon":s["params"]["lon"],"lat":s["params"]["lat"],"search_type":"sweep","team_size":n,"spacing_m":spacing}])
        r=client.post(f"/api/missions/{mid}/plans",json=req); assert r.status_code==200,r.text
        return r.json()
    one=run(1,15); ten=run(10,20)
    a,b=one["assignments"][0],ten["assignments"][0]
    assert a["band_rows"]==1 and a["team_size"]==1 and b["band_rows"]==6 and b["team_size"]==10 and b["spacing_m"]==20
    assert b["swept_area_m2"]>a["swept_area_m2"]
    assert b["assumed_incremental_detection_range"][1]>a["assumed_incremental_detection_range"][1]
    assert set(b["swept_cells"])>=set(b["search_cells"])
    assert one["assumptions"]["candidate_rule"]=="GEOMETRIC_MEAN_CONSENSUS_REACHABLE"
    bad=request_for(s,teams=[{"name":"1팀","lon":s["params"]["lon"],"lat":s["params"]["lat"],"search_type":"sweep","team_size":40}])
    assert client.post(f"/api/missions/{mid}/plans",json=bad).status_code==422


def test_upload_records_team_geometry_and_rejects_bad_values(client):
    mid,s=current(client)
    gpx=client.get(f"/api/missions/{mid}/demo.gpx").content
    r=client.post(f"/api/missions/{mid}/tracks",files={"file":("t.gpx",gpx,"application/gpx+xml")},data={"team_size":"5","spacing_m":"12"})
    assert r.status_code==200,r.text
    assert r.json()["summary"]["team"]=={"team_size":5,"spacing_m":12.,"band_width_m":48.,"band_rows":2,"effort_multiplier":5,"assumption":"RECORDER_MID_BAND_UNIFORM"}
    bad=client.post(f"/api/missions/{mid}/tracks",files={"file":("u.gpx",gpx,"application/gpx+xml")},data={"team_size":"0"})
    assert bad.status_code==422
    demo=client.post(f"/api/missions/{mid}/demo-track",json={"team_size":3,"spacing_m":20})
    assert demo.status_code==409 # same geometry already registered above
