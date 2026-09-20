"""Local-only learned ordinary-mobility experiment, isolated from mission planning.

No source GPS, serialized model or fitted transition table is exposed by this API.
MSR-LA permits non-commercial demonstrations; artifacts must not be redistributed.
"""
from pathlib import Path
import hashlib
import json
import re
import numpy as np
from fastapi import APIRouter,HTTPException,Query

ROOT=Path(__file__).resolve().parents[1]
MODELS=ROOT/'data/models/walking_reference'
router=APIRouter()
NOTICE='일반 보행 실험 모델입니다. 평가 인원 부족으로 미채택이며 실제 실종자, 지형, 밤, 시설, 교통, 탐지확률을 검증하지 않았습니다.'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def load():
    try:
        pointer=json.loads((MODELS/'current.json').read_bytes());mid=pointer['model_id']
        if not re.fullmatch('[a-f0-9]{64}',mid):raise ValueError('Invalid model ID')
        folder=MODELS/mid;raw=(folder/'receipt.json').read_bytes()
        if sha(raw)!=pointer['receipt_sha256']:raise ValueError('Receipt hash mismatch')
        receipt=json.loads(raw)
        if receipt['model_id']!=mid or set(receipt['files'])!={'model.json','evaluation.json','inventory.json'}:
            raise ValueError('Unexpected receipt')
        files={}
        for name,digest in receipt['files'].items():
            raw=(folder/name).read_bytes()
            if sha(raw)!=digest:raise ValueError('Model artifact hash mismatch')
            if name=='model.json' and sha(raw)!=mid:raise ValueError('Model identity mismatch')
            files[name]=json.loads(raw)
        model=files['model.json'];p=np.array(model['transition_prob']);initial=np.array(model['initial_speed_prob'])
        speeds=np.array(model['speed_representatives_mps'])
        if model['format']!='cheonrajimang-ordinary-walk-v1' or model['step_seconds']!=30:raise ValueError('Unsupported model')
        if p.shape!=(6,54) or not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1):raise ValueError('Invalid probability matrix')
        if initial.shape!=(6,) or not np.isfinite(initial).all() or (initial<0).any() or not np.isclose(initial.sum(),1):raise ValueError('Invalid initial probability')
        if speeds.shape!=(6,) or not np.isfinite(speeds).all() or (speeds<0).any() or (speeds>4).any():raise ValueError('Invalid speed representatives')
        return mid,model,files['evaluation.json'],files['inventory.json']
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as e:raise ValueError('Walking model is unavailable or invalid') from e

def rollout(model,seconds,seed,particles=4096):
    """Shared numerical kernel for the UI and evaluation; no observed future inputs."""
    if not isinstance(seconds,int) or isinstance(seconds,bool) or not 0<=seconds<=300:raise ValueError('Seconds must be 0–300')
    if not isinstance(seed,int) or isinstance(seed,bool) or not 0<=seed<2**31:raise ValueError('Invalid seed')
    if not isinstance(particles,int) or isinstance(particles,bool) or not 1<=particles<=65536:raise ValueError('Invalid sample count')
    rng=np.random.default_rng(seed);cdf=np.cumsum(model['transition_prob'],axis=1);cdf[:,-1]=1
    initial=np.cumsum(model['initial_speed_prob']);initial[-1]=1
    state=np.searchsorted(initial,rng.random(particles),side='right')
    heading=rng.uniform(-np.pi,np.pi,particles)
    speeds=np.array(model['speed_representatives_mps']);pos=np.zeros((particles,2));distance=np.zeros(particles)
    frames=[pos[:24].copy()];timestamps=[0];elapsed=0
    while elapsed<seconds:
        choice=(rng.random(particles)[:,None]>=cdf[state]).sum(axis=1)
        state,turn=np.divmod(choice,9)
        unknown_heading=rng.uniform(-np.pi,np.pi,particles)
        heading=np.where(turn==8,unknown_heading,heading+turn*np.pi/4)
        dt=min(30,seconds-elapsed);length=speeds[state]*dt
        pos+=length[:,None]*np.column_stack([np.cos(heading),np.sin(heading)])
        distance+=length;elapsed+=dt;frames.append(pos[:24].copy());timestamps.append(elapsed)
    return pos,distance,frames,timestamps

def simulate(model,seconds,seed,particles=4096):
    if not isinstance(particles,int) or isinstance(particles,bool) or not 1<=particles<=8192:raise ValueError('Invalid sample count')
    pos,distance,frames,timestamps=rollout(model,seconds,seed,particles)
    # Fixed 40m bins, no clipping of outlying probability into an edge cell.
    cell=40;bins=np.floor(pos/cell).astype(int)
    unique,counts=np.unique(bins,axis=0,return_counts=True)
    return {'seconds':seconds,'seed':seed,'particles':particles,'step_seconds':30,
            'cell_m':cell,'cells':[[int(x),int(y),int(n)/particles] for (x,y),n in zip(unique,counts)],
            'mass':float(counts.sum()/particles),'frames_seconds':timestamps,
            'paths':np.stack(frames).transpose(1,0,2).round(3).tolist(),
            'mean_net_speed_mps':float(distance.mean()/seconds) if seconds else 0.,
            'median_displacement_m':float(np.median(np.linalg.norm(pos,axis=1))),
            'simulation_only':True,'long_horizon_validated':False,
            'terrain_applied':False,'source_coordinates_exposed':False,
            'note':'학습한 30초 전이를 반복 생성한 가상 경로입니다. 임무·수색 추천·인계에는 반영하지 않습니다.'}

@router.get('/api/ai/walking/status')
def status():
    from . import core
    if not core.NONCOMMERCIAL_ENABLED:raise HTTPException(503,'비상업(MSR-LA) 보행 실험 모델은 이 배포 프로필에서 비활성화되어 있습니다.')
    try:mid,model,evaluation,inventory=load()
    except ValueError as e:raise HTTPException(503,str(e))
    supplementary=None
    path=ROOT/'data/ml/geolife/persistence_check.json'
    if path.exists():
        check=json.loads(path.read_text())
        if check.get('model_id')==mid and check.get('prepared_sha256')==inventory['prepared_sha256']:
            supplementary=check['summary']
    return {'available':True,'model_id':mid,'status':evaluation['status'],
            'people':inventory['people'],'rows':inventory['rows'],'splits':inventory['split_counts'],
            'test':evaluation['splits']['test']['summary'],
            'nll_improvement':evaluation['primary_relative_nll_improvement'],
            'post_fit_persistence_check':supplementary,
            'gain_95ci':evaluation['paired_person_bootstrap_nll_gain_95ci'],
            'license':'MSR-LA, 비상업적 로컬 연구, 원자료/모델 재배포 금지',
            'source_url':'https://www.microsoft.com/en-us/download/details.aspx?id=52367',
            'notice':NOTICE,'architecture':'이전 속도에 따른 다음 30초 속도와 회전 확률을 학습한 Markov 모델',
            'mission_integration':False,'missing_person_validated':False}

@router.get('/api/ai/walking/simulate')
def prediction(seconds:int=Query(default=120,ge=0,le=300),seed:int=Query(default=42,ge=0,lt=2**31),
               model_id:str=Query(pattern='^[a-f0-9]{64}$')):
    try:mid,model,evaluation,_=load()
    except ValueError as e:raise HTTPException(503,str(e))
    if model_id!=mid:raise HTTPException(409,'모델이 변경됐습니다. 학습 결과를 새로 불러오세요.')
    return {**simulate(model,seconds,seed),'model_id':mid,'status':evaluation['status'],'notice':NOTICE}
