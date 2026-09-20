"""Confirm timestamp-field agreement for conflicting walking coordinates."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import io
import json
import zipfile
import numpy as np
from .prepare_geolife import SOURCE,SOURCE_HASH,OUT,digest,read_labels,label_points

def main():
    if digest(SOURCE)!=SOURCE_HASH:raise ValueError('Source changed')
    count=Counter();seen=set();people=set()
    with zipfile.ZipFile(SOURCE) as z:
        labels={n.split('/')[-2]:read_labels(z.read(n)) for n in z.namelist() if n.endswith('/labels.txt')}
        for name in sorted(z.namelist()):
            if not name.endswith('.plt'):continue
            person=name.split('/')[-3]
            if person not in labels or not any(l[2]=='walk' for l in labels[person]):continue
            raw=z.read(name);key=hashlib.sha256(raw).digest()
            if key in seen:continue
            seen.add(key)
            a=np.loadtxt(io.BytesIO(raw),delimiter=',',skiprows=6,usecols=(0,1,4),ndmin=2)
            ts=np.rint((a[:,2]-25569)*86400);ids=label_points(ts,labels[person])
            selected=np.flatnonzero((np.diff(ts)==0)&np.any(a[1:,:2]!=a[:-1,:2],axis=1)&(ids[:-1]>=0)&(ids[:-1]==ids[1:]))
            if not len(selected):continue
            people.add(person);lines=raw.decode().splitlines()[6:]
            for k in selected:
                pair=[]
                for ix in (k,k+1):
                    cols=lines[ix].split(',')
                    text_time=datetime.fromisoformat(cols[5]+'T'+cols[6]).replace(tzinfo=timezone.utc).timestamp()
                    pair.append(text_time);count['point_comparisons']+=1
                    count['text_numeric_mismatch_over_1s']+=int(abs(text_time-ts[ix])>1)
                count['different_text_time_pairs']+=int(pair[0]!=pair[1])
                count['same_text_time_pairs']+=int(pair[0]==pair[1])
    result={'source_sha256':SOURCE_HASH,'code_sha256':digest(__file__),'affected_people':len(people),'stats':dict(count),
            'interpretation':'Conflicting coordinates share both timestamp fields. Sub-second quantization or other recording effects remain possible; this is not proof that all coordinates are wrong. No invented timestamps or model changes.'}
    with (OUT/'conflict_time_check.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
