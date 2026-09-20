"""Local, synthetic-data coordinator prototype. Run on loopback only."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
import hashlib
import html
import io
import json
import os
import sqlite3
import threading
import time
import uuid
import zipfile
import csv
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Query, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from typing import Literal
from pydantic import BaseModel, Field, AwareDatetime, model_validator
from . import core, live, daylight, roles

ROOT=Path(__file__).resolve().parents[1]
DB=Path(os.environ.get("SEARCHPROOF_DB",ROOT/"data/runtime/searchproof.sqlite3"))
LOCK=threading.RLock()

def now(): return datetime.now(timezone.utc).isoformat()
def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def sha(value): return hashlib.sha256(value).hexdigest()

def connect():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def initialize():
    DB.parent.mkdir(parents=True,exist_ok=True)
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,name TEXT NOT NULL,params TEXT NOT NULL,created_at TEXT NOT NULL,current_version INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS versions(mission TEXT NOT NULL REFERENCES missions(id),number INTEGER NOT NULL,parent_hash TEXT NOT NULL,hash TEXT NOT NULL,event TEXT NOT NULL,created_at TEXT NOT NULL,state BLOB NOT NULL,summary TEXT NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(mission,number));
        CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY,mission TEXT NOT NULL REFERENCES missions(id),name TEXT NOT NULL,status TEXT NOT NULL,sha256 TEXT NOT NULL,raw BLOB NOT NULL,coverage BLOB NOT NULL,summary TEXT NOT NULL,created_at TEXT NOT NULL,applied_version INTEGER,UNIQUE(mission,sha256));
        CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY,mission TEXT NOT NULL REFERENCES missions(id),kind TEXT NOT NULL,note TEXT NOT NULL,source TEXT NOT NULL,observed_at TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions(mission TEXT NOT NULL REFERENCES missions(id),zone INTEGER NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(mission,zone));
        """)
        from . import ai_api
        ai_api.initialize(c)
        roles.initialize(c)

class MissionInput(BaseModel):
    name:str=Field(default="한림읍 모의 수색",min_length=1,max_length=80)
    # Coarse island bounds; the precise check is core.Master.window_origin (the point must be on the master grid).
    lon:float=Field(default=126.268,ge=126.02,le=127.08,allow_inf_nan=False)
    lat:float=Field(default=33.397,ge=33.0,le=33.67,allow_inf_nan=False)
    hours:float=Field(default=3,ge=.25,le=8,allow_inf_nan=False)
    sigma:float=Field(default=50,ge=5,le=500,allow_inf_nan=False)
    seed:int=Field(default=42,ge=0,le=2**31-1)
    facility_influence:bool=False
    facility_strength:float=Field(default=.6,ge=0,le=1,allow_inf_nan=False)
    missing_at:AwareDatetime|None=None
    analysis_at:AwareDatetime|None=None
    daylight_enabled:bool=False
    night_factor:float=Field(default=.6,ge=.3,le=1,allow_inf_nan=False)
    # Scenario A source: fitted find-distance model (default) or the √time assumption.
    # Minimal description shown to volunteers only (training data; never real personal data).
    subject_brief:str=Field(default="",max_length=200)
    distance_model:Literal["learned_lognormal","assumed_sqrt_time"]="learned_lognormal"
    # Rest-of-world share reserved outside every mapped hypothesis (team assumption).
    row_share:float=Field(default=core.ASSUMPTIONS["row_share_default"],ge=0,le=.5,allow_inf_nan=False)

    @model_validator(mode="after")
    def check_times(self):
        if self.daylight_enabled and self.missing_at is None:
            raise ValueError("낮밤 계산에는 마지막 확인 날짜와 시각이 필요합니다.")
        if self.analysis_at is not None and self.missing_at is None:
            raise ValueError("기준 지도 시각과 마지막 확인 시각을 함께 입력해 주세요.")
        if self.missing_at is not None:
            self.analysis_at=self.analysis_at or datetime.now(timezone.utc)
            if not all(1900<=dt.year<=2100 for dt in (self.missing_at,self.analysis_at)):
                raise ValueError("모의 시각은 1900–2100년 범위여야 합니다.")
            hours=(self.analysis_at-self.missing_at).total_seconds()/3600
            if not .25<=hours<=8:
                raise ValueError("마지막 확인부터 기준 지도까지 15분–8시간 범위로 입력해 주세요.")
            self.hours=hours
        return self

class ApplyInput(BaseModel):
    outcome:str
    expected_version:int=Field(ge=0)

class TrackTeamInput(BaseModel):
    team_size:int=Field(default=1,ge=1,le=core.MAX_TEAM_SIZE)
    spacing_m:float=Field(default=15.,ge=core.MIN_SPACING_M,le=core.MAX_SPACING_M,allow_inf_nan=False)

class LedgerInput(BaseModel):
    note:str=Field(min_length=2,max_length=1000)
    source:str=Field(default="조정자",max_length=100)
    observed_at:str=Field(default="",max_length=50)

def get_mission(c,mid):
    m=c.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
    if m is None: raise HTTPException(404,"임무를 찾을 수 없습니다.")
    return m

def version_row(c,mid,number=None):
    m=get_mission(c,mid)
    n=m["current_version"] if number is None else number
    v=c.execute("SELECT * FROM versions WHERE mission=? AND number=?",(mid,n)).fetchone()
    if v is None: raise HTTPException(404,"지도 버전을 찾을 수 없습니다.")
    return m,v

def save_version(c,mid,number,priors,coverage,event,parent_hash,params,evidence=None):
    state=core.pack(priors,coverage)
    t=core.terrain_for(params)
    summary,_,_=core.summarize(priors,coverage,t)
    stamp=now()
    receipt={"engine":core.ENGINE,"mission":mid,"version":number,"parent_hash":parent_hash,"event":event,
             "created_at":stamp,"parameters":params,"assumptions":core.ASSUMPTIONS,
             "state_at":params.get("analysis_at") or stamp,
             "observation_time_model":"STATIC_SNAPSHOT_SUMMARY",
             "state_sha256":sha(state),"terrain_sources":t.meta["sources"],"analysis_window":t.window,
             "input_files":core.data_file_hashes(),
             "code_files":{name:sha((ROOT/name).read_bytes()) for name in ("backend/core.py","backend/main.py","backend/live.py","backend/facilities.py","backend/daylight.py")},
             "evidence":evidence}
    if params.get("facility_snapshot"):
        from . import facilities
        _,_,metadata=facilities.snapshot(params["facility_snapshot"])
        receipt["facility_snapshot"]=params["facility_snapshot"]
        receipt["facility_source"]=metadata
    digest=sha(canonical(receipt).encode())
    c.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?)",(mid,number,parent_hash,digest,event,stamp,state,canonical(summary),canonical(receipt)))
    c.execute("UPDATE missions SET current_version=? WHERE id=?",(number,mid))
    return number

def create_mission(data):
    params=data.model_dump(mode="json")
    if params["daylight_enabled"]:
        params["daylight_model"]=daylight.MODEL
    if params["facility_influence"]:
        from . import facilities
        latest=facilities.latest()
        if not latest.get("available"):
            raise HTTPException(422,"시설 데이터가 준비되지 않았습니다. 시설 가정을 끄고 다시 시도해 주세요.")
        params["facility_snapshot"]=latest["snapshot"]
    started=time.perf_counter()
    try: priors=core.compute(params)
    except ValueError as e: raise HTTPException(422,str(e))
    params["compute_seconds"]=round(time.perf_counter()-started,3)
    mid=uuid.uuid4().hex
    with LOCK,connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO missions VALUES(?,?,?,?,0)",(mid,params["name"],canonical(params),now()))
        save_version(c,mid,0,priors,np.zeros((3,core.N,core.N)),"SCENARIOS_COMPUTED","",params)
    return mid

@asynccontextmanager
async def lifespan(app):
    initialize()
    core.master();core.terrain()
    with connect() as c: exists=c.execute("SELECT 1 FROM missions LIMIT 1").fetchone()
    if not exists: create_mission(MissionInput())
    yield

app=FastAPI(title="천라지망 local prototype",lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","testserver"])

@app.middleware("http")
async def local_boundary(request:Request,call_next):
    origin=request.headers.get("origin")
    if request.method not in ("GET","HEAD","OPTIONS") and origin and origin not in ("http://127.0.0.1:8000","http://localhost:8000","http://127.0.0.1:5173","http://localhost:5173"):
        return Response("Local application only",status_code=403)
    limit=26_000_000 if request.url.path=="/api/verify" else 2_200_000
    try:
        if int(request.headers.get("content-length","0"))>limit: return Response("File too large",status_code=413)
    except ValueError: return Response("Invalid length",status_code=400)
    response=await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"]="no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    return response

@app.get("/api/health")
def health(): return {"ok":True,"mode":"LOCAL_DEMO","engine":core.ENGINE}

@app.get("/api/base")
def base():
    from . import facilities
    m=core.master()
    return {**m.meta,"bounds":m.bounds(),"window":{"cells":core.N,"res_m":core.CELL,"size_m":core.N*core.CELL,"lattice_cells":core.LATTICE,"legacy":core.terrain().window},
            "assumptions":core.ASSUMPTIONS,"facilities":facilities.latest(),
            "deploy_profile":core.DEPLOY_PROFILE,"noncommercial_enabled":core.NONCOMMERCIAL_ENABLED,
            "satellite_enabled":core.NONCOMMERCIAL_ENABLED,"walking_lab_enabled":core.NONCOMMERCIAL_ENABLED,
            "distance_models":core.DISTANCE_MODELS,"team_limits":{"max_team_size":core.MAX_TEAM_SIZE,"min_spacing_m":core.MIN_SPACING_M,"max_spacing_m":core.MAX_SPACING_M},
            "row_share_default":core.ASSUMPTIONS["row_share_default"]}

@app.get("/api/facilities")
def facility_data(snapshot:str|None=None,mission:str|None=None):
    """POI extract; with ?mission= only the POIs in that mission's analysis window (+ margin)."""
    from . import facilities
    digest=snapshot or facilities.latest().get("snapshot")
    if not digest: return {"type":"FeatureCollection","features":[]}
    try:
        _,geo,metadata=facilities.snapshot(digest)
        if mission:
            with connect() as c: m=get_mission(c,mission)
            row0,col0=core.window_of(json.loads(m["params"]))
            features=facilities.features_in_window(geo,row0,col0,facilities.MARGIN)
            return {"type":"FeatureCollection","features":features,"metadata":{**metadata,"window_count":len(features),"window":{"row0":row0,"col0":col0}}}
        return {**geo,"metadata":metadata}
    except ValueError as e: raise HTTPException(422,str(e))

@app.get("/api/base/basemap.png")
def base_basemap():
    return Response(core.basemap_png(*core.LEGACY_WINDOW),media_type="image/png")

@app.get("/api/missions/{mid}/basemap.png")
def mission_basemap(mid:str):
    with connect() as c: m=get_mission(c,mid)
    return Response(core.basemap_png(*core.window_of(json.loads(m["params"]))),media_type="image/png")

@app.get("/api/missions")
def missions():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT id,name,created_at,current_version FROM missions ORDER BY created_at DESC")]

@app.post("/api/missions")
def new_mission(data:MissionInput): return {"id":create_mission(data)}

@app.get("/api/missions/{mid}")
def state(mid:str,version:int|None=None):
    with connect() as c:
        m,v=version_row(c,mid,version)
        tracks=[dict(r) for r in c.execute("SELECT id,name,status,sha256,summary,created_at,applied_version FROM tracks WHERE mission=? ORDER BY created_at DESC",(mid,))]
        for tr in tracks: tr["summary"]=json.loads(tr["summary"])
        versions=[dict(r) for r in c.execute("SELECT number,event,created_at,hash,parent_hash FROM versions WHERE mission=? ORDER BY number DESC",(mid,))]
        ledger=[dict(r) for r in c.execute("SELECT id,kind,note,source,observed_at,created_at FROM ledger WHERE mission=? ORDER BY created_at DESC",(mid,))]
        decisions={r["zone"]:r["status"] for r in c.execute("SELECT zone,status FROM decisions WHERE mission=?",(mid,))}
        summary=json.loads(v["summary"])
        previous=c.execute("SELECT summary FROM versions WHERE mission=? AND number=?",(mid,v["number"]-1)).fetchone()
        previous_ranks={f["properties"]["id"]:f["properties"]["rank"] for f in json.loads(previous["summary"])["zones"]["features"]} if previous else {}
        for f in summary["zones"]["features"]:
            p=f["properties"]
            p["decision"]=decisions.get(p["id"],"PROPOSED")
            p["previous_rank"]=previous_ranks.get(p["id"])
        params=json.loads(m["params"])
        return {**summary,"id":mid,"name":m["name"],"params":params,"version":v["number"],"current_version":m["current_version"],
                "window":core.terrain_for(params).window,
                "receipt":{**json.loads(v["receipt"]),"hash":v["hash"]},"tracks":tracks,"versions":versions,"ledger":ledger,
                "daylight":daylight.context(json.loads(m["params"])),"status":roles.mission_status(c,mid),
                "members":{"volunteers":c.execute("SELECT COUNT(*) AS n FROM members WHERE mission=? AND role='volunteer' AND revoked_at IS NULL",(mid,)).fetchone()["n"],
                           "family":c.execute("SELECT COUNT(*) AS n FROM members WHERE mission=? AND role='family' AND revoked_at IS NULL",(mid,)).fetchone()["n"]}}

@lru_cache(maxsize=48)
def render_image(mid,number,layer,phase):
    with connect() as c: _,v=version_row(c,mid,number)
    priors,C=core.unpack(v["state"])
    return core.png_for(priors,C,layer,phase)

@app.get("/api/missions/{mid}/layers/{layer}.png")
def layer(mid:str,layer:str,version:int=0,phase:str="post"):
    if layer not in ("a","b","b2","combined","shadow","consensus") or phase not in ("prior","post"): raise HTTPException(422)
    return Response(render_image(mid,version,layer,phase),media_type="image/png")

@lru_cache(maxsize=6)
def cached_forecast(db_path, mid, number, version_hash, seconds):
    with connect() as c:
        m,v=version_row(c,mid,number)
    if v["hash"]!=version_hash: raise HTTPException(409,"기준 지도 버전이 변경되었습니다.")
    priors,coverage=core.unpack(v["state"])
    started=time.perf_counter()
    base_at=json.loads(v["receipt"]).get("state_at",v["created_at"])
    params=json.loads(m['params']);events=();timed=bool(params.get('analysis_at'));purged_note=None
    if timed:
        from . import temporal
        try:
            events=tuple(e for e in cached_observations(db_path,mid,number,version_hash) if e.seconds<=seconds)
            with connect() as c:_,initial=version_row(c,mid,0)
            # Replay from the immutable pre-search state. Approval time/order never
            # changes when a recorded observation acts on a moving trajectory.
            priors,coverage=core.unpack(initial['state'])
            base_at=params['analysis_at']
        except ValueError as e:
            if '삭제' not in str(e): raise HTTPException(422,str(e))
            # Hand-over purge removed the raw observations: propagate the saved summary instead.
            timed=False;purged_note=str(e)
    try: predictions,baseline,summary,speed=live.forecast(priors,coverage,seconds,params,start_at=base_at,observations=events)
    except ValueError as e: raise HTTPException(422,str(e))
    if timed:
        speed['observation_timing']={'model':temporal.MODEL,'bin_seconds':temporal.BIN_SECONDS,'time_origin':base_at,
            'uses_approval_time':False,'note':'승인된 GPX의 관찰 시각을 60초 구간 끝에 반영합니다. 최대 60초 지연 근사이며 이동과 탐지 규칙은 검증 전입니다.'}
    else:
        speed['observation_timing']={'model':'LEGACY_STATIC_SNAPSHOT','note':purged_note or '실종 시각과 기준 시각이 없는 이전 임무입니다. 시간별 관찰 재생 없이 정적 요약을 전파합니다.'}
    return predictions,baseline,summary,speed,round(time.perf_counter()-started,3),now()


@lru_cache(maxsize=4)
def cached_observations(db_path,mid,number,version_hash):
    from . import temporal
    with connect() as c:
        m,v=version_row(c,mid,number)
        if v['hash']!=version_hash:raise ValueError('기준 버전 불일치')
        rows=c.execute("SELECT raw,summary FROM tracks WHERE mission=? AND status='APPLIED' AND applied_version<=?",(mid,number)).fetchall()
    if any(not r['raw'] for r in rows):
        raise ValueError('인계 완료로 원본 수색 기록이 삭제된 임무입니다. 시간 재생 대신 저장된 지도를 사용합니다.')
    raws=[(r['raw'],json.loads(r['summary']).get('team',{})) for r in rows]
    params=json.loads(m['params'])
    return temporal.observations(raws,params['analysis_at'],live.MAX_SECONDS,core.terrain_for(params))


@lru_cache(maxsize=2)
def cached_plan_paths(db_path,mid,number,version_hash,seconds,minutes):
    from . import trajectory,facilities,temporal
    end=seconds+minutes*60
    if seconds<0 or end>live.MAX_SECONDS:
        raise ValueError('수색 종료까지 최초 기준 시각의 6시간 이내여야 합니다. 계획 시간을 줄여 주세요.')
    with connect() as c:
        m,v=version_row(c,mid,number)
        if v['hash']!=version_hash:raise ValueError('계획 기준 버전 불일치')
        params=json.loads(m['params'])
        base_at=json.loads(v['receipt']).get('state_at',v['created_at'])
        priors,coverage=core.unpack(v['state'])
        if params.get('analysis_at'):
            _,initial=version_row(c,mid,0)
            priors,coverage=core.unpack(initial['state']);base_at=params['analysis_at']
    # Future known observations must not leak into a historical proposal.
    events=tuple(e for e in cached_observations(db_path,mid,number,version_hash) if e.seconds<=seconds) if params.get('analysis_at') else ()
    t=core.terrain_for(params)
    field=facilities.resolve(params,t);clock=daylight.clock_for(params,base_at,end)
    times=np.arange(seconds,end+1,trajectory.BIN_SECONDS,dtype=float)
    paths=[]
    baselines=[core.update(d,coverage[1]) for d in priors]
    budgets=trajectory.sample_budgets([2*np.count_nonzero(d.grid) for d in baselines],len(times))
    for i,(d,budget) in enumerate(zip(baselines,budgets)):
        _,stats=live.project(d,end,params['seed']+i*101,behavior=(i==2),terrain=t,
                             facility_field=field if i==2 else None,facility_strength=params.get('facility_strength',0),
                             clock=clock,observations=events,capture_times=times,max_packets=budget)
        paths.append(stats['trajectory'])
    timing={'model':temporal.MODEL if params.get('analysis_at') else 'LEGACY_STATIC_SNAPSHOT',
            'time_origin':base_at,'observation_cutoff_seconds':seconds,'observation_bins':len(events),
            'uses_approval_time':False,'future_observations_used':False}
    return tuple(paths),timing


def forecast_context(mid,version,seconds):
    with connect() as c: m,v=version_row(c,mid,version)
    params=json.loads(m["params"])
    # Preserve legacy relative-time missions; never infer a missing timestamp.
    base_at=daylight.aware(params.get('analysis_at') or v['created_at'])
    age=(datetime.now(timezone.utc)-base_at).total_seconds()
    if seconds is None and age<0:
        raise HTTPException(422,"기준 지도 시각이 미래입니다. 실시간 추정 대신 시간 미리보기를 사용해 주세요.")
    age=max(0,age)
    requested=int(age//live.REFRESH_SECONDS)*live.REFRESH_SECONDS if seconds is None else seconds
    elapsed=min(requested,live.MAX_SECONDS)
    result=cached_forecast(str(DB),mid,v["number"],v["hash"],elapsed)
    return v,base_at,elapsed,requested>live.MAX_SECONDS,result


@app.get("/api/missions/{mid}/forecast")
def forecast_state(mid:str,version:int|None=None,seconds:int|None=Query(default=None,ge=0,le=live.MAX_SECONDS)):
    v,base_at,elapsed,expired,(_,_,summary,speed,duration,computed_at)=forecast_context(mid,version,seconds)
    return {"mission":mid,"base_version":v["number"],"base_hash":v["hash"],
            "base_at":base_at.isoformat(),"estimated_at":(base_at+timedelta(seconds=elapsed)).isoformat(),
            "computed_at":computed_at,"projection_seconds":elapsed,"max_seconds":live.MAX_SECONDS,
            "refresh_seconds":live.REFRESH_SECONDS,"expired":expired,"saved":False,
            "zones":summary["zones"],"outside_mixed":summary["outside_mixed"],
            "outside":summary["outside"],"inside_mass":summary["inside_mass"],
            "water_mass":summary["water_mass"],"speed":speed,"compute_seconds":duration}


@app.get("/api/missions/{mid}/forecast/{layer}.png")
def forecast_image(mid:str,layer:str,version:int,seconds:int=Query(ge=0,le=live.MAX_SECONDS)):
    if layer not in ("a","b","b2","combined","consensus"): raise HTTPException(422)
    _,_,_,_,(predictions,baseline,_,_,_,_)=forecast_context(mid,version,seconds)
    return Response(core.png_for(predictions,np.zeros((3,core.N,core.N)),layer,"prior",reference=baseline),media_type="image/png")

def insert_track(mid,raw,name,team=None,member=None):
    team=team or TrackTeamInput()
    with connect() as c: params=json.loads(get_mission(c,mid)["params"])
    try:
        segments,digest=core.parse_gpx(raw)
        coverage,summary=core.coverage_for(segments,team.team_size,team.spacing_m,core.terrain_for(params))
    except ValueError as e: raise HTTPException(422,str(e))
    if member: summary["submitted_by"]=member
    buff=io.BytesIO();np.save(buff,coverage,allow_pickle=False)
    tid=uuid.uuid4().hex
    try:
        with LOCK,connect() as c:
            get_mission(c,mid)
            roles.require_active(c,mid)
            c.execute("INSERT INTO tracks VALUES(?,?,?,?,?,?,?,?,?,NULL)",(tid,mid,name[:100],"UPLOADED",digest,raw,buff.getvalue(),canonical(summary),now()))
    except sqlite3.IntegrityError: raise HTTPException(409,"같은 좌표 경로가 이미 등록되어 있습니다. 중복 반영하지 않았습니다.")
    return {"id":tid,"status":"UPLOADED","summary":summary}

def track_team(team_size,spacing_m):
    try: return TrackTeamInput(team_size=team_size,spacing_m=spacing_m)
    except ValueError as e: raise HTTPException(422,"팀 인원은 1~12명, 대원 간격은 3~60 m 범위여야 합니다.")

@app.post("/api/missions/{mid}/tracks")
async def upload(mid:str,file:UploadFile=File(...),team_size:int=Form(default=1),spacing_m:float=Form(default=15.)):
    raw=await file.read(2_000_001)
    return insert_track(mid,raw,Path(file.filename or "track.gpx").name,track_team(team_size,spacing_m))

@app.post("/api/missions/{mid}/demo-track")
def demo_track(mid:str,team:TrackTeamInput|None=None):
    with connect() as c: m=get_mission(c,mid)
    return insert_track(mid,core.demo_gpx(json.loads(m["params"])),"합성 훈련 트랙.gpx",team or TrackTeamInput())

@app.get("/api/missions/{mid}/demo.gpx")
def demo_download(mid:str):
    with connect() as c: m=get_mission(c,mid)
    return Response(core.demo_gpx(json.loads(m["params"])),media_type="application/gpx+xml",headers={"Content-Disposition":"attachment; filename=searchproof-demo.gpx"})

@app.get("/api/missions/{mid}/tracks/{tid}/shadow.png")
def track_shadow(mid:str,tid:str):
    with connect() as c:
        _,v=version_row(c,mid)
        tr=c.execute("SELECT coverage FROM tracks WHERE mission=? AND id=?",(mid,tid)).fetchone()
        if not tr: raise HTTPException(404)
    priors,_=core.unpack(v["state"])
    return Response(core.png_for(priors,np.load(io.BytesIO(tr["coverage"]),allow_pickle=False),"shadow","post"),media_type="image/png")

@app.post("/api/missions/{mid}/tracks/{tid}/apply")
def apply_track(mid:str,tid:str,data:ApplyInput):
    if data.outcome!="COMPLETED_NO_FIND": raise HTTPException(422,"미발견으로 확정한 수색만 반영할 수 있습니다.")
    with LOCK,connect() as c:
        c.execute("BEGIN IMMEDIATE")
        m,v=version_row(c,mid)
        roles.require_active(c,mid)
        if m["current_version"]!=data.expected_version: raise HTTPException(409,"지도가 변경되었습니다. 최신 버전을 확인한 뒤 다시 승인해 주세요.")
        tr=c.execute("SELECT * FROM tracks WHERE id=? AND mission=?",(tid,mid)).fetchone()
        if not tr: raise HTTPException(404)
        if tr["status"]!="UPLOADED": raise HTTPException(409,"이미 처리된 기록입니다.")
        priors,C=core.unpack(v["state"])
        addition=np.load(io.BytesIO(tr["coverage"]),allow_pickle=False)
        segments,_=core.parse_gpx(tr['raw'])
        observed=[p[2] for s in segments for p in s]
        number=v["number"]+1
        save_version(c,mid,number,priors,C+addition,"COMPLETED_NO_FIND:"+tid,v["hash"],json.loads(m["params"]),
                     {"track_id":tid,"gpx_sha256":sha(tr["raw"]),"geometry_sha256":tr["sha256"],"coverage_sha256":sha(tr["coverage"]),
                      'observed_start_at':datetime.fromtimestamp(min(observed),timezone.utc).isoformat(),
                      'observed_end_at':datetime.fromtimestamp(max(observed),timezone.utc).isoformat()})
        c.execute("UPDATE tracks SET status='APPLIED',applied_version=? WHERE id=?",(number,tid))
    return {"version":number}

@app.post("/api/missions/{mid}/tracks/{tid}/reject")
def reject_track(mid:str,tid:str):
    with LOCK,connect() as c:
        roles.require_active(c,mid)
        changed=c.execute("UPDATE tracks SET status='REJECTED' WHERE mission=? AND id=? AND status='UPLOADED'",(mid,tid)).rowcount
        if not changed: raise HTTPException(409,"대기 중인 기록만 제외할 수 있습니다.")
    return {"ok":True}

@app.post("/api/missions/{mid}/ledger")
def add_ledger(mid:str,data:LedgerInput):
    observed=data.observed_at or now()
    try:
        parsed=datetime.fromisoformat(observed.replace("Z","+00:00"))
        if parsed.tzinfo is None: raise ValueError()
    except ValueError: raise HTTPException(422,"관찰 시각에 시간대가 필요합니다.")
    with LOCK,connect() as c:
        get_mission(c,mid); roles.require_active(c,mid)
        c.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?)",(uuid.uuid4().hex,mid,"CLUE",data.note,data.source,observed,now()))
    return {"ok":True,"map_changed":False}

@app.post("/api/missions/{mid}/zones/{zid}/approve")
def approve_zone(mid:str,zid:int):
    with LOCK,connect() as c:
        _,v=version_row(c,mid); roles.require_active(c,mid)
        zone=next((f["properties"] for f in json.loads(v["summary"])["zones"]["features"] if f["properties"]["id"]==zid),None)
        if not zone: raise HTTPException(404)
        if zone["access"]=="AGENCY_ONLY": raise HTTPException(422,"전문기관 검토 구역은 일반팀에 배정할 수 없습니다.")
        c.execute("INSERT INTO decisions VALUES(?,?,?,?) ON CONFLICT(mission,zone) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at",(mid,zid,"APPROVED",now()))
        c.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?,?)",(uuid.uuid4().hex,mid,"DECISION",f"구역 {zone['label']} 수색 후보 승인 (현장 접근 확인 별도)","조정자",now(),now()))
    return {"ok":True}

@app.get("/api/missions/{mid}/package.zip")
def package(mid:str):
    with connect() as c:
        m,v=version_row(c,mid)
        versions=[dict(r) for r in c.execute("SELECT number,receipt,hash FROM versions WHERE mission=? ORDER BY number",(mid,))]
        ledger=[dict(r) for r in c.execute("SELECT * FROM ledger WHERE mission=?",(mid,))]
        ai_plans=[{**json.loads(r['result']),'status':r['status']} for r in c.execute(
            "SELECT result,status FROM ai_plans WHERE mission=? ORDER BY created_at",(mid,))]
        ai_maps=[dict(r) for r in c.execute('SELECT m.* FROM ai_plan_maps m JOIN ai_plans p ON p.id=m.plan WHERE p.mission=?',(mid,))]
        params=json.loads(m["params"])
        participants=roles.package_files(c,mid)
    summary=json.loads(v["summary"])
    files={"zones.geojson":canonical(summary["zones"]).encode(),"parameters.json":canonical(params).encode(),
           "state.npz":v["state"],"receipts.json":canonical(versions).encode(),"ledger.json":canonical(ledger).encode(),
           "data_sources.json":canonical(core.terrain_for(params).meta).encode(),"participants.json":canonical(participants).encode()}
    if ai_plans:
        from . import ml_models
        files['ai/plans.json']=canonical(ai_plans).encode()
        for image in ai_maps:
            if sha(image['image'])!=image['sha256']:raise HTTPException(503,'계획 지도 무결성 확인 실패')
            files['ai/maps/'+image['plan']+'.png']=image['image']
        for model_id in sorted({p['model_id'] for p in ai_plans}):
            bundle=ml_models.load_bundle(model_id)
            files['ai/'+model_id+'/manifest.json']=canonical(bundle['manifest']).encode()
            files['ai/'+model_id+'/endpoint.json']=canonical(bundle['endpoint']).encode()
            files['ai/'+model_id+'/searcher_speed.json']=canonical(bundle['searcher_speed']).encode()
    if params.get("facility_snapshot"):
        from . import facilities
        _,geo,manifest=facilities.snapshot(params["facility_snapshot"])
        row0,col0=core.window_of(params)
        files["facilities/facilities.geojson"]=canonical(geo).encode()
        files["facilities/manifest.json"]=canonical({**manifest,"window":{"row0":row0,"col0":col0}}).encode()
        try:
            field=facilities.window_field(params["facility_snapshot"],row0,col0)
            buff=io.BytesIO();np.save(buff,np.asarray(field),allow_pickle=False);files["facilities/potential.npy"]=buff.getvalue()
        except ValueError as e:
            files["facilities/potential_unavailable.txt"]=str(e).encode()
    csvout=io.StringIO(); writer=csv.writer(csvout)
    writer.writerow(["zone","POA","POD_low_assumption","POD_high_assumption","POS","access"])
    for f in summary["zones"]["features"]:
        p=f["properties"];writer.writerow([p["label"],p["poa"],p["pod_low"],p["pod_high"],p["pos"],p["access"]])
    files["zones.csv"]=("\ufeff"+csvout.getvalue()).encode()
    files["README.txt"]=("천라지망 로컬 훈련용 인계 패키지\n실제 사건 검증 전입니다. 탐지확률 범위는 가정 범위이며 신뢰구간이 아닙니다.\n원본 GPX/개인정보는 이 패키지에 포함하지 않았습니다.\nPDF는 앱의 보고서 인쇄에서 별도로 저장합니다.\n무결성 확인은 앱의 패키지 검증에서 ZIP을 선택하세요.\n해시는 내용 변경을 탐지할 뿐 기록의 진실성이나 기관 승인을 증명하지 않습니다.\n").encode()
    manifest={"format":"searchproof-package-v1","version_hash":v["hash"],"files":{name:sha(raw) for name,raw in files.items()}}
    files["manifest.json"]=canonical(manifest).encode()
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,"w",zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items(): z.writestr(name,raw)
    return Response(buff.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="Cheonrajimang-v{v["number"]}.zip"'})

@app.post("/api/verify")
async def verify(file:UploadFile=File(...)):
    raw=await file.read(25_000_001)
    if len(raw)>25_000_000: raise HTTPException(413)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos=z.infolist()
            if len(infos)>50 or sum(i.file_size for i in infos)>100_000_000: raise ValueError("Package size limit")
            if len({i.filename for i in infos})!=len(infos): raise ValueError("Duplicate archive entry")
            manifest=json.loads(z.read("manifest.json"))
            expected=manifest["files"]
            failed=[name for name,digest in expected.items() if sha(z.read(name))!=digest]
            extras=set(z.namelist())-set(expected)-{"manifest.json"}
            if extras: failed+=list(extras)
        return {"valid":not failed,"files":len(expected),"failed":failed,"note":"패키지 내부 해시 비교 결과입니다. 발급자 신원, 관측 사실, 외부 봉인은 확인하지 않습니다."}
    except Exception: raise HTTPException(422,"지원되는 천라지망 패키지가 아니거나 손상되었습니다.")

@app.get("/api/missions/{mid}/report",response_class=Response)
def report(mid:str):
    from . import ml_models
    s=state(mid)
    e=html.escape
    rows="".join(f"<tr><td>{e(f['properties']['label'])}</td><td>{f['properties']['poa']*100:.2f}%</td><td>{f['properties']['pod_low']*100:.1f}–{f['properties']['pod_high']*100:.1f}%</td><td>{f['properties'].get('agreement',0)*100:.0f}%</td><td>{'기관 검토' if f['properties']['access']=='AGENCY_ONLY' else '현장 확인 필요'}</td></tr>" for f in s["zones"]["features"][:30])
    track_rows="".join(f"<tr><td>{e(t['name'])}</td><td>{ {'UPLOADED':'확정 대기','APPLIED':'미발견 반영','REJECTED':'제외'}.get(t['status'],t['status'])}</td><td>{t['summary']['distance_m']/1000:.2f} km</td><td>{t['summary'].get('team',{}).get('team_size',1)}명, 간격 {t['summary'].get('team',{}).get('spacing_m',15):g} m</td><td>{t['summary']['gaps']} / {t['summary']['excluded_segments']}</td></tr>" for t in s["tracks"])
    notes="".join(f"<li><b>{e({'DECISION':'결정','CLUE':'단서','AI_PLAN':'AI 계획'}.get(l['kind'],l['kind']))}</b> {e(l['note'])} <small>({e(l['source'])}, {e(l['created_at'][:16])})</small></li>" for l in s["ledger"])
    dm=s["params"].get("distance_model_used",{}); dm_text=("실제 실종 사건 %d건의 발견 거리 lognormal(도달 가능 반경 %g km/h로 절단)"%(dm.get("cases",0),dm.get("reach_speed_kmh",0))) if dm.get("used")=="learned_lognormal" else "팀 가정 700√시간 m Gaussian"
    status=s.get("status",{}).get("status","ACTIVE"); status_text={"ACTIVE":"진행 중","PAUSED":"일괄 중지","HANDED_OVER":"기관 인계 완료, 원본 GPX 삭제"}.get(status,status)
    try: comp="".join(f"<tr><td>{e(c['name'])}</td><td>{e(c['status'])}</td><td class='small'>{e(c['detail'])}</td></tr>" for c in ml_models.components(ml_models.load_bundle()))
    except ValueError: comp="<tr><td colspan=3>학습 모델 묶음을 읽을 수 없습니다.</td></tr>"
    with connect() as c: participants=roles.package_files(c,mid)
    members=participants["members"]
    member_rows="".join(f"<tr><td>{e(m['label'])}</td><td>{'봉사자' if m['role']=='volunteer' else '가족'}</td><td>{'동의' if m['consented'] else '미동의'}</td><td>{e(m['last_checkin_at'][:16]) if m['last_checkin_at'] else '-'}</td><td>{'해지' if m['revoked'] else '유효'}</td></tr>" for m in members)
    page=f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>천라지망 인계 보고서</title><style>body{{font:15px system-ui;max-width:900px;margin:40px auto;line-height:1.65;color:#13315a}}header{{display:flex;align-items:center;gap:16px;border-bottom:3px solid #359197;padding-bottom:12px;margin-bottom:18px}}header img{{width:72px;height:72px}}h1{{font-size:24px;margin:0}}h2{{font-size:17px;margin-top:28px;border-left:4px solid #359197;padding-left:8px}}table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{border-bottom:1px solid #d6dee6;text-align:left;padding:6px 8px;vertical-align:top}}th{{background:#eef5f6}}.small{{font-size:12px;overflow-wrap:anywhere}}.meta span{{display:inline-block;margin-right:14px}}.warn{{background:#fff6e5;border:1px solid #f0c987;padding:8px 12px;border-radius:6px;font-size:13.5px}}@media print{{button{{display:none}}body{{margin:16px}}}}</style>
<button onclick="window.print()">인쇄 / PDF로 저장</button>
<header><img src="/logo-mark.png" alt="천라지망 로고"><div><h1>천라지망 수색 상황보고서</h1><div class="small">{e(s["name"])}, 지도 버전 v{s["version"]}, 상태: {status_text}, 로컬 모의 수색</div></div></header>
<p class="warn">판단 보조용 훈련 도구의 출력입니다. 실제 수색 성능은 검증 전이며, POD 범위는 탐지폭 가정 8–22 m와 팀 밴드 가정에 따른 범위입니다. 낮은 확률은 부재의 증거가 아닙니다.</p>
<p class="meta"><span>마지막 확인 위치 {s["params"]["lat"]:.5f}, {s["params"]["lon"]:.5f}</span><span>경과 {s["params"]["hours"]:.2f}시간</span><span>거리 시나리오: {e(dm_text)}</span><span>모형 밖 잔여(ROW) 초기 {s["params"].get("row_share",0)*100:.0f}%</span></p>
<p class="meta"><span><b>현재 지도 밖 잔여확률 {s["outside_mixed"]*100:.2f}%</b></span><span>수색 기록 영역 {s["coverage_km2"]:.3f} km²</span><span>확정 수색 기록 {sum(1 for t in s["tracks"] if t["status"]=="APPLIED")}건</span></p>
<h2>우선순위 구역 (최대 30개)</h2><table><tr><th>구역</th><th>존재 가능성</th><th>조건부 탐지확률 범위</th><th>가설 합의도</th><th>접근 검토</th></tr>{rows}</table>
<p class="small">가설 합의도 = 세 시나리오 구역 확률의 최소/최대 비율. 순위는 세 시나리오의 기하평균(로그 선형 합의)으로 정하며, 어느 한 시나리오라도 0이면 후보에서 빠집니다.</p>
<h2>수색 기록</h2><table><tr><th>기록</th><th>상태</th><th>유효 거리</th><th>팀 구성 가정</th><th>공백 / 제외 구간</th></tr>{track_rows or '<tr><td colspan=5>등록된 수색 기록 없음</td></tr>'}</table>
<h2>참여자</h2><table><tr><th>이름</th><th>역할</th><th>위치정보 동의</th><th>마지막 체크인</th><th>코드</th></tr>{member_rows or '<tr><td colspan=5>참여 코드 발급 기록 없음</td></tr>'}</table>
<p class="small">참여 코드 원문은 저장하지 않으며, 인계 완료 시 원본 GPX가 삭제되고 해시만 남습니다. 삭제 기록 {len(participants['purged_tracks'])}건.</p>
<h2>근거 장부</h2><ul>{notes or '<li>추가된 근거 없음</li>'}</ul>
<h2>이 지도를 만든 계산 요소</h2><table><tr><th>요소</th><th>상태</th><th>내용</th></tr>{comp}</table>
<h2>재현 영수증</h2><p class="small">버전 해시 {s["receipt"]["hash"]}</p><pre class="small">{e(json.dumps(s["params"],ensure_ascii=False,indent=2))}</pre><p class="small">{e(core.master().meta["attribution"])}</p></html>"""
    return Response(page,media_type="text/html")

from .ai_api import router as ai_router
app.include_router(ai_router)
app.include_router(roles.router)
from .walking_reference import router as walking_router
app.include_router(walking_router)
from .walking_evaluation import router as walking_evaluation_router
app.include_router(walking_evaluation_router)

DIST=ROOT/"frontend/dist"
if DIST.exists(): app.mount("/",StaticFiles(directory=DIST,html=True),name="frontend")
