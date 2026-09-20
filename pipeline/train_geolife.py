"""Fit and evaluate the frozen, person-held-out GeoLife Markov protocol."""
from pathlib import Path
from datetime import datetime,timezone
import json
import hashlib
import numpy as np
from .prepare_geolife import ROOT,OUT,PROTOCOL,digest

MODELS=ROOT/'data/models/walking_reference'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def fit(rows,person):
    users,counts=np.unique(person,return_counts=True)
    sizes=dict(zip(users,counts));w=np.array([1000/sizes[p] for p in person])
    previous=rows[:,1].astype(int);target=rows[:,2].astype(int)
    raw=np.zeros((6,54));np.add.at(raw,(previous,target),w)
    conditional=raw+.5;conditional/=conditional.sum(axis=1,keepdims=True)
    baseline=raw.sum(axis=0)+.5;baseline/=baseline.sum()
    initial=np.bincount(previous,weights=w,minlength=6)+.5;initial/=initial.sum()
    representatives=[]
    for k in range(6):
        chosen=(target//9)==k
        if not chosen.any():raise ValueError(f'No training data in speed bin {k}; protocol requires review, not silent fallback')
        representatives.append(float(np.average(rows[chosen,4],weights=w[chosen])))
    return {'format':'cheonrajimang-ordinary-walk-v1','step_seconds':30,
            'speed_edges_mps':[0,.25,.75,1.25,1.75,2.5,4],
            'speed_representatives_mps':representatives,'initial_speed_prob':initial.tolist(),
            'transition_prob':conditional.tolist(),'baseline_prob':baseline.tolist(),
            'training_people':len(users),'training_rows':len(rows),
            'fit':'Person-weighted maximum likelihood with Dirichlet 0.5 smoothing',
            'domain':'ORDINARY_WALKING_NOT_LOST_PEOPLE','redistribution_allowed':False}

def score(model,rows,person):
    q=np.array(model['transition_prob'])[rows[:,1].astype(int)]
    b=np.broadcast_to(model['baseline_prob'],q.shape)
    y=rows[:,2].astype(int);idx=np.arange(len(y))
    speeds=np.repeat(model['speed_representatives_mps'],9)
    per=[]
    metrics={}
    for label,prob in [('model',q),('baseline',b)]:
        metrics[label+'_nll']=-np.log(prob[idx,y])
        metrics[label+'_brier']=np.square(prob).sum(axis=1)-2*prob[idx,y]+1
        metrics[label+'_speed_mae_mps']=np.abs(prob@speeds-rows[:,4])
    for p in np.unique(person):
        mask=person==p
        per.append({'person':int(p),'rows':int(mask.sum()),**{k:float(v[mask].mean()) for k,v in metrics.items()}})
    summary={k:float(np.mean([r[k] for r in per])) for k in metrics}
    summary.update(people=len(per),rows=len(rows))
    return {'summary':summary,'per_person':per}

def main():
    inventory=json.loads((OUT/'inventory.json').read_text())
    if digest(PROTOCOL)!=inventory['protocol_sha256'] or digest(OUT/'walking_samples.npz')!=inventory['prepared_sha256']:
        raise ValueError('Frozen inputs changed')
    with np.load(OUT/'walking_samples.npz',allow_pickle=False) as data:rows,person=data['rows'],data['person']
    membership=np.array([inventory['split_by_person'][str(int(p))] for p in person])
    mask=membership=='train';model=fit(rows[mask],person[mask])
    results={s:score(model,rows[membership==s],person[membership==s]) for s in ['train','validation','test']}
    test=results['test'];improvements=np.array([r['baseline_nll']-r['model_nll'] for r in test['per_person']])
    bootstrap=np.random.default_rng(20260920).choice(improvements,size=(2000,len(improvements)),replace=True).mean(axis=1)
    ci=np.quantile(bootstrap,[.025,.975]).tolist()
    relative=float(improvements.mean()/test['summary']['baseline_nll'])
    adopt=inventory['people']>=30 and len(improvements)>=6 and relative>=.01 and ci[0]>0
    evaluation={'created_at':datetime.now(timezone.utc).isoformat(),'status':'ADOPTED_30S_ORDINARY_WALKING_ONLY' if adopt else 'NOT_ADOPTED',
                'primary_relative_nll_improvement':relative,'paired_person_bootstrap_nll_gain_95ci':ci,
                'bootstrap_repetitions':2000,'bootstrap_seed':20260920,'splits':results,
                'long_horizon_validated':False,'missing_person_validated':False,
                'note':'Next-step evaluation conditions on observed previous speed. Closed-loop rollouts do not observe it and have no validated long-horizon accuracy.'}
    model['evaluation_status']=evaluation['status']
    payload=canonical(model);model_id=hashlib.sha256(payload).hexdigest()
    folder=MODELS/model_id;folder.mkdir(parents=True,exist_ok=False)
    with (folder/'model.json').open('xb') as f:f.write(payload)
    for name,content in [('evaluation.json',evaluation),('inventory.json',inventory)]:
        with (folder/name).open('xb') as f:f.write(canonical(content))
    receipt={'model_id':model_id,'created_at':evaluation['created_at'],'files':{n:digest(folder/n) for n in ['model.json','evaluation.json','inventory.json']},
             'source_sha256':inventory['archive_sha256'],'prepared_sha256':inventory['prepared_sha256'],
             'protocol_sha256':digest(PROTOCOL),'trainer_sha256':digest(__file__),'preparer_sha256':inventory['preparer_sha256'],
             'numpy_version':np.__version__,'license':'MSR-LA NON-COMMERCIAL; NO REDISTRIBUTION',
             'publication':'LOCAL_RESEARCH_ONLY; excluded from mission handoff exports'}
    with (folder/'receipt.json').open('xb') as f:f.write(canonical(receipt))
    with (MODELS/'current.json').open('xb') as f:f.write(canonical({'model_id':model_id,'receipt_sha256':digest(folder/'receipt.json')}))
    print(json.dumps({'model_id':model_id,'path':str(folder),'status':evaluation['status'],'test':test['summary'],
                      'relative_nll_improvement':relative,'bootstrap_ci':ci},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
