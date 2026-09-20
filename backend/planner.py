"""Constrained, model-assisted search proposals. Never an observed search.

One group is represented by one searcher path. Detection widths remain explicit
assumptions. A frozen distribution or coherent moving paths are used within a
plan; neither is a trained target motion model.
"""
from functools import lru_cache
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from . import core,ml_models

ALGORITHM='cell-route-greedy-v2-band'
WIDTHS=np.array([8.,15.,22.])


@lru_cache(maxsize=8)
def movement_graph(bundle_id,search_type,window=core.LEGACY_WINDOW):
    bundle=ml_models.load_bundle(bundle_id)
    t=core.terrain(*window);n=core.N
    ids=np.arange(n*n).reshape(n,n)
    # Four-neighbor paths cannot cut diagonally across hazard corners.
    a=np.concatenate([ids[:,:-1].ravel(),ids[:-1,:].ravel()])
    b=np.concatenate([ids[:,1:].ravel(),ids[1:,:].ravel()])
    grade=np.abs(t.dem.ravel()[b]-t.dem.ravel()[a])/core.CELL
    ok=~t.hazard.ravel()[a]&~t.hazard.ravel()[b]&np.isfinite(grade)&(grade<=1)
    a,b,grade=a[ok],b[ok],grade[ok]
    # Fixed 0.001 grade table; quantization is an engineering approximation.
    table=ml_models.speed(bundle,np.arange(1001)/1000,search_type)
    v=table[np.rint(grade*1000).astype(int)]
    lc=np.minimum(1.,(t.speed.ravel()[a]+t.speed.ravel()[b])/2)
    v=np.clip(v,.05,1.5)*lc
    seconds=core.CELL/v
    graph=coo_matrix((np.r_[seconds,seconds],(np.r_[a,b],np.r_[b,a])),shape=(n*n,n*n)).tocsr()
    return graph,table


def backtrack(predecessors,start,end):
    nodes=[int(end)]
    for _ in range(core.N*core.N):
        if nodes[-1]==start:return nodes[::-1]
        nxt=int(predecessors[nodes[-1]])
        if nxt<0: return []
        nodes.append(nxt)
    raise ValueError('경로 재구성 제한 초과')


def serpentine(zone,band_rows=1):
    """Lawn-mower passes over a 16x16 zone as a 4-connected walk.

    A searcher line covering `band_rows` rows walks the middle row of each band
    and sweeps the whole band. Between passes the walk steps row by row down the
    turning column (transition cells sweep only themselves). The returned
    `covered` array lists, per walked cell, the cells it sweeps (-1 = none).
    Passes never leave the zone. band_rows=1 is the original single-observer
    pattern, where transitions are the ordinary adjacent-row step.
    """
    zr,zc=divmod(zone,32)
    k=max(1,min(int(band_rows),core.ZONE))
    block=np.arange(core.N*core.N).reshape(core.N,core.N)[zr*16:(zr+1)*16,zc*16:(zc+1)*16]
    walk=[];covered=[];previous_mid=None
    for p,start in enumerate(range(0,core.ZONE,k)):
        rows=list(range(start,min(start+k,core.ZONE)))
        mid=rows[(len(rows)-1)//2]
        cols=list(range(core.ZONE)) if p%2==0 else list(range(core.ZONE-1,-1,-1))
        if previous_mid is not None:
            for r in range(previous_mid+1,mid):
                walk.append(int(block[r,cols[0]]));covered.append([int(block[r,cols[0]])]+[-1]*(k-1))
        for col in cols:
            walk.append(int(block[mid,col]))
            covered.append([int(block[r,col]) for r in rows]+[-1]*(k-len(rows)))
        previous_mid=mid
    walk=np.array(walk);covered=np.array(covered,dtype=np.int64)
    return ((walk,covered),(walk[::-1],covered[::-1]))


def geometry(nodes,kind,team,t=None):
    r,c=np.divmod(np.asarray(nodes),core.N)
    lon,lat=(t or core.terrain()).lonlat(r+.5,c+.5)
    return {'type':'Feature','properties':{'kind':kind,'team':team},
            'geometry':{'type':'LineString','coordinates':np.column_stack([lon,lat]).tolist()}}


@lru_cache(maxsize=16)
def endpoint_reference(bundle_id,lon,lat,sigma,seed,endpoint_model='lognorm',row=0.,window=core.LEGACY_WINDOW):
    bundle=ml_models.load_bundle(bundle_id)
    if endpoint_model not in {o['key'] for o in ml_models.endpoint_options(bundle)}:
        raise ValueError('이 모델 묶음에는 선택한 발견점 모델이 없습니다.')
    s,loc,scale=bundle['endpoint']['models'][endpoint_model]['params']
    if not np.isfinite([s,loc,scale]).all() or s<=0 or loc!=0 or scale<=0:
        raise ValueError('잘못된 발견점 모델 파라미터')
    rng=np.random.default_rng(seed);n=160000
    distance=loc+rng.lognormal(np.log(scale),s,n)
    angle=rng.uniform(0,2*np.pi,n)
    r,c=core.terrain(*window).rc(lon,lat)
    rows=r+(np.sin(angle)*distance+rng.normal(0,sigma,n))/core.CELL
    cols=c+(np.cos(angle)*distance+rng.normal(0,sigma,n))/core.CELL
    return core.with_row(core.histogram(rows,cols,n),row)


def plan(distributions,bundle,teams,minutes,night_factor=1.,excluded_zones=(),dynamic=None,terrain=None):
    if not distributions:raise ValueError('위치 분포가 필요합니다.')
    for d in distributions:d.check()
    if not 5<=minutes<=120:raise ValueError('계획 시간은 5~120분입니다.')
    if not 1<=len(teams)<=4:raise ValueError('수색팀은 1~4팀입니다.')
    if not .3<=night_factor<=1:raise ValueError('야간 시간 가정을 확인하세요.')
    t=terrain or core.terrain();mass=np.stack([d.grid.ravel() for d in distributions])
    covered=np.zeros(core.N*core.N)
    hazard_share=core.zone_sum(t.hazard.astype(float))/256
    zone_mass=np.stack([core.zone_sum(d.grid) for d in distributions])
    eligible=hazard_share<.02
    eligible[list(excluded_zones)]=False
    initial=dynamic.candidate_priority() if dynamic is not None else core.consensus(zone_mass)
    initial[~eligible]=0
    assignments=[];routes=[];notes=[];considered=set()
    for team in teams:
        band=core.team_band(team.get('team_size',1),team.get('spacing_m',15.))
        k=band['band_rows'];scale=band['effort_multiplier']/k
        r,c=t.rc(team['lon'],team['lat']);r,c=int(np.floor(r)),int(np.floor(c))
        if not (0<=r<core.N and 0<=c<core.N) or t.hazard[r,c]:
            raise ValueError(f"{team['name']}: 출발 위치가 분석 영역 밖이거나 일반팀 접근 제한 셀입니다.")
        source=r*core.N+c
        graph,_=movement_graph(bundle['bundle_id'],team['search_type'],(t.row0,t.col0))
        dist,pred=dijkstra(graph,directed=True,indices=source,return_predecessors=True)
        dist=dist/night_factor
        # Exact start -> center is inside the selected 30m cell, never snapped across hazards.
        rr,cc=t.rc(team['lon'],team['lat'])
        local_speed=float(ml_models.speed(bundle,[np.tan(np.radians(t.slope[r,c]))],team['search_type'])[0])
        local_speed=np.clip(local_speed,.05,1.5)*min(1.,t.speed[r,c])
        centering=core.CELL*np.hypot(rr-(r+.5),cc-(c+.5))/local_speed/night_factor
        budget=minutes*60-2*centering
        # Candidate cap is explicit and returned; not a global optimum claim. Only
        # zones this team can reach and leave within its budget are considered.
        zone_reach=np.where(np.isfinite(dist),dist,np.inf).reshape(32,core.ZONE,32,core.ZONE).min(axis=(1,3)).ravel()
        candidates=[int(z) for z in np.argsort(-initial,kind='stable') if initial[z]>0 and 2*zone_reach[z]<budget][:32]
        considered.update(candidates)
        best=None
        for zone in candidates:
            for walk,band_cells in serpentine(zone,k):
                entry=int(walk[0]);travel=dist[entry]
                if not np.isfinite(travel) or travel>=budget:continue
                prefix=[entry];spent=travel;arrivals=[travel+centering]
                for a,b in zip(walk,walk[1:]):
                    if t.hazard.ravel()[b]:break
                    edge=float(graph[int(a),int(b)])/night_factor
                    if edge<=0 or not np.isfinite(dist[b]) or spent+edge+dist[b]>budget:break
                    prefix.append(int(b));spent+=edge;arrivals.append(spent+centering)
                if len(prefix)<2:continue
                cells=np.array(prefix)
                # Swept cells: the walked cell's band column, minus hazard/invalid cells.
                swept=band_cells[:len(cells)].copy()
                swept[(swept>=0)&t.hazard.ravel()[np.maximum(swept,0)]]=-1
                # Effort per swept cell per walked step: n sweep widths over k rows.
                step_effort=np.full(len(cells),scale*core.CELL/(core.CELL**2));step_effort[[0,-1]]/=2
                extra=None
                if dynamic is not None:gain,extra=dynamic.score(cells,arrivals,band=swept,scale=scale)
                else:
                    valid=swept>=0
                    flat=swept[valid];eff=np.repeat(step_effort,k).reshape(len(cells),k)[valid]
                    inc=eff[None,:]*WIDTHS[:,None]
                    gain=np.sum(mass[:,None,flat]*np.exp(-covered[flat][None,None,:]*WIDTHS[None,:,None])
                                *(-np.expm1(-inc))[None,:,:],axis=2)
                total=spent+dist[cells[-1]]+2*centering
                robust=float(np.min(gain[:,1]))
                if dynamic is not None and robust<=0:continue
                value=robust/max(total,1.)
                if best is None or value>best['value']:
                    best={'value':value,'zone':zone,'cells':cells,'swept':swept,'step_effort':step_effort,'gain':gain,
                          'travel':travel+centering,'search':spent-travel,'return':dist[cells[-1]]+centering,
                          'total':total,'source':source,'entry':entry,'pred':pred,'arrivals':arrivals,'extra':extra}
        if best is None:
            notes.append(team['name']+(': 시간, 접근, 가설 공통 탐지효과 조건을 만족하는 경로를 찾지 못했습니다. 표집 점수 0은 부재의 증거가 아닙니다.' if dynamic is not None else ': 시간과 접근 조건을 만족하는 경로가 없습니다.'));continue
        cells=best['cells'];swept=best['swept'];valid=swept>=0
        np.add.at(covered,swept[valid],np.repeat(best['step_effort'],k).reshape(len(cells),k)[valid])
        if dynamic is not None:dynamic.accept(best['extra'])
        outbound=backtrack(best['pred'],source,best['entry'])
        inbound=backtrack(best['pred'],source,int(cells[-1]))[::-1]
        for path,kind in [(outbound,'TRANSIT'),(cells,'SEARCH'),(inbound,'RETURN')]:
            if len(path)>1:routes.append(geometry(path,kind,team['name'],t))
        zr,zc=divmod(best['zone'],32)
        assignments.append({'team':team['name'],'zone_id':best['zone'],'zone_label':f'{chr(65+zr//26)}{zr%26+1:02d}-{zc+1:02d}',
                            'search_type':team['search_type'],'search_cells':cells.tolist(),
                            'swept_cells':sorted(int(x) for x in np.unique(swept[valid])),
                            'team_size':band['team_size'],'spacing_m':band['spacing_m'],'band_rows':k,'band_width_m':band['band_width_m'],
                            'search_arrival_seconds':best['arrivals'],
                            'total_minutes':best['total']/60,'travel_minutes':best['travel']/60,
                            'search_minutes':best['search']/60,'return_minutes':best['return']/60,
                            'search_distance_m':(len(cells)-1)*core.CELL,
                            'swept_area_m2':int(np.count_nonzero(valid))*core.CELL**2,
                            'assumed_incremental_detection_range':[float(best['gain'].min()),float(best['gain'].max())],
                            'score':best['value'],'access':'COORDINATOR_REVIEW_REQUIRED',
                            'reason':('학습된 수색대 속도로 방문 시각을 계산하고, 이동 가설 경로와 같은 시각의 추가 탐지효과를 비교했습니다.' if dynamic is not None else '학습된 수색대 속도로 왕복 시간을 계산하고, 셀별 추가 탐지효과/시간을 비교했습니다.')})
    area=float(np.count_nonzero(covered)*core.CELL**2)
    target_motion=dynamic.metadata() if dynamic is not None else None
    if target_motion and target_motion['any_sampled']:
        notes.append('메모리와 경로 수 한도로 일부 가중 경로를 표집했습니다. 표집에 따라 추천 구역이 달라질 수 있으며 추천 순위의 수치 안정성은 보장하지 않습니다.')
    if target_motion and target_motion['low_effective_support']:
        notes.append('일부 시나리오의 유효 경로 수가 256 미만입니다. 표집 점수가 불안정할 수 있습니다. 256은 검증된 성능 기준이 아닌 구현 경고값입니다.')
    return {'algorithm':ALGORITHM+('-timed-paths' if dynamic is not None else ''),'assignments':assignments,'routes':{'type':'FeatureCollection','features':routes},
            'planned_cell_area_m2':area,'candidate_count':len(considered),'model_id':bundle['bundle_id'],
            'speed_engine':bundle['searcher_speed']['recommended_engine'],
            'outside_mass':[d.outside for d in distributions],'status':'PROPOSED','notes':notes,
            'target_motion':target_motion,
            'assumptions':{'widths_m':WIDTHS.tolist(),'within_plan_target':'COHERENT_TIMED_PATHS' if dynamic is not None else 'FROZEN_AT_PLAN_START',
                           'one_observer_path_per_team':True,'team_band':core.ASSUMPTIONS['team_band'],'night_factor':night_factor,
                           'landcover_factors':'UNCALIBRATED','speed_clip_mps':[.05,1.5],
                           'grade_quantization':.001,'max_candidates':32,'candidate_rule':'GEOMETRIC_MEAN_CONSENSUS_REACHABLE','return_to_start_in_budget':True},
            'warning':'훈련용 제안입니다. 시간별 실종자 행동, POD, 현장 도보 통행 가능성은 검증 전이며 실제 이동은 현장 책임자가 판단해야 합니다.'}
