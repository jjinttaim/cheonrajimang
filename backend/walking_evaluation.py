"""Read-only aggregate evaluation; never serve private trajectory/window records."""
from pathlib import Path
import hashlib
import json
from fastapi import APIRouter,HTTPException,Query
from .walking_reference import load

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/ml/geolife'
router=APIRouter()

def public_report(model_id):
    try:
        current,_,_,inventory=load()
        if current!=model_id:raise ValueError('모델이 변경됐습니다. 다시 불러오세요.')
        receipt=json.loads((DATA/'horizon_receipt.json').read_bytes())
        raw=(DATA/'horizon_evaluation.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt['report_sha256']:raise ValueError('시간 예측 평가 파일 무결성 오류')
        report=json.loads(raw)
        if report['model_id']!=model_id or receipt['model_id']!=model_id:raise ValueError('평가와 모델 버전 불일치')
        if report['prepared_sha256']!=inventory['prepared_sha256'] or receipt['prepared_sha256']!=inventory['prepared_sha256']:
            raise ValueError('평가 데이터 버전 불일치')
        horizons=[]
        for h in report['horizons_seconds']:
            r=report['main'][str(h)];macro=r['macro']
            horizons.append({'seconds':h,'model_energy_m':macro['model_energy_m']['mean'],
                             'ballistic_energy_m':macro['ballistic_energy_m']['mean'],
                             'stationary_energy_m':macro['stationary_energy_m']['mean'],
                             'radius_80_m':r['model_radius_m']['80'],
                             'coverage_80_macro':macro['model_coverage_80']['mean'],
                             'coverage_80_person_median':macro['model_coverage_80']['median'],
                             'gain_vs_ballistic_95ci_m':r['gains_vs_baselines']['ballistic']['person_bootstrap_95ci_m']})
        return {'model_id':model_id,'report_sha256':receipt['report_sha256'],
                'people':report['people'],'windows':report['windows'],'horizons':horizons,
                'information':report['information'],'status':report['adoption'],
                'long_horizon_validated':False,'missing_person_validated':False,
                'note':'동일 평가자 5명의 후속 점검입니다. 실종자 현장 검증이 아니며 80% 반경은 검증된 신뢰구간이 아닙니다.',
                'metric_note':'분포 점수(에너지 점수)는 작을수록 좋지만 실제 위치 오차나 발견률은 아닙니다.'}
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as e:raise ValueError('시간 예측 평가를 불러올 수 없습니다.') from e

@router.get('/api/ai/walking/horizon-evaluation')
def status(model_id:str=Query(pattern='^[a-f0-9]{64}$')):
    try:return public_report(model_id)
    except ValueError as e:raise HTTPException(503,str(e))
