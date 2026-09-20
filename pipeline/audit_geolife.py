"""Post-fit descriptive source/QC audit; never changes splits or fitted model."""
from collections import Counter
from datetime import datetime,timezone
import io
import json
import zipfile
import numpy as np
from .prepare_geolife import SOURCE,OUT,read_labels,label_points,vectors,digest

def main():
    stats=Counter();seen=set()
    with zipfile.ZipFile(SOURCE) as z:
        labels={n.split('/')[-2]:read_labels(z.read(n)) for n in z.namelist() if n.endswith('/labels.txt')}
        for name in sorted(z.namelist()):
            if not name.endswith('.plt'):continue
            person=name.split('/')[-3]
            if person not in labels or not any(l[2]=='walk' for l in labels[person]):continue
            raw=z.read(name)
            import hashlib
            checksum=hashlib.sha256(raw).digest()
            if checksum in seen:continue
            seen.add(checksum)
            arr=np.loadtxt(io.BytesIO(raw),delimiter=',',skiprows=6,usecols=(0,1,4),ndmin=2)
            lat,lon,days=arr.T;ts=np.rint((days-25569)*86400);ids=label_points(ts,labels[person])
            valid=np.isfinite(arr).all(axis=1)&(np.abs(lat)<=90)&(np.abs(lon)<=180)
            dt=np.diff(ts);dist=np.linalg.norm(vectors(lat,lon),axis=1)
            walk=ids>=0;pair=walk[:-1]&walk[1:]&(ids[:-1]==ids[1:])
            stats['unique_input_tracks']+=1;stats['input_points']+=len(arr)
            stats['unambiguous_walk_points']+=int(walk.sum());stats['invalid_coordinate_or_number_points']+=int((~valid).sum())
            stats['same_walk_interval_pairs']+=int(pair.sum())
            stats['walk_pair_nonpositive_time']+=int((pair&(dt<=0)).sum())
            stats['walk_pair_gaps_over_15s']+=int((pair&(dt>15)).sum())
            stats['walk_pair_speed_over_4mps']+=int((pair&(dt>0)&(dist>4*dt)).sum())
            good=pair&(dt>0)&(dt<=15)&(dist<=4*dt)&valid[:-1]&valid[1:]
            stats['usable_connected_seconds']+=float(dt[good].sum())
            lines=raw.decode().splitlines()[6:]
            for line in (lines[0],lines[-1]):
                cols=line.split(',')
                parsed=datetime.fromisoformat(cols[5]+'T'+cols[6]).replace(tzinfo=timezone.utc).timestamp()
                delta=abs(round((float(cols[4])-25569)*86400)-parsed)
                stats['timestamp_endpoint_checks']+=1;stats['timestamp_endpoint_mismatches_over_1s']+=int(delta>1)
    report={'kind':'POST_FIT_DESCRIPTIVE_AUDIT_NOT_MODEL_SELECTION','stats':dict(stats),
            'source_sha256':digest(SOURCE),'auditor_sha256':digest(__file__),
            'note':'Drop counts can overlap. Checked first/last timestamp per unique input track, not every textual timestamp. This audit changes no fitted values or inclusion rules.'}
    with (OUT/'quality_audit.json').open('x') as f:json.dump(report,f,indent=2)
    print(json.dumps(report),flush=True)

if __name__=='__main__':main()
