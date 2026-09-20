"""Read-only duplicate timestamp audit, independent of prediction performance."""
from collections import Counter
import hashlib
import io
import json
import zipfile
import numpy as np
from .prepare_geolife import SOURCE,OUT,digest,read_labels,label_points

def main():
    inventory=json.loads((OUT/'inventory.json').read_text())
    if digest(SOURCE)!=inventory['archive_sha256']:raise ValueError('Source changed')
    seen=set();stats=Counter();per={}
    with zipfile.ZipFile(SOURCE) as z:
        labels={n.split('/')[-2]:read_labels(z.read(n)) for n in z.namelist() if n.endswith('/labels.txt')}
        for name in sorted(z.namelist()):
            if not name.endswith('.plt'):continue
            person=name.split('/')[-3]
            if person not in labels or not any(l[2]=='walk' for l in labels[person]):continue
            raw=z.read(name);h=hashlib.sha256(raw).digest()
            if h in seen:continue
            seen.add(h)
            a=np.loadtxt(io.BytesIO(raw),delimiter=',',skiprows=6,usecols=(0,1,4),ndmin=2)
            ts=np.rint((a[:,2]-25569)*86400);lab=label_points(ts,labels[person]);dt=np.diff(ts)
            walk=(lab[:-1]>=0)&(lab[:-1]==lab[1:])
            eq=np.all(a[1:,:2]==a[:-1,:2],axis=1)
            masks={'walk_points':lab>=0,'same_time_same_coordinate':walk&(dt==0)&eq,
                   'same_time_conflicting_coordinate':walk&(dt==0)&~eq,'backwards_time':walk&(dt<0),
                   'same_raw_time_same_coordinate':walk&(np.diff(a[:,2])==0)&eq,
                   'within_second_moving_records':walk&(dt==0)&(np.diff(a[:,2])>0)&~eq}
            counts={k:int(v.sum()) for k,v in masks.items()}
            stats.update(counts);per.setdefault(person,Counter()).update(counts)
    report={'kind':'INPUT_QUALITY_AUDIT_NO_PREDICTIONS','source_sha256':digest(SOURCE),
            'v1_inventory_sha256':digest(OUT/'inventory.json'),'code_sha256':digest(__file__),
            'stats':dict(stats),'per_person':{p:dict(c) for p,c in per.items()},
            'prior_people':inventory['people'],'note':'Exact latitude/longitude equality, not a tolerance or inferred stationary label.'}
    with (OUT/'duplicate_audit.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({'stats':dict(stats),'people_with_exact_duplicate_records':sum(c['same_time_same_coordinate']>0 for c in per.values()),
                      'v1_excluded_people_with_exact_duplicates':sum(c['same_time_same_coordinate']>0 and str(int(p)) not in inventory['split_by_person'] for p,c in per.items())}),flush=True)

if __name__=='__main__':main()
