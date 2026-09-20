"""Hash-verified, non-executable model artifacts. No sklearn runtime dependency."""
from pathlib import Path
import hashlib
import json
import re
import numpy as np
from . import core

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / 'models'
TYPES = ('hasty', 'sweep', 'paired')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def features(grade, search_type):
    if search_type not in TYPES:
        raise ValueError('지원되는 수색 방식이 아닙니다.')
    grade = np.asarray(grade, dtype=float).reshape(-1)
    if not np.isfinite(grade).all() or (np.abs(grade)>1).any():
        raise ValueError('속도 모델의 경사 입력 범위는 -1~1입니다.')
    return np.column_stack([np.abs(grade)] + [np.full(len(grade), float(search_type==k)) for k in TYPES])


def forest_predict(model, X):
    """Match sklearn's float32 tree traversal; JSON cannot execute Python code."""
    X = np.asarray(X, dtype=np.float32)
    if X.ndim!=2 or X.shape[1]!=4 or not np.isfinite(X).all():
        raise ValueError('유한한 4개 속도 특성이 필요합니다.')
    out = np.zeros(len(X))
    trees = model['trees']
    if not 1<=len(trees)<=256:
        raise ValueError('잘못된 트리 개수')
    for tree in trees:
        left,right,feat,threshold,values = [np.asarray(tree[k]) for k in ('left','right','feature','threshold','value')]
        n = len(left)
        if not all(len(a)==n for a in (right,feat,threshold,values)) or n>4096:
            raise ValueError('잘못된 트리 구조')
        nodes = np.zeros(len(X),dtype=int)
        for _ in range(32):
            ids = np.flatnonzero(left[nodes]>=0)
            if not len(ids): break
            at = nodes[ids]; fs = feat[at].astype(int)
            if (fs<0).any() or (fs>=4).any(): raise ValueError('잘못된 특성 번호')
            nodes[ids] = np.where(X[ids,fs]<=threshold[at],left[at],right[at])
            if (nodes<0).any() or (nodes>=n).any(): raise ValueError('잘못된 노드 번호')
        else: raise ValueError('트리 깊이 제한 초과')
        out += values[nodes]
    out /= len(trees)
    if not np.isfinite(out).all() or (out<0).any(): raise ValueError('잘못된 예측 결과')
    return out


def current_id():
    try: return json.loads((MODEL_ROOT/'current.json').read_text())['bundle_id']
    except (OSError,KeyError,ValueError) as e:
        raise ValueError('학습 모델이 준비되지 않았습니다.') from e


def load_bundle(bundle_id=None):
    bundle_id = bundle_id or current_id()
    if not isinstance(bundle_id,str) or not re.fullmatch(r'[a-f0-9]{64}',bundle_id):
        raise ValueError('모델 식별자가 올바르지 않습니다.')
    try:
        folder = MODEL_ROOT/bundle_id
        raw = (folder/'manifest.json').read_bytes()
        if digest(raw)!=bundle_id: raise ValueError('모델 manifest 해시 불일치')
        manifest = json.loads(raw)
        names = {'endpoint.json','searcher_speed.json','evaluation.json','data_inventory.json','exercise_validation.json'}
        if set(manifest['files'])!=names: raise ValueError('모델 구성 파일 불일치')
        data = {}
        for name in names:
            content = (folder/name).read_bytes()
            if digest(content)!=manifest['files'][name]: raise ValueError('모델 파일 손상: '+name)
            data[name.removesuffix('.json')] = json.loads(content)
        if manifest.get('format')!='cheonrajimang-models-v1': raise ValueError('지원되지 않는 모델 형식')
        return {'bundle_id':bundle_id,'manifest':manifest,**data}
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as e:
        raise ValueError('학습 모델을 읽을 수 없습니다.') from e


def speed(bundle, grade, search_type='sweep', engine='recommended'):
    X=features(grade,search_type)
    model=bundle['searcher_speed']
    chosen=model['recommended_engine'] if engine=='recommended' else engine
    if chosen=='random_forest': result=forest_predict(model,X)
    elif chosen=='type_median': result=np.full(len(X),model['baseline_mps'][search_type])
    else: raise ValueError('지원되지 않는 속도 모델')
    # Raw predictions are preserved; operational floors belong in the planner.
    return result


def endpoint_options(bundle):
    options = [{'key':'lognorm','label':'미국 하이커 65건, 기존 거리 모델',
                'cases':bundle['evaluation']['endpoint']['summary']['cases'],
                'scope':'발견점 거리 참고, 시간 예측 아님'}]
    extra=bundle['endpoint']['models'].get('yosar_interval_lognorm')
    if extra and core.NONCOMMERCIAL_ENABLED:
        options.append({'key':'yosar_interval_lognorm','label':f'YOSAR {extra["cases"]}개 사건 키, 좌표 오차 반영',
                        'cases':extra['cases'],'scope':'별도 표본, 시간 예측 아님',
                        'license':extra['license'],'attribution':extra['attribution'],
                        'license_url':extra['license_url'],
                        'uncertainty_units_inferred':extra['uncertainty_units_inferred']})
    return options


def research_status(path,key):
    try: return json.loads((ROOT/path).read_text())[key]
    except (OSError,KeyError,ValueError): return None


def components(bundle):
    """Inventory of what is learned, what is research-only, and what is assumed.

    Shown in the app and written into hand-over packages so that no reader
    mistakes an assumption for a trained result.
    """
    e=bundle['evaluation'];sp=e['searcher_speed']['summary'];ep=e['endpoint']['summary']
    lognorm=ep['models']['lognorm']['leave_one_case_out']
    items=[
        {'key':'distance_prior','name':'거리 시나리오(A) 발견 거리 분포','status':'LEARNED',
         'detail':f"실제 실종 하이커 {ep['cases']}건의 IPP→발견점 거리에 lognormal 적합. 사건 제외 교차검증 NLL {lognorm['nll']:.2f}, 80% 반경 포함률 {lognorm['coverage'][1]*100:.1f}%.",
         'used_in':'새 임무의 기본 거리 시나리오(도달 가능 반경으로 절단)과 AI 계획의 발견점 참고 모드',
         'caveat':'미국 하이커 표본이며 원 논문이 1 km 미만 사건을 대부분 제외해 근거리 확률을 낮게 볼 수 있습니다. 근거리는 지형 시나리오 B와 B′가 담당합니다.'},
        {'key':'searcher_speed','name':'수색대 이동 속도','status':'LEARNED',
         'detail':f"수색대 GPS {sp['tracks']}트랙 {sp['used_segments']:,}구간으로 Random Forest 회귀. 팀 단위 교차검증 MAE {sp['forest_track_macro_mae_mps']:.3f} m/s (방식별 중앙값 기준 {sp['baseline_track_macro_mae_mps']:.3f}).",
         'used_in':'AI 계획의 경로 이동시간과 왕복 시간 계산(Dijkstra 간선 비용)','caveat':'입력은 |경사|와 수색 방식뿐이며 실종자 속도나 탐지확률이 아닙니다.'},
        {'key':'yosar_endpoint','name':'YOSAR 발견점 구간 모델','status':'LEARNED_SEPARATE' if core.NONCOMMERCIAL_ENABLED else 'DISABLED_BY_PROFILE',
         'detail':'하이커 단일 발견점 132개 사건 키의 좌표 오차 구간 lognormal. 공간 분리 구간 NLL 2.645(기존 모델 3.762).',
         'used_in':'AI 계획의 발견점 참고 모드에서 명시 선택 시','caveat':'CC BY-NC-SA 4.0 비상업 연구 참고. 기본 지도와 합치지 않습니다.'},
        {'key':'terrain_endpoint','name':'실제 사건 지형 계수 모델','status':research_status('data/research/endpoint_terrain_2026/latest_run.json','adoption') and research_status('data/research/endpoint_terrain_2026/latest_run.json','adoption').get('status','UNKNOWN') or 'UNKNOWN',
         'detail':'65건 + 독립 GLO-90 지형 타일 31개로 지형 계수 학습. 40 km 완충 공간 평가 NLL 7.628→7.613, 개선 신뢰범위가 0을 포함해 사전 기준 미달.',
         'used_in':'앱 미반영(연구 결과 보존)','caveat':'채택 기준을 사후에 낮추지 않았습니다.'},
        {'key':'walking_reference','name':'일반 보행 다음 30초 모델(GeoLife)','status':(research_status('data/models/walking_reference/current.json','model_id') and 'NOT_ADOPTED') or 'UNKNOWN',
         'detail':'37,469 표본 학습, 사람별 분리 평가 NLL 2.587→2.001. 평가 인원 5명으로 사전 인원 기준 미달.',
         'used_in':'보행 학습 실험실 화면에서만 실행' if core.NONCOMMERCIAL_ENABLED else '배포 프로필에서 비활성','caveat':'실종자 행동이 아닌 일반 보행 자료(MSR-LA 비상업).'},
        {'key':'terrain_walk','name':'지형 이동 시뮬레이션(B, B′)','status':'PHYSICS_ASSUMPTION',
         'detail':'Tobler 보행 함수 × 토지피복 및 도로 계수 × 이동성 0.42, 입자 20,000개. B′는 경로 추종, 내리막, 시설 선호 규칙 추가.',
         'used_in':'모든 임무의 지형 및 행동 시나리오와 시간 추정','caveat':'계수는 팀 가정이며 실종자 관측으로 보정되지 않았습니다.'},
        {'key':'detection','name':'탐지폭 8, 15, 22 m / 팀 밴드','status':'ASSUMPTION',
         'detail':'수색 경로 1 m당 탐지폭 W의 노력을 셀에 배분(POD = 1−e^−C). 팀 n명, 간격 s는 n×W를 (n−1)s 폭에 분산.',
         'used_in':'GPX 반영과 AI 계획의 POD 범위','caveat':'실측 POD 보정 전. 범위는 신뢰구간이 아닙니다.'},
        {'key':'row','name':'모형 밖 잔여확률(ROW)','status':'ASSUMPTION',
         'detail':f"기본 {core.ASSUMPTIONS['row_share_default']*100:.0f}%를 지도와 도보 가설 밖(차량, 교통, 오인 신고)에 예약. 수색 미발견이 쌓이면 자동으로 커집니다.",
         'used_in':'모든 새 임무','caveat':'수색 이론의 관행적 항목이며 값은 조정 가능한 팀 가정입니다.'},
        {'key':'daylight_facility','name':'야간 이동 계수와 시설 선호','status':'ASSUMPTION',
         'detail':'태양고도 기반 야간 계수(기본 0.6)와 OSM 시설 630곳까지의 지형 비용 선호.',
         'used_in':'해당 옵션을 켠 임무의 B와 B′','caveat':'검증 전 팀 가정.'},
    ]
    return items


def public_status():
    try:
        b=load_bundle()
        e=b['evaluation']
        return {'available':True,'status':'TRAINED_EXPERIMENTAL','bundle_id':b['bundle_id'],
                'trained_at':b['manifest']['trained_at'],'endpoint':e['endpoint']['summary'],
                'endpoint_options':endpoint_options(b),
                'yosar_endpoint':e.get('yosar_endpoint',{}).get('summary') if core.NONCOMMERCIAL_ENABLED else None,
                'searcher_speed':e['searcher_speed']['summary'],
                'recommended_speed_engine':b['searcher_speed']['recommended_engine'],
                'exercise':b['exercise_validation']['summary'],
                'limitations':b['manifest']['limitations'],
                'components':components(b),'deploy_profile':core.DEPLOY_PROFILE,
                'default_distance_model':core.DISTANCE_MODELS[0]}
    except ValueError as e:
        return {'available':False,'status':'UNAVAILABLE','reason':str(e)}
