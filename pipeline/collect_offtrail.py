"""Archive an author-published walking experiment without executing contents.

This is NOT lost-person behavior or calibrated detection data. Collection does
not select a model or overwrite existing source files.
"""
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import io
import json
import os
import tempfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/offtrail_lidar_2025'
RECORD='https://zenodo.org/api/records/17081136'
FILE='https://zenodo.org/api/records/17081136/files/trajectories.zip/content'


def retrieve(url,path,limit):
    if path.exists():return path.read_bytes()
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'SearchProof educational research'}),timeout=30) as r:
        raw=r.read(limit+1)
    if len(raw)>limit:raise ValueError('Source exceeds size limit')
    fd,temp=tempfile.mkstemp(prefix='.download-',dir=path.parent)
    with os.fdopen(fd,'wb') as f:f.write(raw)
    os.replace(temp,path)
    return raw


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    metadata_raw=retrieve(RECORD,OUT/'record.json',2_000_000)
    metadata=json.loads(metadata_raw)
    if metadata['metadata']['license']['id']!='cc-by-4.0':raise ValueError('Unexpected license')
    f=next(f for f in metadata['files'] if f['key']=='trajectories.zip')
    if f['links']['self']!=FILE or f['size']>5_000_000:raise ValueError('Unexpected source file')
    raw=retrieve(FILE,OUT/'trajectories.zip',5_000_000)
    if len(raw)!=f['size'] or 'md5:'+hashlib.md5(raw).hexdigest()!=f['checksum']:
        raise ValueError('Author checksum mismatch')
    inventory=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(i.file_size for i in archive.infolist())>20_000_000:raise ValueError('Archive exceeds inspection limit')
        for entry in archive.infolist():
            if entry.is_dir():continue
            # Never extract or execute anything from the archive.
            content=archive.read(entry.filename)
            inventory.append({'name':entry.filename,'bytes':len(content),
                              'sha256':hashlib.sha256(content).hexdigest(),
                              'las_signature':content[:4]==b'LASF'})
    manifest={'retrieved_at':datetime.now(timezone.utc).isoformat(),
              'doi':'10.5281/zenodo.17081136','paper':'https://doi.org/10.1080/15481603.2026.2626632',
              'attribution':'Sierra Lynn Cutler, Michael J. Campbell, Philip E. Dennison (2025)',
              'license':'CC-BY-4.0','license_url':'https://creativecommons.org/licenses/by/4.0/',
              'files':[{'path':'record.json','url':RECORD,'sha256':hashlib.sha256(metadata_raw).hexdigest()},
                       {'path':'trajectories.zip','url':FILE,'sha256':hashlib.sha256(raw).hexdigest(),'author_checksum':f['checksum']}],
              'archive_inventory':inventory,'las_files':sum(x['las_signature'] for x in inventory),
              'status':'COLLECTED_NOT_USED_FOR_TRAINING',
              'reason':'Controlled off-trail walking, not lost subjects. CRS, time fields, person/transect groups and independent terrain inputs require validation.',
              'trained_bundle_modified':False}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({k:manifest[k] for k in ('doi','license','las_files','status','trained_bundle_modified')},ensure_ascii=False))


if __name__=='__main__':main()
