import uuid
from datetime import datetime,timezone,timedelta
import numpy as np
import pytest
from backend import core,live,main,planner,daylight
from backend.trajectory import Paths,Scorer,OUTSIDE,exposure,BIN_SECONDS,MAX_PACKETS,PATH_ARRAY_BUDGET_BYTES,sample_budgets
from backend.temporal import Observation
from backend.tests.test_searchproof import client,current
from backend.tests.test_ai import request_for
from backend.tests.test_live import flat_terrain
from backend.tests.test_temporal import gpx


def paths(cells,weights=None,outside=0.):
    cells=np.asarray(cells,dtype=np.int32)
    weights=np.full(cells.shape[1],(1-outside)/cells.shape[1]) if weights is None else np.asarray(weights)
    return Paths(np.arange(len(cells))*60.,cells,cells.copy(),np.zeros(cells.shape),weights,outside).check()


def test_static_equivalence_and_duplicate_team_survival():
    p=paths([[0,1,2],[0,1,2],[0,1,2]],weights=[.2,.3,.4],outside=.1)
    scorer=Scorer([p],planner.WIDTHS)
    gain,extra=scorer.score([0,1,2],[0.,60.,120.])
    effort=np.array([.5,1,.5])/core.CELL
    expected=np.sum(p.weights[None,:]*(-np.expm1(-planner.WIDTHS[:,None]*effort)),axis=1)
    assert np.allclose(gain[0],expected)
    scorer.accept(extra);second,again=scorer.score([0,1,2],[0.,60.,120.])
    assert np.all(second<gain)
    combined=np.sum(p.weights[None,:]*(-np.expm1(-2*planner.WIDTHS[:,None]*effort)),axis=1)
    assert np.allclose((gain+second)[0],combined) and np.max(combined)<=.9


def test_same_cell_different_time_and_future_candidate():
    # One target enters cell 0 only at minute 2. Same spatial path, different timing.
    p=paths([[10000],[10000],[0],[0]])
    assert exposure(p,[0,1],[0.,60.])[0]==0
    assert exposure(p,[0,1],[120.,180.])[0]>0
    priority=Scorer([p],planner.WIDTHS).candidate_priority()
    assert priority[0]>0 # Not occupied at t=0, but is a valid future candidate.
    # Different targets at different times do not get a static-cell overlap discount.
    q=paths([[0,10000],[0,10000],[10000,0],[10000,0]])
    scorer=Scorer([q],planner.WIDTHS)
    early,extra=scorer.score([0,1],[0.,60.]);scorer.accept(extra)
    late,_=scorer.score([0,1],[120.,180.])
    assert np.allclose(early,late)


def test_path_recording_matches_project_frames_and_mass():
    grid=np.zeros((core.N,core.N));grid[200,200]=.7
    prior=core.Distribution(grid,.3).check();times=[0.,60.,120.,180.]
    final,s=live.project(prior,180,41,capture_times=times)
    p=s['trajectory'];assert np.array_equal(final.grid,p.distribution(3).grid)
    for i,seconds in enumerate(times):
        reference,_=live.project(prior,seconds,41)
        recorded=p.distribution(i)
        assert np.allclose(reference.grid,recorded.grid,atol=1e-12)
        assert reference.outside==pytest.approx(recorded.outside)
    assert len(p.signature())==64


def test_terminal_outside_and_observed_weights_recording():
    t=core.terrain();r,c=np.argwhere(t.water)[0]
    grid=np.zeros((core.N,core.N));grid[r,c]=.8
    prior=core.Distribution(grid,.2).check()
    event=Observation(60.,np.array([r*core.N+c]),np.array([1.]))
    _,s=live.project(prior,180,42,observations=[event],capture_times=[60.,120.,180.])
    p=s['trajectory'];expected=core.update(prior,np.where(grid>0,1.,0.))
    for i in range(3):
        d=p.distribution(i);assert np.allclose(d.grid,expected.grid);assert d.outside==pytest.approx(expected.outside)
    empty=core.Distribution(np.zeros_like(grid),1.)
    _,s=live.project(empty,60,42,capture_times=[0.,60.]);s['trajectory'].distribution(1).check()


def test_bounded_reproducible_sampling_and_mass():
    prior=core.Distribution(np.full((core.N,core.N),.8/(core.N**2)),.2).check()
    _,a=live.project(prior,60,42,capture_times=[0.,60.],max_packets=256)
    _,b=live.project(prior,60,42,capture_times=[0.,60.],max_packets=256)
    p=a['trajectory'];assert len(p.weights)<=256
    assert p.sampling['sampled'] and p.sampling['sample_draws']==256
    assert p.sampling['population_paths']==2*core.N*core.N
    assert p.signature()==b['trajectory'].signature()
    assert p.distribution(0).outside==pytest.approx(.2)
    for i in range(2):p.distribution(i).check()
    assert np.isfinite(Scorer([p],planner.WIDTHS).metadata()['effective_paths']).all()


def test_dynamic_api_horizon_and_no_endpoint_mixing(client):
    mid,s=current(client)
    req=request_for(s,temporal_projection=True,projection_seconds=60,moving_target=True,minutes=15)
    response=client.post(f'/api/missions/{mid}/plans',json=req)
    assert response.status_code==200,response.text
    p=response.json();assert p['moving_target'] and p['assignments']
    assert p['assumptions']['within_plan_target']=='COHERENT_TIMED_PATHS'
    assert p['target_motion']['frames'][-1]['relative_seconds']==900
    assert all(n<=MAX_PACKETS for n in p['target_motion']['unique_paths'])
    assert p['target_motion']['time_step_seconds']==15
    assert p['target_motion']['max_visit_time_error_seconds']==7.5
    assert p['target_motion']['path_array_bytes']<=PATH_ARRAY_BUDGET_BYTES
    assert not p['target_motion']['any_sampled']
    assert p['target_motion']['learned_target_motion'] is False
    for a in p['assignments']:
        assert len(a['search_arrival_seconds'])==len(a['search_cells'])
        assert np.all(np.diff(a['search_arrival_seconds'])>0)
        assert a['total_minutes']<=15 and max(a['search_arrival_seconds'])<900
    assert client.get(f'/api/missions/{mid}').json()['receipt']['hash']==s['receipt']['hash']
    saved=client.get(f'/api/missions/{mid}/plans/{p["id"]}').json()
    assert saved['target_motion']['path_sha256']==p['target_motion']['path_sha256']
    assert saved['inputs']['moving_target'] is True
    assert client.post(f'/api/missions/{mid}/plans',json={**req,'projection_seconds':21600,'idempotency_key':uuid.uuid4().hex}).status_code==422
    assert client.post(f'/api/missions/{mid}/plans',json={**req,'distribution_mode':'ENDPOINT_REFERENCE','temporal_projection':False,'projection_seconds':0}).status_code==422
    assert client.post(f'/api/missions/{mid}/plans',json={**req,'temporal_projection':False,'projection_seconds':0}).status_code==422


def test_capture_partial_edges_boundary_and_changing_daylight():
    grid=np.zeros((core.N,core.N));grid[256,511]=.8
    prior=core.Distribution(grid,.2).check();t=flat_terrain()
    clock=daylight.SolarClock('2026-09-19T06:00:00+09:00',360,33.397,126.268,.6)
    times=np.arange(0,361,60)
    _,stats=live.project(prior,360,42,terrain=t,clock=clock,capture_times=times)
    for i,at in enumerate(times):
        expected,_=live.project(prior,float(at),42,terrain=t,clock=clock)
        actual=stats['trajectory'].distribution(i)
        assert np.allclose(actual.grid,expected.grid,atol=1e-12)
        assert actual.outside==pytest.approx(expected.outside)


def test_historical_plan_paths_do_not_use_future_approved_tracks(client):
    base=datetime(2026,9,19,tzinfo=timezone.utc)
    s=client.post('/api/missions',json={'analysis_at':base.isoformat(),
           'missing_at':(base-timedelta(minutes=30)).isoformat(),'hours':.5}).json()
    mid=s['id'];before=client.get(f'/api/missions/{mid}').json()
    raw=gpx(base+timedelta(seconds=1))
    tid=client.post(f'/api/missions/{mid}/tracks',files={'file':('example.gpx',raw)}).json()['id']
    response=client.post(f'/api/missions/{mid}/tracks/{tid}/apply',json={'expected_version':0,'outcome':'COMPLETED_NO_FIND'})
    assert response.status_code==200,response.text
    after=client.get(f'/api/missions/{mid}').json()
    args=str(main.DB),mid
    old,ot=main.cached_plan_paths(*args,0,before['receipt']['hash'],0,15)
    revised,nt=main.cached_plan_paths(*args,1,after['receipt']['hash'],0,15)
    assert ot['observation_bins']==nt['observation_bins']==0
    assert [p.signature() for p in old]==[p.signature() for p in revised]
    later,info=main.cached_plan_paths(*args,1,after['receipt']['hash'],180,15)
    original,_=main.cached_plan_paths(*args,0,before['receipt']['hash'],180,15)
    assert info['observation_bins']==3 and not info['future_observations_used']
    assert any(not np.array_equal(p.weights,q.weights) for p,q in zip(later,original))


def test_adaptive_budget_keeps_all_small_cases_and_bounds_large_cases():
    assert sample_budgets([16124,2928,3484],121)==[None,None,None]
    assert sample_budgets([44838,9594,12442],121)==[None,None,None]
    for populations,frames,budget in [([524288]*3,481,PATH_ARRAY_BUDGET_BYTES),
                                      ([4,500,0],61,100000),([0,0,0],481,PATH_ARRAY_BUDGET_BYTES)]:
        chosen=sample_budgets(populations,frames,budget)
        counts=[n if b is None else b for n,b in zip(populations,chosen)]
        assert sum(counts)*(16*frames+8)+8*frames*len(counts)<=budget
        assert all(0<=n<=min(original,MAX_PACKETS) for n,original in zip(counts,populations))
        assert all(n>0 for n,original in zip(counts,populations) if original)
    assert sample_budgets([4,500,0],61,100000)[0] is None
    with pytest.raises(ValueError,match='메모리'):
        sample_budgets([10,10,10],61,1000)


def test_fast_encounter_is_not_lost_between_legacy_frames():
    # Synthetic numerical counterexample: target moves 30 m every 20 s.
    # At t=30 its represented mass lies in cells 1/2, both searched then.
    def crossing(step):
        times=np.arange(0,61,step,dtype=float)
        x=times/20
        left=np.floor(x).astype(np.int32)[:,None]
        return Paths(times,left,left+1,(x-np.floor(x))[:,None],np.array([1.]),0.).check()
    coarse=crossing(60)
    assert exposure(coarse,[1,2],[20.,40.])[0]==0
    current=crossing(BIN_SECONDS)
    assert exposure(current,[1,2],[20.,40.])[0]==pytest.approx(1/(2*core.CELL))
    meta=Scorer([current],planner.WIDTHS).metadata()
    assert meta['time_step_seconds']==BIN_SECONDS and meta['max_visit_time_error_seconds']==BIN_SECONDS/2
    # Metadata describes actual supplied frames, not the application's default.
    assert Scorer([coarse],planner.WIDTHS).metadata()['max_visit_time_error_seconds']==30


@pytest.mark.parametrize('cells,arrival',[
    ([0,1],[0.,float('nan')]),([0,1],[0.,float('inf')]),
    ([0.,1.5],[0.,60.]),([0,2],[0.,60.]),([511,512],[0.,60.]),
])
def test_invalid_visits_cannot_silently_create_exposure(cells,arrival):
    with pytest.raises(ValueError):exposure(paths([[0],[0]]),cells,arrival)
