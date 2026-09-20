"""Model status, read-only predictions, and versioned human-approved proposals."""
from datetime import datetime,timezone,timedelta
from typing import Literal
import json
import time
import uuid
import numpy as np
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field,model_validator
from . import core,ml_models,planner

router=APIRouter()


def initialize(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS ai_plans(
        id TEXT PRIMARY KEY,mission TEXT NOT NULL REFERENCES missions(id),
        base_version INTEGER NOT NULL,base_hash TEXT NOT NULL,model_id TEXT NOT NULL,
        input_hash TEXT NOT NULL,request TEXT NOT NULL,result TEXT NOT NULL,
        created_at TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,UNIQUE(mission,idempotency_key));
        CREATE TABLE IF NOT EXISTS ai_plan_maps(
        plan TEXT PRIMARY KEY REFERENCES ai_plans(id),image BLOB NOT NULL,sha256 TEXT NOT NULL);''')


class TeamInput(BaseModel):
    name:str=Field(min_length=1,max_length=30)
    lon:float=Field(ge=126.02,le=127.08,allow_inf_nan=False)
    lat:float=Field(ge=33.0,le=33.67,allow_inf_nan=False)
    search_type:Literal['hasty','sweep','paired']='sweep'
    team_size:int=Field(default=1,ge=1,le=core.MAX_TEAM_SIZE)
    spacing_m:float=Field(default=15.,ge=core.MIN_SPACING_M,le=core.MAX_SPACING_M,allow_inf_nan=False)


class PlanInput(BaseModel):
    base_version:int=Field(ge=0)
    model_id:str=Field(pattern=r'^[a-f0-9]{64}$')
    teams:list[TeamInput]=Field(min_length=1,max_length=4)
    minutes:int=Field(default=30,ge=5,le=120)
    projection_seconds:int=Field(default=0,ge=0,le=21600)
    temporal_projection:bool=False
    moving_target:bool=False
    distribution_mode:Literal['TIME_SCENARIOS','ENDPOINT_REFERENCE']='TIME_SCENARIOS'
    endpoint_model:Literal['lognorm','yosar_interval_lognorm']='lognorm'
    excluded_zones:list[int]=Field(default_factory=list,max_length=1024)
    idempotency_key:str=Field(min_length=8,max_length=100)

    @model_validator(mode='after')
    def valid(self):
        if len({t.name for t in self.teams})!=len(self.teams):raise ValueError('팀 이름이 중복됩니다.')
        if any(not 0<=z<1024 for z in self.excluded_zones):raise ValueError('잘못된 제외 구역')
        if self.distribution_mode=='ENDPOINT_REFERENCE' and (self.projection_seconds or self.temporal_projection):
            raise ValueError('발견점 참고 모델에는 시간별 정답이 없습니다. 기준 지도에서 사용하세요.')
        if self.distribution_mode!='ENDPOINT_REFERENCE' and self.endpoint_model!='lognorm':
            raise ValueError('추가 발견점 모델은 시간 시나리오와 혼합할 수 없습니다.')
        if self.moving_target and (self.distribution_mode!='TIME_SCENARIOS' or not (self.temporal_projection or self.projection_seconds)):
            raise ValueError('수색 중 이동 계산은 시간 미리보기와 실시간 추정에서만 가능합니다.')
        return self


class ApproveInput(BaseModel):
    expected_version:int=Field(ge=0)


def same_request(row,requested,digest):
    if row['input_hash']==digest:return True
    # Old saved requests predate the optional selector. Preserve their receipts
    # and accept only the same request with the original default made explicit.
    old=json.loads(row['request'])
    old.setdefault('endpoint_model','lognorm')
    old.setdefault('moving_target',False)
    for team in old.get('teams',[]):
        team.setdefault('team_size',1);team.setdefault('spacing_m',15.)
    return old==requested


@router.get('/api/ai/status')
def status():return ml_models.public_status()


@router.get('/api/ai/evaluation')
def evaluation():
    try:
        b=ml_models.load_bundle()
        # Full per-row predictions remain local artifacts, not sent to the UI.
        return {'bundle_id':b['bundle_id'],'endpoint':b['evaluation']['endpoint']['summary'],
                'yosar_endpoint':b['evaluation'].get('yosar_endpoint',{}).get('summary'),
                'speed':b['evaluation']['searcher_speed']['summary'],
                'tracks':b['evaluation']['searcher_speed']['per_track'],
                'inventory':b['data_inventory'],'exercise':b['exercise_validation'],
                'limitations':b['manifest']['limitations']}
    except ValueError as e:raise HTTPException(503,str(e))


@router.post('/api/missions/{mid}/plans')
def create_plan(mid:str,data:PlanInput):
    from . import main,daylight
    requested=data.model_dump(mode='json');digest=main.sha(main.canonical(requested).encode())
    from . import roles
    with main.connect() as c:
        m,v=main.version_row(c,mid,data.base_version)
        roles.require_active(c,mid)
        if m['current_version']!=data.base_version:raise HTTPException(409,'최신 지도 버전에서 다시 계산하세요.')
        existing=c.execute('SELECT * FROM ai_plans WHERE mission=? AND idempotency_key=?',(mid,data.idempotency_key)).fetchone()
        if existing:
            if not same_request(existing,requested,digest):raise HTTPException(409,'같은 요청 키에 다른 입력을 사용할 수 없습니다.')
            return {**json.loads(existing['result']),'status':existing['status']}
    try:bundle=ml_models.load_bundle(data.model_id)
    except ValueError as e:raise HTTPException(503,str(e))
    start=time.perf_counter();priors,coverage=core.unpack(v['state']);params=json.loads(m['params'])
    use_projection=data.temporal_projection or bool(data.projection_seconds)
    base_at=(params.get('analysis_at') if use_projection else None) or json.loads(v['receipt']).get('state_at',v['created_at'])
    estimated_at=datetime.fromisoformat(base_at)+timedelta(seconds=data.projection_seconds)
    projection_meta=None;dynamic=None
    try:
        if data.moving_target:
            from .trajectory import Scorer
            paths,projection_meta=main.cached_plan_paths(str(main.DB),mid,v['number'],v['hash'],data.projection_seconds,data.minutes)
            dynamic=Scorer(paths,planner.WIDTHS)
            distributions=[p.distribution(0) for p in paths]
        elif data.distribution_mode=='ENDPOINT_REFERENCE':
            q=planner.endpoint_reference(data.model_id,params['lon'],params['lat'],params['sigma'],params['seed'],data.endpoint_model,float(params.get('row_share',0.)),core.window_of(params))
            distributions=[core.update(q,coverage[1])]
        elif use_projection:
            predictions,_,_,speed_info,_,_=main.cached_forecast(str(main.DB),mid,v['number'],v['hash'],data.projection_seconds)
            distributions=predictions
            projection_meta=speed_info.get('observation_timing')
        else:distributions=[core.update(d,coverage[1]) for d in priors]
        # Worst light multiplier over planning interval is an uncalibrated time reserve.
        clock=daylight.clock_for(params,estimated_at.isoformat(),data.minutes*60)
        night=min(float(clock.factor(s)) for s in range(0,data.minutes*60+1,60)) if clock is not None else 1.
        result=planner.plan(distributions,bundle,[t.model_dump() for t in data.teams],data.minutes,night,data.excluded_zones,dynamic=dynamic,terrain=core.terrain_for(params))
    except ValueError as e:raise HTTPException(422,str(e))
    stamp=datetime.now(timezone.utc);pid=uuid.uuid4().hex;expires=(stamp+timedelta(minutes=2)).isoformat()
    result.update({'id':pid,'mission':mid,'base_version':v['number'],'base_hash':v['hash'],
                   'input_hash':digest,'created_at':stamp.isoformat(),'expires_at':expires,
                   'distribution_mode':data.distribution_mode,'projection_seconds':data.projection_seconds,
                   'endpoint_model':data.endpoint_model if data.distribution_mode=='ENDPOINT_REFERENCE' else None,
                   'temporal_projection':use_projection,
                   'moving_target':data.moving_target,
                   'observation_timing':projection_meta,
                   'estimated_at':estimated_at.isoformat(),'compute_seconds':time.perf_counter()-start,
                   'code_hash':main.sha((main.ROOT/'backend/planner.py').read_bytes())})
    result['code_files']={name:main.sha((main.ROOT/'backend'/name).read_bytes()) for name in
                          ('main.py','planner.py','ai_api.py','core.py','live.py','temporal.py','trajectory.py','daylight.py','facilities.py','ml_models.py')}
    # Freeze the exact input distribution shown with this proposal. Reopening a
    # plan must not silently put its routes over a newer/different model's map.
    display_distributions=distributions if len(distributions)==3 else distributions*3
    empty=np.zeros_like(coverage)
    display_summary,_,_=core.summarize(display_distributions,empty,core.terrain_for(params))
    png=core.png_for(display_distributions,empty,'combined','prior')
    result.update({'display_summary':display_summary,'display_map_sha256':main.sha(png),
                   'display_map_url':f'/api/missions/{mid}/plans/{pid}/map.png'})
    if data.moving_target:
        result['warning']=f"수색 방문 시각과 이동 가설을 맞춘 {result['target_motion']['time_step_seconds']:g}초 근사입니다. 지도는 계획 시작 분포이며 0점은 부재의 증거가 아닙니다. "+result['warning']
    if data.distribution_mode=='ENDPOINT_REFERENCE':
        result['endpoint_model_info']=next(o for o in ml_models.endpoint_options(bundle) if o['key']==data.endpoint_model)
        result['warning']='학습 발견점의 거리 참고 모드이며 현재 시점 존재확률이 아닙니다. '+result['warning']
        if data.endpoint_model=='yosar_interval_lognorm':
            result['warning']='YOSAR 비상업적 연구 참고(CC BY-NC-SA 4.0). 좌표 오차의 미터 단위는 논문 기반 추론입니다. '+result['warning']
    with main.LOCK,main.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        m2,v2=main.version_row(c,mid)
        if v2['hash']!=v['hash']:raise HTTPException(409,'계산 중 수색 결과가 변경됐습니다. 다시 계산하세요.')
        existing=c.execute('SELECT * FROM ai_plans WHERE mission=? AND idempotency_key=?',(mid,data.idempotency_key)).fetchone()
        if existing:
            if not same_request(existing,requested,digest):raise HTTPException(409,'같은 요청 키에 다른 입력을 사용할 수 없습니다.')
            return {**json.loads(existing['result']),'status':existing['status']}
        c.execute('INSERT INTO ai_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (pid,mid,v['number'],v['hash'],data.model_id,digest,main.canonical(requested),main.canonical(result),stamp.isoformat(),expires,'PROPOSED',data.idempotency_key))
        c.execute('INSERT INTO ai_plan_maps VALUES(?,?,?)',(pid,png,main.sha(png)))
    return result


@router.get('/api/missions/{mid}/plans')
def list_plans(mid:str):
    from . import main
    with main.connect() as c:
        main.get_mission(c,mid)
        rows=c.execute('''SELECT result,status FROM ai_plans WHERE mission=? AND
            (status='APPROVED' OR id IN (SELECT id FROM ai_plans WHERE mission=? ORDER BY created_at DESC LIMIT 20))
            ORDER BY CASE WHEN status='APPROVED' THEN 0 ELSE 1 END,created_at DESC''',(mid,mid))
        keys=('id','base_version','distribution_mode','created_at','expires_at','estimated_at','model_id','endpoint_model','endpoint_model_info')
        return [{**{k:v for k,v in json.loads(r['result']).items() if k in keys},'status':r['status']} for r in rows]


@router.get('/api/missions/{mid}/plans/{pid}')
def get_plan(mid:str,pid:str):
    from . import main
    with main.connect() as c:
        row=c.execute('SELECT * FROM ai_plans WHERE mission=? AND id=?',(mid,pid)).fetchone()
        if not row:raise HTTPException(404,'계획을 찾을 수 없습니다.')
        return {**json.loads(row['result']),'status':row['status'],'inputs':json.loads(row['request'])}


@router.get('/api/missions/{mid}/plans/{pid}/map.png')
def plan_map(mid:str,pid:str):
    from . import main
    with main.connect() as c:
        row=c.execute('''SELECT image,sha256 FROM ai_plan_maps m JOIN ai_plans p ON p.id=m.plan
                         WHERE p.mission=? AND p.id=?''',(mid,pid)).fetchone()
        if not row:raise HTTPException(404,'이 이전 계획에는 저장된 분포 지도가 없습니다.')
        if main.sha(row['image'])!=row['sha256']:raise HTTPException(503,'저장 지도 무결성 확인 실패')
        return Response(row['image'],media_type='image/png')


@router.post('/api/missions/{mid}/plans/{pid}/approve')
def approve(mid:str,pid:str,data:ApproveInput):
    from . import main
    from . import roles
    with main.LOCK,main.connect() as c:
        c.execute('BEGIN IMMEDIATE');_,v=main.version_row(c,mid);roles.require_active(c,mid)
        row=c.execute('SELECT * FROM ai_plans WHERE mission=? AND id=?',(mid,pid)).fetchone()
        if not row:raise HTTPException(404,'추천을 찾을 수 없습니다.')
        if data.expected_version!=v['number'] or row['base_hash']!=v['hash']:
            raise HTTPException(409,'수색 결과가 바뀌었습니다. 새 추천을 받아 주세요.')
        if row['status']=='APPROVED':return {'ok':True,'status':'APPROVED','id':pid}
        if row['status']!='PROPOSED':raise HTTPException(409,'다른 계획으로 대체된 기록입니다. 새로 계산하세요.')
        if datetime.now(timezone.utc)>datetime.fromisoformat(row['expires_at']):
            raise HTTPException(409,'추천 유효시간이 지났습니다. 다시 계산하세요.')
        try:ml_models.load_bundle(row['model_id'])
        except ValueError as e:raise HTTPException(503,str(e))
        result=json.loads(row['result'])
        if not result['assignments']:raise HTTPException(422,'배정할 수 있는 추천 경로가 없습니다.')
        # Prior approved plans remain historical; explicit approval is the only mutation.
        c.execute("UPDATE ai_plans SET status='SUPERSEDED' WHERE mission=? AND status='APPROVED'",(mid,))
        c.execute("UPDATE ai_plans SET status='APPROVED' WHERE id=?",(pid,))
        note='모의 AI 계획 확정 '+pid+' / '+', '.join(a['team']+': '+a['zone_label'] for a in result['assignments'])
        c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,mid,'AI_PLAN',note,'조정자',main.now(),main.now()))
    return {'ok':True,'status':'APPROVED','id':pid}
