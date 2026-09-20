"""Fetch Microsoft's public GeoLife archive without executing/extracting it.

This is ordinary mobility data, never labeled missing-person behavior.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/geolife_2012'
URL = ('https://download.microsoft.com/download/F/4/8/'
       'F4894AA5-FDBC-481E-9285-D5F8C4C4F039/Geolife%20Trajectories%201.3.zip')
MAX_BYTES = 350_000_000


def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    target=OUT/'Geolife_Trajectories_1.3.zip'
    if not target.exists():
        fd,tmp=tempfile.mkstemp(prefix='geolife-',suffix='.part',dir=OUT)
        count=0
        try:
            request=urllib.request.Request(URL,headers={'User-Agent':'SearchProof academic data audit'})
            with os.fdopen(fd,'wb') as f,urllib.request.urlopen(request,timeout=60) as response:
                if response.status!=200: raise ValueError('Unexpected download response')
                while chunk:=response.read(1024*1024):
                    count+=len(chunk)
                    if count>MAX_BYTES: raise ValueError('Archive exceeds size limit')
                    f.write(chunk)
                    if count%(25*1024*1024)==0: print(json.dumps({'downloaded_bytes':count}),flush=True)
                f.flush();os.fsync(f.fileno())
            if not zipfile.is_zipfile(tmp): raise ValueError('Not a ZIP archive')
            os.link(tmp,target)  # Never replace a concurrent/existing archive.
        finally:
            Path(tmp).unlink(missing_ok=True)  # Only this attempt's temporary file.
    with zipfile.ZipFile(target) as z:
        if any(i.file_size>100_000_000 for i in z.infolist()): raise ValueError('Unexpected oversized member')
        if sum(i.file_size for i in z.infolist())>3_000_000_000: raise ValueError('Expanded archive too large')
        damaged=z.testzip()
        if damaged: raise ValueError('ZIP CRC failed: '+damaged)
        members=[{'name':i.filename,'bytes':i.file_size,'crc32':i.CRC} for i in z.infolist()]
        notice=[i.filename for i in z.infolist() if i.filename.lower().endswith(('.pdf','.txt','.md')) and '/Data/' not in i.filename]
    receipt={'source_page':'https://www.microsoft.com/en-us/download/details.aspx?id=52367',
             'url':URL,'retrieved_at':datetime.now(timezone.utc).isoformat(),'bytes':target.stat().st_size,
             'sha256':sha256(target),'crc_verified':True,'role':'ORDINARY_MOBILITY_NOT_LOST_PEOPLE',
             'license_status':'REVIEW_BEFORE_TRAINING','members':members,
             'collector_sha256':sha256(Path(__file__))}
    manifest=OUT/'manifest.json'
    if manifest.exists():
        old=json.loads(manifest.read_text())
        if old['sha256']!=receipt['sha256'] or old['members']!=members: raise ValueError('Existing manifest differs')
    else:
        with manifest.open('x') as f: json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(json.dumps({'archive':str(target),'sha256':receipt['sha256'],'members':len(members),'notice_files':notice}))


if __name__=='__main__': main()
