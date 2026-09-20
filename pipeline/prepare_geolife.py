"""Prepare labeled ordinary walking only. Local research; NO REDISTRIBUTION."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import io
import json
import zipfile
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/research/geolife_2012/Geolife_Trajectories_1.3.zip'
OUT=ROOT/'data/ml/geolife'
PROTOCOL=ROOT/'docs/AI-GeoLife-사전검증계획.md'
SOURCE_HASH='1107c5ac064d0a23c8d021a8736a77e53abc75b227062e6260342c6a8d86bdb6'
EDGES=np.array([0,.25,.75,1.25,1.75,2.5,4.0000001])

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def stamp(s):return datetime.strptime(s.strip(),'%Y/%m/%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()

def read_labels(raw):
    rows=[]
    for line in raw.decode('utf-8-sig').splitlines()[1:]:
        if not line.strip():continue
        a,b,mode=line.split('\t');a,b=stamp(a),stamp(b)
        if not a<b:raise ValueError('Invalid label interval')
        rows.append((a,b,mode.strip().lower()))
    return sorted(rows)

def label_points(times,labels):
    # Count ALL active intervals, excluding even duplicate/overlapping walk labels.
    starts=np.array([x[0] for x in labels]);ends=np.array([x[1] for x in labels])
    start_order=np.argsort(starts);end_order=np.argsort(ends)
    ns=np.searchsorted(starts[start_order],times,side='right')
    ne=np.searchsorted(ends[end_order],times,side='left')
    count=ns-ne
    # For exactly one active interval, its ID is the difference of the two
    # cumulative ID sums. This also handles a short interval nested in a long one.
    index=np.r_[0,np.cumsum(start_order+1)][ns]-np.r_[0,np.cumsum(end_order+1)][ne]-1
    safe=np.clip(index,0,len(labels)-1)
    ok=(index>=0)&(count==1)&(times<=ends[safe])&np.array([x[2]=='walk' for x in labels])[safe]
    return np.where(ok,index,-1)

def vectors(lat,lon):
    north=np.diff(lat)*np.pi/180*6371008.8
    east=((np.diff(lon)+180)%360-180)*np.pi/180*6371008.8*np.cos((lat[1:]+lat[:-1])*np.pi/360)
    return np.column_stack([east,north])

def samples(points,labels):
    """Yield arrays for continuous valid labeled segments, with endpoint coords for dedup only."""
    lat,lon,days=points.T
    ts=np.rint((days-25569)*86400)
    valid=np.isfinite(points).all(axis=1)&(np.abs(lat)<=90)&(np.abs(lon)<=180)
    safe_ts=np.where(np.isfinite(ts),ts,0)
    ids=label_points(safe_ts,labels)
    valid&=ids>=0
    dt=np.diff(safe_ts)
    step=np.linalg.norm(vectors(lat,lon),axis=1)
    connected=(valid[:-1]&valid[1:]&(ids[:-1]==ids[1:])&(dt>0)&(dt<=15)&(step<=4*np.maximum(dt,0)))
    boundaries=np.r_[0,np.flatnonzero(~connected)+1,len(ts)]
    for lo,hi in zip(boundaries[:-1],boundaries[1:]):
        if hi-lo<3 or not valid[lo] or ts[hi-1]-ts[lo]<60:continue
        grid=np.arange(np.ceil(ts[lo]/30)*30,np.floor(ts[hi-1]/30)*30+1,30)
        if len(grid)<3:continue
        la=np.interp(grid,ts[lo:hi],lat[lo:hi])
        # Longitude unwrapping avoids interpolating across the entire world.
        ln=np.rad2deg(np.interp(grid,ts[lo:hi],np.unwrap(np.deg2rad(lon[lo:hi]))))
        vec=vectors(la,ln);speed=np.linalg.norm(vec,axis=1)/30
        heading=np.arctan2(vec[:,1],vec[:,0])
        turn=(np.floor(((heading[1:]-heading[:-1])+np.pi/8)%(2*np.pi)/(np.pi/4)).astype(int))
        turn[(speed[:-1]<.25)|(speed[1:]<.25)]=8
        sb=np.clip(np.searchsorted(EDGES,speed,side='right')-1,0,5)
        good=np.isfinite(speed[:-1])&np.isfinite(speed[1:])&(speed[:-1]<=4)&(speed[1:]<=4)
        rows=np.column_stack([grid[1:-1],sb[:-1],sb[1:]*9+turn,speed[:-1],speed[1:],vec[:-1],vec[1:]])
        coords=np.column_stack([la[:-2],ln[:-2],la[1:-1],ln[1:-1],la[2:],ln[2:]])
        yield rows[good],coords[good]

def main():
    if digest(SOURCE)!=SOURCE_HASH:raise ValueError('Source hash mismatch')
    OUT.mkdir(parents=True,exist_ok=True)
    target=OUT/'walking_samples.npz'
    if target.exists() or (OUT/'inventory.json').exists():raise FileExistsError('Preserve existing preparation; do not overwrite')
    stats=Counter();chunks=[];people=[];tracks=[];seen_files=set();seen_times=set();seen_coords={}
    with zipfile.ZipFile(SOURCE) as z:
        labels={n.split('/')[-2]:read_labels(z.read(n)) for n in z.namelist() if n.endswith('/labels.txt')}
        eligible={p for p,ls in labels.items() if any(l[2]=='walk' for l in ls)}
        members=sorted(n for n in z.namelist() if n.endswith('.plt') and n.split('/')[-3] in eligible)
        for j,name in enumerate(members):
            person=name.split('/')[-3];raw=z.read(name);h=hashlib.sha256(raw).digest()
            if h in seen_files:stats['duplicate_files']+=1;continue
            seen_files.add(h)
            arr=np.loadtxt(io.BytesIO(raw),delimiter=',',skiprows=6,usecols=(0,1,4),ndmin=2)
            stats['input_points']+=len(arr);stats['input_tracks']+=1
            for rows,coords in samples(arr,labels[person]):
                keep=[]
                for k,(row,coord) in enumerate(zip(rows,coords)):
                    key=(person,int(row[0]));ck=tuple(np.round(coord,6))
                    if key in seen_times:stats['duplicate_person_time']+=1;continue
                    if ck in seen_coords and seen_coords[ck]!=person:stats['duplicate_coordinate_windows']+=1;continue
                    seen_times.add(key);seen_coords[ck]=person;keep.append(k)
                if keep:
                    chunks.append(rows[keep]);people.extend([int(person)]*len(keep));tracks.extend([j]*len(keep))
            if (j+1)%500==0:print(json.dumps({'processed_tracks':j+1,'of':len(members),'samples_so_far':len(people)}),flush=True)
    rows=np.concatenate(chunks);person=np.array(people,dtype=np.int16);track=np.array(tracks,dtype=np.int32)
    counts=Counter(person.tolist());valid_people={p for p,n in counts.items() if n>=100}
    keep=np.isin(person,list(valid_people));stats['small_person_rows_excluded']=int((~keep).sum())
    rows,person,track=rows[keep],person[keep],track[keep]
    order=sorted(valid_people,key=lambda p:hashlib.sha256(f'geolife-v1:{p:03d}'.encode()).digest())
    a,b=int(len(order)*.6),int(len(order)*.8)
    splits={str(p):('train' if i<a else 'validation' if i<b else 'test') for i,p in enumerate(order)}
    np.savez_compressed(target,rows=rows,person=person,track=track)
    inv={'created_at':datetime.now(timezone.utc).isoformat(),'role':'ORDINARY_WALKING_NOT_LOST_PEOPLE',
         'license':'MSR-LA NON-COMMERCIAL; LOCAL RESEARCH; NO REDISTRIBUTION',
         'archive_sha256':SOURCE_HASH,'protocol_sha256':digest(PROTOCOL),'preparer_sha256':digest(__file__),
         'prepared_sha256':digest(target),'columns':['utc','previous_speed_bin','next_joint_class','previous_speed_mps','next_speed_mps','previous_dx_m','previous_dy_m','next_dx_m','next_dy_m'],
         'speed_edges_mps':[0,.25,.75,1.25,1.75,2.5,4],'turn_classes':9,
         'stats':dict(stats),'people':len(valid_people),'rows':len(rows),'split_by_person':splits,
         'split_counts':{s:{'people':sum(v==s for v in splits.values()),'rows':int(sum(np.count_nonzero(person==int(p)) for p,v in splits.items() if v==s))} for s in ['train','validation','test']},
         'limitations':['Label files: 69 actual vs 73 in guide','Ordinary mobility, mostly China, 2007–2012','Within-person overlap excluded; companions/shared routes can remain','Linear interpolation and GPS errors; net displacement not path speed']}
    with (OUT/'inventory.json').open('x') as f:json.dump(inv,f,indent=2,ensure_ascii=False,allow_nan=False)
    print(json.dumps({k:inv[k] for k in ['people','rows','split_counts','stats']}),flush=True)

if __name__=='__main__':main()
