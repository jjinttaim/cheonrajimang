"""Additional descriptive baseline, explicitly post-fit; changes no adoption gate."""
import json
import numpy as np
from .prepare_geolife import OUT,digest
from backend.walking_reference import load

def main():
    mid,model,evaluation,inventory=load()
    if digest(OUT/'walking_samples.npz')!=inventory['prepared_sha256']:raise ValueError('Data changed')
    with np.load(OUT/'walking_samples.npz',allow_pickle=False) as d:rows,person=d['rows'],d['person']
    results=[]
    for p in np.unique(person):
        if inventory['split_by_person'][str(int(p))]!='test':continue
        r=rows[person==p]
        results.append({'person':int(p),'rows':len(r),'mae_mps':float(np.abs(r[:,3]-r[:,4]).mean())})
    summary={'people':len(results),'persistence_mae_mps':float(np.mean([r['mae_mps'] for r in results])),
             'model_mae_mps':evaluation['splits']['test']['summary']['model_speed_mae_mps']}
    report={'model_id':mid,'prepared_sha256':inventory['prepared_sha256'],'code_sha256':digest(__file__),
            'kind':'POST_FIT_DESCRIPTIVE_NOT_SELECTION','summary':summary,'per_person':results,
            'note':'Last measured speed persists. This point-speed baseline outperforms Markov expected speed on this split; do not call the model best or validated.'}
    with (OUT/'persistence_check.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
