"""Train actual-data models, audit every dataset, and publish immutable JSON.

Run: .venv-ml/bin/python -m pipeline.train_models
Protocol fixed before fitting: docs/AI-구현-검증계획.md. No app/database mutation.
"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import csv
import hashlib
import json
import os
import tempfile
import numpy as np
import scipy
from scipy import stats
import sklearn
from sklearn.ensemble import RandomForestRegressor
from backend.ml_models import forest_predict,features,TYPES

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/research'
SEED=20260919
CONFIG=dict(n_estimators=64,max_depth=6,min_samples_leaf=40,random_state=SEED,n_jobs=2)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read_csv(path):
    with path.open(encoding='utf-8',newline='') as f: return list(csv.DictReader(f))


def inventory():
    manifest=json.loads((DATA/'manifest.json').read_text())
    originals=[]
    for ds in manifest['datasets']:
        for f in ds['files']:
            raw=(DATA/f['path']).read_bytes()
            if sha(raw)!=f['sha256']: raise ValueError('Original changed: '+f['path'])
            if 'MOESM1' in f['path']: role='EXCLUDED_SYNTHETIC_NOT_REAL_TRAJECTORY'
            elif 'MOESM2' in f['path']: role='EXCLUDED_FITTED_ON_TARGET_LEAKAGE'
            elif ds['id']=='cyprus_exercise_2022': role='EXTERNAL_EXERCISE_QA_NOT_LOST_PERSON_TRAINING'
            elif 'MOESM3' in f['path']: role='REAL_ENDPOINT_TRAINING'
            elif f['path'].endswith('SARsearchertracks.csv'): role='SEARCHER_SPEED_TRAINING'
            else: role='PROVENANCE_AND_DOCUMENTATION'
            originals.append({**f,'role':role,'dataset':ds['id'],'license':ds.get('license')})
    prepared=[{'path':str(p.relative_to(ROOT)),'sha256':sha(p.read_bytes())} for p in sorted((DATA/'prepared').glob('*.csv'))]
    return {'originals':originals,'prepared':prepared,'mixing_real_and_synthetic':False}


def fit_endpoint():
    rows=read_csv(DATA/'prepared/incident_endpoints.csv')
    d=np.array([float(r['straight_line_distance_m']) for r in rows])
    ids=[r['incident_index'] for r in rows]
    assert len(set(ids))==len(rows) and all(r['row_kind']=='REAL_INCIDENT_ENDPOINTS' for r in rows)
    assert np.isfinite(d).all() and (d>0).all()
    spatial=np.array([f"{np.floor(float(r['IPP_lat'])):.0f}:{np.floor(float(r['IPP_lon'])):.0f}" for r in rows])
    evaluations={};models={}
    for name,family in [('lognorm',stats.lognorm),('rayleigh',stats.rayleigh)]:
        schemes={}
        for kind,groups in [('leave_one_case_out',np.array(ids)),('one_degree_spatial_block',spatial)]:
            folds=[]
            for group in sorted(set(groups)):
                train=groups!=group;test=~train
                p=family.fit(d[train],floc=0)
                for i in np.flatnonzero(test):
                    radii=family.ppf([.5,.8,.95],*p)
                    folds.append({'test_incident':ids[i],'withheld_group':group,
                                  'train_incidents':[ids[j] for j in np.flatnonzero(train)],
                                  'actual_m':float(d[i]),'nll':float(-family.logpdf(d[i],*p)),
                                  'radius_m':radii.tolist(),'inside':(d[i]<=radii).tolist()})
            schemes[kind]={'nll':float(np.mean([x['nll'] for x in folds])),
                           'coverage':np.mean([x['inside'] for x in folds],axis=0).tolist(),
                           'mean_radius_m':np.mean([x['radius_m'] for x in folds],axis=0).tolist(),
                           'cases':folds}
        evaluations[name]=schemes
        models[name]={'params':[float(v) for v in family.fit(d,floc=0)]}
    summary={'cases':len(rows),'quantiles':[.5,.8,.95],
             'models':{n:{k:{a:b for a,b in v.items() if a!='cases'} for k,v in e.items()} for n,e in evaluations.items()},
             'scope':'SELECTED_US_HIKER_ENDPOINT_DISTANCE_NOT_TIME_POSITION',
             'straight_line_under_1km':int((d<1000).sum())}
    return {'kind':'endpoint_distance','unit':'m','reference_model':'lognorm','models':models}, {'summary':summary,'details':evaluations}


def export_forest(forest):
    trees=[]
    for estimator in forest.estimators_:
        t=estimator.tree_
        trees.append({'left':t.children_left.tolist(),'right':t.children_right.tolist(),
                      'feature':t.feature.tolist(),'threshold':t.threshold.tolist(),'value':t.value[:,0,0].tolist()})
    return trees


def baseline(rows):
    values={}
    all_tracks=defaultdict(list)
    for r in rows: all_tracks[r['track_id']].append(float(r['speed_mps']))
    fallback=float(np.median([np.median(v) for v in all_tracks.values()]))
    for kind in TYPES:
        tracks={r['track_id'] for r in rows if r['search_type']==kind}
        values[kind]=float(np.median([np.median(all_tracks[t]) for t in tracks])) if tracks else fallback
    return values


def fit_speed():
    raw=read_csv(DATA/'prepared/searcher_segments.csv');rows=[];excluded=Counter()
    for r in raw:
        if r['quality_flag']!='CANDIDATE': excluded[r['quality_flag']]+=1;continue
        try:g=float(r['grade'])
        except ValueError:g=float('nan')
        if not np.isfinite(g):excluded['GRADE_MISSING_NONFINITE']+=1;continue
        if abs(g)>1:excluded['ABS_GRADE_OVER_1']+=1;continue
        rows.append(r)
    X=np.vstack([features([float(r['grade'])],r['search_type'])[0] for r in rows])
    y=np.array([float(r['speed_mps']) for r in rows]);assert np.isfinite(y).all() and (y>=0).all()
    groups=np.array([r['team_id'] for r in rows]);tracks=np.array([r['track_id'] for r in rows])
    counts=Counter(tracks);weights=np.array([1/counts[t] for t in tracks]);weights*=len(rows)/weights.sum()
    predictions=np.zeros(len(rows));simple=np.zeros(len(rows));folds=[]
    for group in sorted(set(groups)):
        train=groups!=group;test=~train
        forest=RandomForestRegressor(**CONFIG).fit(X[train],y[train],sample_weight=weights[train])
        predictions[test]=forest.predict(X[test])
        medians=baseline([r for i,r in enumerate(rows) if train[i]])
        simple[test]=[medians[rows[i]['search_type']] for i in np.flatnonzero(test)]
        folds.append({'test_team':group,'train_teams':sorted(set(groups[train])),
                      'test_tracks':sorted(set(tracks[test]))})
        print('Speed held-out team',group,'done',flush=True)
    track_metrics=[]
    for t in sorted(set(tracks)):
        m=tracks==t
        track_metrics.append({'track_id':t,'team_id':groups[m][0],'rows':int(m.sum()),
                              'forest_mae_mps':float(np.mean(np.abs(predictions[m]-y[m]))),
                              'baseline_mae_mps':float(np.mean(np.abs(simple[m]-y[m])))})
    rf_mae=float(np.mean([t['forest_mae_mps'] for t in track_metrics]))
    base_mae=float(np.mean([t['baseline_mae_mps'] for t in track_metrics]))
    forest=RandomForestRegressor(**CONFIG).fit(X,y,sample_weight=weights)
    model={'kind':'searcher_speed_forest','features':['abs_grade','is_hasty','is_sweep','is_paired'],
           'grade_domain':[-1,1],'unit':'m/s','config':CONFIG,'trees':export_forest(forest),
           'baseline_mps':baseline(rows),'recommended_engine':'random_forest' if rf_mae<base_mae else 'type_median'}
    exported=forest_predict(model,X)
    max_error=float(np.max(np.abs(exported-forest.predict(X))))
    assert max_error<1e-10, ('JSON export mismatch',max_error)
    # Residual quantiles are empirical OOF diagnostics, NOT calibrated deployment intervals.
    summary={'raw_segments':len(raw),'used_segments':len(rows),'tracks':len(set(tracks)),
             'withheld_team_groups':len(set(groups)),'excluded':dict(excluded),
             'forest_track_macro_mae_mps':rf_mae,'baseline_track_macro_mae_mps':base_mae,
             'oof_error_q90_mps':float(np.quantile(np.abs(predictions-y),.9)),
             'json_export_max_error':max_error,'recommended_engine':model['recommended_engine'],
             'scope':'SEARCHER_SPEED_NOT_SUBJECT_SPEED_OR_POD','selection':'EXPLORATORY_CV_NOT_FINAL_EXTERNAL_TEST'}
    oof=[{'source_row':int(r['source_row']),'track_id':r['track_id'],'team_id':r['team_id'],
          'actual_mps':float(y[i]),'forest_mps':float(predictions[i]),'baseline_mps':float(simple[i])} for i,r in enumerate(rows)]
    return model,{'summary':summary,'folds':folds,'per_track':track_metrics,'held_out_predictions':oof}


def check_exercise():
    rows=read_csv(DATA/'prepared/exercise_traces.csv');people=defaultdict(list)
    for r in rows:people[r['responder_id']].append(r)
    details=[]
    for person,points in sorted(people.items()):
        points.sort(key=lambda r:r['local_timestamp']);flags=Counter();speeds=[]
        for a,b in zip(points,points[1:]):
            seconds=(datetime.fromisoformat(b['local_timestamp'])-datetime.fromisoformat(a['local_timestamp'])).total_seconds()
            if seconds<=0:flags['NONINCREASING_TIME']+=1;continue
            if seconds>120:flags['GAP_OVER_120S']+=1;continue
            lat1,lon1,lat2,lon2=np.radians([float(a['latitude']),float(a['longitude']),float(b['latitude']),float(b['longitude'])])
            q=np.sin((lat2-lat1)/2)**2+np.cos(lat1)*np.cos(lat2)*np.sin((lon2-lon1)/2)**2
            v=6371008.8*2*np.arcsin(np.sqrt(np.clip(q,0,1)))/seconds
            if v>3:flags['SPEED_OVER_3MPS']+=1;continue
            speeds.append(float(v))
        details.append({'responder':person,'points':len(points),'accepted_segments':len(speeds),'flags':dict(flags),
                        'mean_speed_mps':float(np.mean(speeds)) if speeds else None,
                        'median_speed_mps':float(np.median(speeds)) if speeds else None})
    return {'summary':{'observations':len(rows),'responders':len(people),'exercises':1,
                       'role':'QA_ONLY_NO_INCIDENT_GENERALIZATION','timezone':'UNKNOWN'},'details':details}


def main():
    checked=inventory();print('Original hashes verified',flush=True)
    endpoint,endpoint_eval=fit_endpoint();print('Endpoint training and held-out evaluation complete',flush=True)
    speed,speed_eval=fit_speed();exercise=check_exercise()
    model_root=ROOT/'models';model_root.mkdir(exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='.training-',dir=model_root))
    content={'endpoint.json':endpoint,'searcher_speed.json':speed,
             'evaluation.json':{'endpoint':endpoint_eval,'searcher_speed':speed_eval},
             'data_inventory.json':checked,'exercise_validation.json':exercise}
    hashes={}
    for name,value in content.items():
        raw=canonical(value);(folder/name).write_bytes(raw);hashes[name]=sha(raw)
    manifest={'format':'cheonrajimang-models-v1','trained_at':datetime.now(timezone.utc).isoformat(),
              'seed':SEED,'files':hashes,'versions':{'numpy':np.__version__,'scipy':scipy.__version__,'sklearn':sklearn.__version__},
              'code':{p:sha((ROOT/p).read_bytes()) for p in ['pipeline/train_models.py','backend/ml_models.py','docs/AI-구현-검증계획.md']},
              'limitations':['미국 하이커 65건은 선택된 발견점 표본이며 시간별 위치 정답이 아님.',
                             '수색대 속도는 실종자 속도·탐지확률이 아님. GPS 경사→DEM 경사 이전은 검증 전.',
                             'team_id 분할로 사건·개인·지형의 모든 중복을 보장할 수 없음.',
                             '시설·낮밤·교통·POD 행동 정답 미확보. 현장 성능 검증 전.',
                             '교차검증에서 선택한 권장 모델 성능은 탐색적 평가이며 외부 확정 성능이 아님.']}
    raw=canonical(manifest);bundle_id=sha(raw);(folder/'manifest.json').write_bytes(raw)
    os.replace(folder,model_root/bundle_id)
    fd,tmp=tempfile.mkstemp(prefix='.current-',dir=model_root)
    with os.fdopen(fd,'wb') as f:f.write(canonical({'bundle_id':bundle_id}))
    os.replace(tmp,model_root/'current.json')
    print(json.dumps({'bundle_id':bundle_id,'speed':speed_eval['summary'],'endpoint':endpoint_eval['summary']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
