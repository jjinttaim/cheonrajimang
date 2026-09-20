from datetime import datetime, timedelta, timezone
import numpy as np
import pytest
from pydantic import ValidationError
from backend import core, daylight, live, main
from backend.tests.test_searchproof import client, current
from backend.tests.test_live import flat_terrain, point_mass


def clock(at,seconds=3600,factor=.6):
    return daylight.SolarClock(at,seconds,33.397,126.268,factor)


def test_solar_position_timezones_seasons_and_boundaries():
    assert daylight.elevation('2026-03-20T12:00:00Z',0,0)>85
    assert daylight.elevation('2026-03-20T00:00:00Z',0,0)<-85
    assert daylight.elevation('2026-06-21T12:00:00+09:00',33.397,126.268)>70
    assert daylight.elevation('2026-12-21T12:00:00+09:00',33.397,126.268)<35
    a=daylight.elevation('2024-02-29T12:00:00+09:00',33.397,126.268)
    assert a==daylight.elevation('2024-02-29T03:00:00Z',33.397,126.268)
    assert daylight.phase(0)=='DAY' and daylight.phase(-3)=='TWILIGHT' and daylight.phase(-6)=='NIGHT'
    with pytest.raises(ValueError):daylight.aware('2026-09-19T12:00:00')


def test_light_clock_integrates_dawn_and_preserves_prefix():
    short=clock('2026-09-19T05:00:00+09:00',3600)
    long=clock('2026-09-19T05:00:00+09:00',10800)
    x=np.arange(0,3601,15)
    assert np.array_equal(short.effective(x),long.effective(x))
    info=long.summary(10800)
    assert info['start_phase']=='NIGHT' and info['end_phase']=='DAY'
    assert 0<info['twilight_minutes']<60
    assert info['day_minutes']+info['twilight_minutes']+info['night_minutes']==180
    assert .6*10800<long.effective(10800)<10800
    # Crossing a changing light level within a step must integrate the full edge.
    start=np.array([4200.0,4300.0]);nominal=np.array([400.0,500.0])
    elapsed=long.travel_seconds(start,nominal)
    assert np.allclose(long.effective(start+elapsed)-long.effective(start),nominal)


def test_night_changes_distance_not_mass_or_zero_time_state():
    night=clock('2026-09-19T23:00:00+09:00')
    day=clock('2026-09-19T12:00:00+09:00')
    prior=point_mass()
    baseline,base_stats=live.project(prior,1800,42,terrain=flat_terrain())
    bright,ds=live.project(prior,1800,42,terrain=flat_terrain(),clock=day)
    dark,ns=live.project(prior,1800,42,terrain=flat_terrain(),clock=night)
    assert np.allclose(bright.grid,baseline.grid)
    assert ns['distance_m']==pytest.approx(base_stats['distance_m']*.6)
    assert ns['mean_kmh']==pytest.approx(ds['mean_kmh']*.6)
    assert not np.allclose(dark.grid,bright.grid)
    dark.check();bright.check()
    zero,zs=live.project(prior,0,42,terrain=flat_terrain(),clock=night)
    assert np.array_equal(zero.grid,prior.grid)
    assert zs['mean_kmh']>0


def test_time_controls_dont_change_radial_or_legacy_and_can_disable_effect():
    p=dict(lon=126.268,lat=33.397,hours=.5,sigma=50,seed=7)
    original=core.compute(p)
    night=core.compute({**p,'daylight_enabled':True,'missing_at':'2026-09-19T23:00:00+09:00','night_factor':.6})
    none=core.compute({**p,'daylight_enabled':True,'missing_at':'2026-09-19T23:00:00+09:00','night_factor':1})
    assert np.array_equal(original[0].grid,night[0].grid)
    assert not np.array_equal(original[1].grid,night[1].grid)
    for old,new,dark in zip(original,none,night):
        assert np.array_equal(old.grid,new.grid)
        dark.check()


def test_timestamp_validation_and_derived_hours():
    data=main.MissionInput(missing_at='2026-09-19T22:00:00+09:00',analysis_at='2026-09-20T00:30:00+09:00',hours=1,daylight_enabled=True)
    assert data.hours==2.5
    for kwargs in ({'daylight_enabled':True},
                   {'missing_at':'2026-09-19T22:00:00'},
                   {'analysis_at':'2026-09-19T22:00:00+09:00'},
                   {'missing_at':'2026-09-19T22:00:00+09:00','analysis_at':'2026-09-19T21:00:00+09:00'},
                   {'missing_at':'2026-09-19T22:00:00+09:00','analysis_at':'2026-09-20T10:00:00+09:00'},
                   {'night_factor':.1}):
        with pytest.raises(ValidationError):main.MissionInput(**kwargs)


def test_api_frozen_clock_forecast_and_legacy_preservation(client):
    old_id,old=current(client)
    data=dict(name='밤→아침 시각 검사',missing_at='2026-09-19T05:00:00+09:00',analysis_at='2026-09-19T05:30:00+09:00',daylight_enabled=True,night_factor=.6)
    response=client.post('/api/missions',json=data)
    assert response.status_code==200,response.text
    mid=response.json()['id'];base=client.get('/api/missions/'+mid).json()
    assert base['params']['hours']==.5
    assert base['daylight']['night_minutes']==30
    assert base['receipt']['parameters']['daylight_model']==daylight.MODEL
    assert 'backend/daylight.py' in base['receipt']['code_files']
    result=client.get(f'/api/missions/{mid}/forecast?version=0&seconds=10800')
    assert result.status_code==200,result.text
    f=result.json()
    assert daylight.aware(f['base_at'])==daylight.aware(data['analysis_at'])
    assert daylight.aware(f['estimated_at'])==daylight.aware(data['analysis_at'])+timedelta(hours=3)
    assert f['speed']['daylight']['start_phase']=='NIGHT'
    assert f['speed']['daylight']['end_phase']=='DAY'
    assert f['inside_mass']+f['outside_mixed']==pytest.approx(1)
    assert client.get('/api/missions/'+mid).json()['receipt']['hash']==base['receipt']['hash']
    assert client.get('/api/missions/'+old_id).json()['receipt']['hash']==old['receipt']['hash']
    assert client.get('/api/missions/'+old_id).json()['daylight']['enabled'] is False
    assert client.get(f'/api/missions/{mid}/forecast/combined.png?version=0&seconds=10800').content.startswith(b'\x89PNG')
    # Approval does not move the time origin. Future observations cannot leak
    # backward into the initial analysis snapshot during temporal replay.
    track=client.post(f'/api/missions/{mid}/demo-track').json()
    assert client.post(f'/api/missions/{mid}/tracks/{track["id"]}/apply',json={'outcome':'COMPLETED_NO_FIND','expected_version':0}).status_code==200
    latest=client.get('/api/missions/'+mid).json()
    f1=client.get(f'/api/missions/{mid}/forecast?version=1&seconds=0').json()
    assert daylight.aware(f1['base_at'])==daylight.aware(latest['receipt']['state_at'])
    assert daylight.aware(f1['base_at'])==daylight.aware(data['analysis_at'])
    assert f1['speed']['observation_bins']==0
    initial_zero=client.get(f'/api/missions/{mid}/forecast?version=0&seconds=0').json()
    assert f1['zones']==initial_zero['zones']
    assert f1['speed']['observation_timing']['uses_approval_time'] is False


def test_future_baseline_is_preview_not_live(client):
    start=datetime.now(timezone.utc)+timedelta(hours=1)
    response=client.post('/api/missions',json={'missing_at':start.isoformat(),'analysis_at':(start+timedelta(minutes=15)).isoformat(),'daylight_enabled':True})
    assert response.status_code==200
    mid=response.json()['id']
    assert client.get(f'/api/missions/{mid}/forecast').status_code==422
    assert client.get(f'/api/missions/{mid}/forecast?seconds=0').status_code==200
