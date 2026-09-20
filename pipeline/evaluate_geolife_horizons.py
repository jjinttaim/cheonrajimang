"""Frozen v1, position-only closed-loop evaluation. No model fitting or selection."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import numpy as np
from .prepare_geolife import ROOT,OUT,digest
from backend.walking_reference import load,rollout

PLAN=ROOT/'docs/AI-GeoLife-시간예측-검증계획.md'
EXPECTED_MODEL='a0d0ebfd08ac11f565e119ac44d9b92dd76fb286e6aeae780b3c130c5b6390b1'
HORIZONS=(30,120,300)
PARTICLES=32768

def windows(rows,person,track,split_by_person):
    """Build disjoint five-minute targets without crossing gaps or track boundaries."""
    ys=[];who=[];keys=[]
    for p in sorted(int(k) for k,s in split_by_person.items() if s=='test'):
        for tid in np.unique(track[person==p]):
            a=rows[(person==p)&(track==tid)]
            a=a[np.argsort(a[:,0],kind='stable')]
            connected=(np.diff(a[:,0])==30)&np.all(a[:-1,7:9]==a[1:,5:7],axis=1)
            cuts=np.r_[0,np.flatnonzero(~connected)+1,len(a)]
            for lo,hi in zip(cuts[:-1],cuts[1:]):
                for start in range(int(lo),int(hi)-9,10):
                    vec=a[start:start+10,7:9]
                    if not np.isfinite(vec).all():raise ValueError('Nonfinite target')
                    ys.append(np.cumsum(vec,axis=0)[[0,3,9]])
                    who.append(p)
                    # Keep exact window identifiers local; never expose them in status.
                    keys.append([p,int(tid),int(a[start,0])])
    if not ys:raise ValueError('No continuous five-minute held-out windows')
    return np.array(ys),np.array(who),keys

def ballistic(model,seconds,seed,particles):
    rng=np.random.default_rng(seed)
    initial=np.cumsum(model['initial_speed_prob']);initial[-1]=1
    state=np.searchsorted(initial,rng.random(particles),side='right')
    angle=rng.uniform(-np.pi,np.pi,particles)
    return np.array(model['speed_representatives_mps'])[state,None]*seconds*np.column_stack([np.cos(angle),np.sin(angle)])

def energy_score(sample,independent,targets):
    """Unbiased paired-MC ES estimate, not a clipped/renormalized histogram score."""
    for a in (sample,independent,targets):
        if a.ndim!=2 or a.shape[1]!=2 or not np.isfinite(a).all():raise ValueError('Expected finite 2D positions')
    if sample.shape!=independent.shape or not len(sample):raise ValueError('Independent samples must have the same nonzero count')
    correction=.5*np.linalg.norm(sample-independent,axis=1).mean()
    # Bounded allocation rather than targets × all particles × time in memory.
    values=[]
    for i in range(0,len(targets),16):
        values.extend(np.linalg.norm(sample[None,:,:]-targets[i:i+16,None,:],axis=2).mean(axis=1)-correction)
    return np.array(values)

def describe(a):
    return {'mean':float(np.mean(a)),'median':float(np.median(a)),
            'q25':float(np.quantile(a,.25)),'q75':float(np.quantile(a,.75))}

def summarize(values,person):
    per=[{'person':int(p),'windows':int((person==p).sum()),
          **{k:float(v[person==p].mean()) for k,v in values.items()}} for p in np.unique(person)]
    return {'macro':{k:describe(np.array([r[k] for r in per])) for k in values},'per_person':per}

def evaluate(model,targets,person,first_seed):
    out={}
    for index,seconds in enumerate(HORIZONS):
        y=targets[:,index,:];distance=np.linalg.norm(y,axis=1)
        a=rollout(model,seconds,first_seed,PARTICLES)[0]
        a2=rollout(model,seconds,first_seed+1,PARTICLES)[0]
        b=ballistic(model,seconds,first_seed,PARTICLES)
        b2=ballistic(model,seconds,first_seed+1,PARTICLES)
        ar=np.quantile(np.linalg.norm(a,axis=1),[.5,.8,.95])
        br=np.quantile(np.linalg.norm(b,axis=1),[.5,.8,.95])
        values={'model_energy_m':energy_score(a,a2,y),'ballistic_energy_m':energy_score(b,b2,y),
                'stationary_energy_m':distance}
        for j,q in enumerate((50,80,95)):
            values[f'model_coverage_{q}']=(distance<=ar[j]).astype(float)
            values[f'ballistic_coverage_{q}']=(distance<=br[j]).astype(float)
        s=summarize(values,person)
        improvements={}
        for baseline in ('ballistic','stationary'):
            delta=np.array([r[f'{baseline}_energy_m']-r['model_energy_m'] for r in s['per_person']])
            boot=np.random.default_rng(20260924).choice(delta,size=(2000,len(delta)),replace=True).mean(axis=1)
            improvements[baseline]={'macro_gain_m':float(delta.mean()),'person_bootstrap_95ci_m':np.quantile(boot,[.025,.975]).tolist()}
        out[str(seconds)]={**s,'model_radius_m':dict(zip(('50','80','95'),ar.tolist())),
                           'ballistic_radius_m':dict(zip(('50','80','95'),br.tolist())),
                           'observed_distance_m_window_weighted':describe(distance),
                           'gains_vs_baselines':improvements}
        print(json.dumps({'seed':first_seed,'seconds':seconds,'energy_m':{k:v['mean'] for k,v in s['macro'].items() if k.endswith('energy_m')},
                          'coverage_80':s['macro']['model_coverage_80']['mean']}),flush=True)
    return out

def main():
    mid,model,_,inventory=load()
    if mid!=EXPECTED_MODEL:raise ValueError('Wrong frozen model')
    if digest(OUT/'walking_samples.npz')!=inventory['prepared_sha256']:raise ValueError('Prepared data changed')
    target=OUT/'horizon_evaluation.json';receipt_path=OUT/'horizon_receipt.json'
    if target.exists() or receipt_path.exists():raise FileExistsError('Keep existing evaluation; do not overwrite')
    with np.load(OUT/'walking_samples.npz',allow_pickle=False) as d:
        targets,person,keys=windows(d['rows'],d['person'],d['track'],inventory['split_by_person'])
    main=evaluate(model,targets,person,20260920);repeat=evaluate(model,targets,person,20260922)
    sensitivity={}
    for h in main:
        a=main[h]['macro'];b=repeat[h]['macro'];names=['model_energy_m','ballistic_energy_m','stationary_energy_m']
        sensitivity[h]={'absolute_macro_energy_change_m':{n:abs(a[n]['mean']-b[n]['mean']) for n in names},
                        'same_ranking':sorted(names,key=lambda n:a[n]['mean'])==sorted(names,key=lambda n:b[n]['mean'])}
    report={'created_at':datetime.now(timezone.utc).isoformat(),'model_id':mid,'kind':'POST_V1_FROZEN_MODEL_CLOSED_LOOP_CHECK',
            'information':'POSITION_ONLY_NO_TRUE_VELOCITY_OR_FUTURE_INPUT','windows':len(targets),'people':len(np.unique(person)),
            'window_keys_local_only':keys,'window_keys_sha256':hashlib.sha256(json.dumps(keys,separators=(',',':')).encode()).hexdigest(),
            'prepared_sha256':inventory['prepared_sha256'],'particles':PARTICLES,'horizons_seconds':list(HORIZONS),
            'primary_horizon_seconds':300,'main_seeds':[20260920,20260921],'repeat_seeds':[20260922,20260923],
            'main':main,'repeat':repeat,'numerical_sensitivity':sensitivity,
            'adoption':'NOT_ADOPTED_UNCHANGED','long_horizon_validated':False,'missing_person_validated':False,
            'limitations':['Only five previously used held-out people, not new external validation',
                           'Only continuous five-minute GPS windows, a selected subset',
                           'Position-only initial speed/heading assumed, no terrain/night/POI/detection',
                           'Person bootstrap intervals are exploratory, not simultaneous or field evidence']}
    with target.open('x') as f:json.dump(report,f,indent=2,ensure_ascii=False,allow_nan=False)
    receipt={'model_id':mid,'report_sha256':digest(target),'protocol_sha256':digest(PLAN),'prepared_sha256':inventory['prepared_sha256'],
             'source_sha256':inventory['archive_sha256'],'code_sha256':{p:digest(ROOT/p) for p in
              ['pipeline/evaluate_geolife_horizons.py','backend/walking_reference.py','pipeline/prepare_geolife.py']},
             'status':'COMPLETED_NO_FITTING','license':'MSR-LA local non-commercial research, no redistribution'}
    with receipt_path.open('x') as f:json.dump(receipt,f,indent=2,ensure_ascii=False)
    print(json.dumps({'report':str(target),'people':report['people'],'windows':report['windows'],'adoption':report['adoption']}),flush=True)

if __name__=='__main__':main()
