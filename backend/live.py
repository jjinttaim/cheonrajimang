"""Read-only time projection from a saved posterior, not live person tracking.

Weighted mass packets preserve each starting cell's exact probability. Motion
uses fixed, uncalibrated walking assumptions. No observed traffic is available.
"""
from datetime import datetime, timedelta
import numpy as np
from . import core

MAX_SECONDS = 6 * 3600
REFRESH_SECONDS = 15
DR = np.array([-1,-1,0,1,1,1,0,-1])
DC = np.array([0,1,1,1,0,-1,-1,-1])
DIST = core.CELL * np.hypot(DR, DC)


def uniform(tokens, steps, seed, salt):
    # Counter-based draws keep path prefixes identical for different forecast times.
    x = (tokens.astype(np.uint64)*747796405 + steps.astype(np.uint64)*2891336453
         + np.uint64(seed)*277803737 + np.uint64(salt)*1597334677) & np.uint64(0xffffffff)
    x = ((x ^ (x >> 16))*2246822519) & np.uint64(0xffffffff)
    x = ((x ^ (x >> 13))*3266489917) & np.uint64(0xffffffff)
    return (x ^ (x >> 16)).astype(float) / 4294967296.0


def walking_speed(t, r, c, nr, nc, distance):
    sr, sc = np.clip(nr,0,core.N-1), np.clip(nc,0,core.N-1)
    grade = (t.dem[sr,sc]-t.dem[r,c])/distance
    # Directional slope + land cover + mapped-road proximity, never traffic data.
    v = 1.667*np.exp(-3.5*np.abs(grade+.05))*t.speed[sr,sc]*core.ASSUMPTIONS["walking_mobility"]
    return np.clip(v,.025,2.0), grade


def project(prior, seconds, seed, behavior=False, terrain=None, facility_field=None, facility_strength=0,clock=None,observations=(),capture_times=(),max_packets=None):
    if not np.isfinite(seconds) or not 0 <= seconds <= MAX_SECONDS:
        raise ValueError("Projection time must be between 0 and six hours")
    prior.check()
    if len(capture_times) and (not np.isfinite(capture_times).all() or min(capture_times)<0 or max(capture_times)>seconds or np.any(np.diff(capture_times)<=0)):
        raise ValueError('Invalid trajectory capture times')
    if max_packets is not None and (not isinstance(max_packets,int) or isinstance(max_packets,bool) or not 1<=max_packets<=65536):
        raise ValueError('Invalid path sample budget')
    t = terrain or core.terrain()
    occupied = np.flatnonzero(prior.grid)
    sampling={'method':'ALL_ORIGINAL_TOKENS','sampled':False,
              'population_paths':int(2*len(occupied)),'sample_draws':None,'draw_seed':None}
    if not len(occupied):
        stats={"mean_kmh":0.0,"distance_m":0.0,"zone_speed_mass":np.zeros(1024)}
        if len(capture_times):
            from .trajectory import Recorder
            stats['trajectory']=Recorder(capture_times,np.array([],int),np.array([],int)).finish(np.array([]),prior.outside,sampling)
        return core.Distribution(prior.grid.copy(),prior.outside),stats
    # Two weighted trajectories per occupied cell. No resampling noise at t=0.
    token = np.repeat(occupied,2)*2 + np.tile([0,1],len(occupied))
    weights = np.repeat(prior.grid.ravel()[occupied]/2,2)
    if max_packets is not None and len(token)>max_packets:
        # Stratified sample of the ORIGINAL coherent path tokens. Repeated
        # tokens are merged by weight, not assigned new random trajectories.
        total=float(weights.sum());cdf=np.cumsum(weights)/total;cdf[-1]=1.
        u=(np.arange(max_packets)+np.random.default_rng(seed).random(max_packets))/max_packets
        indices,counts=np.unique(np.searchsorted(cdf,u,side='right'),return_counts=True)
        token=token[indices];weights=total*counts/max_packets
        sampling.update(method='STRATIFIED_ORIGINAL_TOKENS',sampled=True,
                        sample_draws=max_packets,draw_seed=seed)
    r,c = np.divmod(token//2,core.N)
    recorder=None
    if len(capture_times):
        from .trajectory import Recorder
        recorder=Recorder(capture_times,r,c)
    steps = np.zeros(len(r),dtype=np.uint32)
    heading = np.floor(uniform(token,steps,seed,1)*8).astype(int)
    remaining = np.full(len(r),seconds,dtype=float)
    alive = np.ones(len(r),dtype=bool)
    tr,tc = r.copy(),c.copy()
    fraction = np.zeros(len(r))
    travelled = np.zeros(len(r))
    speed = np.zeros(len(r))
    turn_cdf = np.cumsum([.08,.14,.56,.14,.08] if behavior else [.14,.20,.32,.20,.14])
    events=sorted((e for e in observations if 0<=e.seconds<=seconds),key=lambda e:e.seconds)
    event_times=np.array([e.seconds for e in events])
    log_survival=np.zeros(len(weights))
    for event in events:
        if event.seconds==0:log_survival-=event.at(r,c)

    def observe(ids,starts,ends,nr,nc,v=None,distance=None):
        if recorder is not None:recorder.record(ids,starts,ends,r,c,nr,nc,v,distance,clock)
        if not events or not len(ids):return
        lower=np.searchsorted(event_times,float(np.min(starts)),side='right')
        upper=np.searchsorted(event_times,float(np.max(ends)),side='right')
        for event in events[lower:upper]:
            selected=(starts<event.seconds)&(event.seconds<=ends)
            chosen=ids[selected]
            if not len(chosen):continue
            c0=event.at(r[chosen],c[chosen])
            if v is None:cost=c0
            else:
                elapsed=(clock.effective(event.seconds)-clock.effective(starts[selected])) if clock is not None else event.seconds-starts[selected]
                frac=np.clip(v[selected]*elapsed/distance[selected],0,1)
                # Explicit linear within-edge intensity approximation. No future
                # observations and no reapplying old coverage at the final cell.
                cost=(1-frac)*c0+frac*event.at(nr[selected],nc[selected])
            log_survival[chosen]-=cost

    # The shortest possible edge is 30m / 2m/s. Rejections consume 30 seconds.
    for _ in range(int(seconds/15)+2):
        ids = np.flatnonzero(alive & (remaining>1e-9))
        if not len(ids): break
        terminal = t.water[r[ids],c[ids]] | (t.slope[r[ids],c[ids]]>45)
        stopped_ids=ids[terminal]
        observe(stopped_ids,seconds-remaining[stopped_ids],np.full(len(stopped_ids),seconds),r[stopped_ids],c[stopped_ids])
        alive[ids[terminal]]=False
        speed[ids[terminal]]=0
        ids=ids[~terminal]
        if not len(ids): continue
        turn=np.searchsorted(turn_cdf,uniform(token[ids],steps[ids],seed,2))-2
        d=(heading[ids]+turn)%8
        nr,nc=r[ids]+DR[d],c[ids]+DC[d]
        v,grade=walking_speed(t,r[ids],c[ids],nr,nc,DIST[d])
        accept=np.ones(len(ids),dtype=bool)
        if behavior:
            from .facilities import bias
            sr,sc=np.clip(nr,0,core.N-1),np.clip(nc,0,core.N-1)
            attraction=bias(facility_field,facility_strength,r[ids],c[ids],nr,nc)
            favor=np.exp(np.clip((t.roads[r[ids],c[ids]]-t.roads[sr,sc])/90-grade+attraction,-2,2))
            accept=uniform(token[ids],steps[ids],seed,3)<np.minimum(1,favor)
        rejected=ids[~accept]
        starts=seconds-remaining[rejected]
        observe(rejected,starts,starts+np.minimum(remaining[rejected],30),r[rejected],c[rejected])
        remaining[rejected]-=np.minimum(remaining[rejected],30)
        speed[rejected]=0
        chosen=ids[accept]
        tau=DIST[d[accept]]/v[accept]
        elapsed=seconds-remaining[chosen]
        if clock is not None:
            tau=clock.travel_seconds(elapsed,tau)
        used=np.minimum(tau,remaining[chosen])
        observe(chosen,elapsed,elapsed+used,nr[accept],nc[accept],v[accept],DIST[d[accept]])
        speed[chosen]=v[accept]*(clock.factor(seconds) if clock is not None else 1)
        moved=v[accept]*(clock.effective(elapsed+used)-clock.effective(elapsed) if clock is not None else used)
        travelled[chosen]+=moved
        completed=tau<=remaining[chosen]
        moving=chosen[completed]
        r[moving],c[moving]=nr[accept][completed],nc[accept][completed]
        partial=chosen[~completed]
        tr[partial],tc[partial]=nr[accept][~completed],nc[accept][~completed]
        fraction[partial]=np.clip(moved[~completed]/DIST[d[accept][~completed]],0,1)
        remaining[chosen]-=used
        heading[ids]=d
        steps[ids]+=1
        outside=(r<0)|(r>=core.N)|(c<0)|(c>=core.N)
        alive[outside]=False
        speed[outside]=0
    if np.any(alive & (remaining>1e-7)):
        raise ValueError("Projection did not consume its time budget")
    evidence=1.
    if events:
        weights=weights*np.exp(log_survival)
        evidence=float(weights.sum()+prior.outside)
        if evidence<=0 or not np.isfinite(evidence):raise ValueError('관찰과 모든 가설이 양립하지 않습니다.')
        weights/=evidence
    grid=np.zeros_like(prior.grid)
    outside_mass=prior.outside/evidence
    for rows,cols,mass in ((r,c,weights*(1-fraction)),(tr,tc,weights*fraction)):
        inside=(rows>=0)&(rows<core.N)&(cols>=0)&(cols<core.N)
        np.add.at(grid,(rows[inside],cols[inside]),mass[inside])
        outside_mass+=float(mass[~inside].sum())
    if seconds==0:
        ids=np.arange(len(r))
        v,_=walking_speed(t,r,c,r+DR[heading],c+DC[heading],DIST[heading])
        speed=v*(clock.factor(0) if clock is not None else 1)
    inside=(r>=0)&(r<core.N)&(c>=0)&(c<core.N)
    terminal=np.zeros(len(r),dtype=bool)
    terminal[inside]=t.water[r[inside],c[inside]]|(t.slope[r[inside],c[inside]]>45)
    speed[terminal]=0
    zone_speed_mass=np.zeros(1024)
    for rows,cols,mass in ((r,c,weights*(1-fraction)),(tr,tc,weights*fraction)):
        visible=(rows>=0)&(rows<core.N)&(cols>=0)&(cols<core.N)
        zone_ids=(rows[visible]//core.ZONE)*32+cols[visible]//core.ZONE
        np.add.at(zone_speed_mass,zone_ids,mass[visible]*speed[visible]*3.6)
    distribution=core.Distribution(grid,float(outside_mass)).check()
    stats={
        "mean_kmh":float(np.dot(weights,speed)*3.6/max(weights.sum(),1e-12)),
        "distance_m":float(np.dot(weights,travelled)/max(weights.sum(),1e-12)),
        "zone_speed_mass":zone_speed_mass,
        "observation_bins":len(events),"no_find_likelihood":evidence,
        "effective_packets":float(weights.sum()**2/max(np.square(weights).sum(),1e-300)),
    }
    if recorder is not None:stats['trajectory']=recorder.finish(weights,prior.outside/evidence,sampling)
    return distribution,stats


def forecast(priors, coverage, seconds, params, start_at=None,observations=()):
    from .facilities import resolve
    from . import daylight
    t=core.terrain_for(params)
    field=resolve(params,t)
    clock=daylight.clock_for(params,start_at or params.get("analysis_at"),seconds)
    # Apply past negative evidence ONCE, then move its posterior forward.
    # Passing coverage again after movement would incorrectly clear visited cells.
    baseline=[core.update(d,coverage[1]) for d in priors]
    results=[project(d,seconds,params["seed"]+i*101,behavior=(i==2),terrain=t,
                     facility_field=field if i==2 else None,facility_strength=params.get("facility_strength",0),clock=clock,observations=observations)
             for i,d in enumerate(baseline)]
    distributions=[d for d,_ in results]
    zero=np.zeros_like(coverage)
    summary,_,_=core.summarize(distributions,zero,t)
    stats=[s for _,s in results]
    zone_speeds=sum(w*s["zone_speed_mass"] for w,s in zip(core.WEIGHTS,stats))
    for feature in summary["zones"]["features"]:
        p=feature["properties"]
        p["speed_kmh"]=float(zone_speeds[p["id"]]/max(p["poa"],1e-12))
    return distributions,baseline,summary,{
        "mean_kmh":float(sum(w*s["mean_kmh"] for w,s in zip(core.WEIGHTS,stats))),
        "mean_distance_m":float(sum(w*s["distance_m"] for w,s in zip(core.WEIGHTS,stats))),
        "scenario_kmh":[s["mean_kmh"] for s in stats],
        "observation_bins":len(observations),
        "effective_packets":[s.get('effective_packets',0) for s in stats],
        "mode":"WALKING_ASSUMPTION",
        "traffic_connected":False,
        "traffic_status":"NOT_CONNECTED",
        "daylight":clock.summary(seconds) if clock is not None else {"enabled":False},
        "note":"위치, 경사, 토지피복, 도로에 따른 도보 추정. 실제 속도 관측이나 교통 정체 반영이 아닙니다.",
    }
