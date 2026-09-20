"""Volunteer/family codes, consent, exposure limits, check-in, pause and hand-over purge."""
import io
import json
import zipfile
from datetime import datetime, timezone, timedelta
import pytest
from backend import main, roles
from backend.tests.test_searchproof import client, current


def members(client,mid):
    v=client.post(f"/api/missions/{mid}/members",json={"role":"volunteer","label":"봉사자A"}).json()
    f=client.post(f"/api/missions/{mid}/members",json={"role":"family","label":"가족A"}).json()
    return v,f


def test_codes_are_hashed_and_consent_gates_uploads(client):
    mid,s=current(client)
    v,f=members(client,mid)
    assert len(v["code"])==8 and v["join_path"].endswith(v["code"])
    with main.connect() as c:
        row=c.execute("SELECT code_hash FROM members WHERE id=?",(v["id"],)).fetchone()
        assert row["code_hash"]!=v["code"] and row["code_hash"]==roles.code_hash(v["code"])
    listed=client.get(f"/api/missions/{mid}/members").json()["members"]
    assert all("code" not in m and "code_hash" not in m for m in listed)
    first=client.post("/api/join",json={"code":v["code"]}).json()
    assert first["role"]=="volunteer" and first["needs_consent"] and not first["can_upload"]
    assert "zones" not in first and "poa" not in json.dumps(first)
    gpx=client.get(f"/api/missions/{mid}/demo.gpx").content
    hdr={"X-Role-Code":v["code"]}
    blocked=client.post("/api/join/tracks",headers=hdr,files={"file":("p.gpx",gpx,"application/gpx+xml")})
    assert blocked.status_code==403
    assert client.post("/api/join",json={"code":v["code"],"consent_version":"old"}).status_code==422
    lower=client.post("/api/join",json={"code":v["code"].lower()+"-","consent_version":roles.CONSENT_VERSION}).json()
    assert lower["member"]["consented"] and lower["can_upload"]
    ok=client.post("/api/join/tracks",headers=hdr,files={"file":("p.gpx",gpx,"application/gpx+xml")},data={"team_size":"4","spacing_m":"12"})
    assert ok.status_code==200 and ok.json()["summary"]["team"]["team_size"]==4
    assert ok.json()["summary"]["submitted_by"]==v["id"]
    # Uploading never changes the map: the coordinator still approves.
    assert client.get(f"/api/missions/{mid}").json()["version"]==s["version"]
    assert client.get(f"/api/missions/{mid}/members").json()["members"][0]["tracks"]=={"UPLOADED":1}
    family=client.post("/api/join",json={"code":f["code"]}).json()
    assert family["role"]=="family" and "assignment" not in family and family["progress"]["version"]==s["version"]
    assert client.post("/api/join/tracks",headers={"X-Role-Code":f["code"]},files={"file":("p.gpx",gpx,"application/gpx+xml")}).status_code==403
    assert client.post("/api/join/checkin",headers={"X-Role-Code":f["code"]}).status_code==422
    assert client.get("/api/join/state",headers={"X-Role-Code":"NOPE1234"}).status_code==403


def test_assignment_checkin_overdue_and_revoke(client):
    mid,s=current(client)
    v,_=members(client,mid)
    agency=next(f["properties"]["id"] for f in s["zones"]["features"] if f["properties"]["access"]=="AGENCY_ONLY")
    normal=next(f["properties"]["id"] for f in s["zones"]["features"] if f["properties"]["access"]!="AGENCY_ONLY")
    assert client.post(f"/api/missions/{mid}/members/{v['id']}/assign",json={"zone":agency}).status_code==422
    r=client.post(f"/api/missions/{mid}/members/{v['id']}/assign",json={"zone":normal}).json()
    assert r["assignment"]["zone_id"]==normal and r["assignment"]["geometry"]["type"]=="Polygon"
    hdr={"X-Role-Code":v["code"]}
    view=client.get("/api/join/state",headers=hdr).json()
    assert view["assignment"]["zone_id"]==normal and view["member"]["overdue"] # never checked in
    assert client.post("/api/join/checkin",headers=hdr).json()["ok"]
    assert not client.get("/api/join/state",headers=hdr).json()["member"]["overdue"]
    stale=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    with main.connect() as c: c.execute("UPDATE members SET last_checkin_at=? WHERE id=?",(stale,v["id"]))
    assert client.get(f"/api/missions/{mid}/members").json()["members"][0]["overdue"]
    assert client.post(f"/api/missions/{mid}/members/{v['id']}/revoke").json()["ok"]
    assert client.post(f"/api/missions/{mid}/members/{v['id']}/revoke").status_code==409
    assert client.get("/api/join/state",headers=hdr).status_code==403
    assert client.post(f"/api/missions/{mid}/members/{v['id']}/assign",json={"zone":normal}).status_code==409
    ledger=client.get(f"/api/missions/{mid}").json()["ledger"]
    assert any("배정" in l["note"] for l in ledger) and any("참여 코드 발급" in l["note"] for l in ledger)


def test_pause_and_handover_purge(client):
    mid,s=current(client)
    v,f=members(client,mid)
    client.post("/api/join",json={"code":v["code"],"consent_version":roles.CONSENT_VERSION})
    tr=client.post(f"/api/missions/{mid}/demo-track").json()
    assert client.post(f"/api/missions/{mid}/tracks/{tr['id']}/apply",json={"outcome":"COMPLETED_NO_FIND","expected_version":s["version"]}).status_code==200
    hdr={"X-Role-Code":v["code"]}
    clue=client.post("/api/join/ledger",headers=hdr,json={"note":"훈련 단서(가상)"}).json()
    assert clue["ok"] and not clue["map_changed"]
    paused=client.post(f"/api/missions/{mid}/transition",json={"status":"PAUSED","note":"악천후"}).json()
    assert paused["status"]["status"]=="PAUSED" and client.get("/api/join/state",headers=hdr).json()["paused"]
    assert client.get(f"/api/missions/{mid}").json()["status"]["status"]=="PAUSED"
    before=client.get(f"/api/missions/{mid}").json()
    done=client.post(f"/api/missions/{mid}/transition",json={"status":"HANDED_OVER","note":"경찰 인계"}).json()
    assert done["status"]["status"]=="HANDED_OVER" and done["purged_tracks"]==1
    with main.connect() as c:
        raws=[r["raw"] for r in c.execute("SELECT raw FROM tracks WHERE mission=?",(mid,))]
        purges=c.execute("SELECT * FROM track_purges WHERE mission=?",(mid,)).fetchall()
        revoked=c.execute("SELECT COUNT(*) AS n FROM members WHERE mission=? AND revoked_at IS NULL",(mid,)).fetchone()["n"]
    assert all(len(r)==0 for r in raws) and len(purges)==1 and revoked==0
    after=client.get(f"/api/missions/{mid}").json()
    # Aggregated results and receipts survive the purge unchanged.
    assert after["version"]==before["version"] and after["receipt"]["hash"]==before["receipt"]["hash"]
    assert after["coverage_km2"]==before["coverage_km2"]
    assert client.get("/api/join/state",headers=hdr).status_code==403
    assert client.post(f"/api/missions/{mid}/demo-track").status_code==409
    assert client.post(f"/api/missions/{mid}/ledger",json={"note":"늦은 기록"}).status_code==409
    assert client.post(f"/api/missions/{mid}/members",json={"role":"volunteer","label":"늦은 참여"}).status_code==409
    assert client.post(f"/api/missions/{mid}/transition",json={"status":"ACTIVE"}).status_code==409
    z=client.get(f"/api/missions/{mid}/package.zip")
    names=zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "participants.json" in names
    participants=json.loads(zipfile.ZipFile(io.BytesIO(z.content)).read("participants.json"))
    assert participants["status"]["status"]=="HANDED_OVER" and len(participants["purged_tracks"])==1
    assert all("code" not in m for m in participants["members"])
    assert client.post("/api/verify",files={"file":("p.zip",z.content,"application/zip")}).json()["valid"]
    forecast=client.get(f"/api/missions/{mid}/forecast",params={"version":after["version"],"seconds":600})
    assert forecast.status_code==200
    report=client.get(f"/api/missions/{mid}/report").text
    assert "기관 인계 완료" in report and "봉사자A" in report
