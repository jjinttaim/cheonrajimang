"""Volunteer and family layer: join codes, consent, role-filtered views,
check-in timers, mission pause and hand-over purge.

Trust model of this local prototype: the coordinator is whoever operates the
loopback server. Join codes only protect the shared volunteer/family views and
never unlock coordinator actions. Codes are stored hashed; the plain code is
returned exactly once when it is created.
"""
from datetime import datetime, timezone
import hashlib
import json
import secrets
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form
from pydantic import BaseModel, Field

router = APIRouter()
ROLES = ('volunteer', 'family')
STATUSES = ('ACTIVE', 'PAUSED', 'HANDED_OVER')
CONSENT_VERSION = '2026-09-20-v1'
CONSENT_TEXT = ('이 수색에 참여하는 동안 제출하는 GPS 기록은 이 임무의 수색 범위 계산과 기관 인계 문서 작성에만 사용됩니다. '
                '원본 기록은 임무가 기관에 인계되면 서버에서 삭제되고 집계된 수색 결과와 해시만 남습니다. '
                '동의는 언제든 조정자에게 철회를 요청할 수 있으며, 철회 후에는 새 기록을 제출할 수 없습니다. '
                '이 앱은 훈련용 프로토타입이며 안전 채널이 아닙니다. 위험 구역에는 진입하지 마세요.')
SAFETY_TEXT = '배정 구역 밖으로 나가지 말고, 수역, 급경사, 차도에 접근하지 마세요. 체크인 시간이 지나면 조정자에게 알림이 표시됩니다.'
FAMILY_TEXT = '가족 화면은 수색 진행 상황만 보여 줍니다. 확률 지도와 대원 위치는 조정자만 볼 수 있습니다. 단서나 정보는 아래에 남겨 주시면 조정자가 검토합니다.'
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
DEFAULT_CHECKIN_SECONDS = 1800


def now(): return datetime.now(timezone.utc).isoformat()
def code_hash(code): return hashlib.sha256(code.encode()).hexdigest()
def new_code(): return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
def normalize(code): return ''.join(ch for ch in (code or '').upper() if ch.isalnum())


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY,mission TEXT NOT NULL REFERENCES missions(id),role TEXT NOT NULL,
        label TEXT NOT NULL,code_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,consent_version TEXT,consent_at TEXT,
        revoked_at TEXT,last_checkin_at TEXT,zone INTEGER,checkin_interval INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS mission_status(mission TEXT PRIMARY KEY REFERENCES missions(id),status TEXT NOT NULL,updated_at TEXT NOT NULL,note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS track_purges(track TEXT PRIMARY KEY,mission TEXT NOT NULL REFERENCES missions(id),gpx_sha256 TEXT NOT NULL,purged_at TEXT NOT NULL);
    ''')


def mission_status(c, mid):
    row = c.execute('SELECT status,updated_at,note FROM mission_status WHERE mission=?', (mid,)).fetchone()
    return {'status': row['status'], 'updated_at': row['updated_at'], 'note': row['note']} if row else {'status': 'ACTIVE', 'updated_at': None, 'note': ''}


def require_active(c, mid):
    """Mutations are refused once a mission is handed over; pause only warns."""
    if mission_status(c, mid)['status'] == 'HANDED_OVER':
        raise HTTPException(409, '기관에 인계된 임무는 더 이상 변경할 수 없습니다. 새 모의 수색을 만드세요.')


class MemberInput(BaseModel):
    role: Literal['volunteer', 'family']
    label: str = Field(min_length=1, max_length=40)
    checkin_interval: int = Field(default=DEFAULT_CHECKIN_SECONDS, ge=300, le=7200)


class AssignInput(BaseModel):
    zone: int | None = Field(default=None, ge=0, le=1023)


class TransitionInput(BaseModel):
    status: Literal['ACTIVE', 'PAUSED', 'HANDED_OVER']
    note: str = Field(default='', max_length=300)


class JoinInput(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    consent_version: str | None = None


class ClueInput(BaseModel):
    note: str = Field(min_length=2, max_length=1000)
    observed_at: str = Field(default='', max_length=50)


def member_view(c, row, clock=None):
    at = clock or datetime.now(timezone.utc)
    last = datetime.fromisoformat(row['last_checkin_at']) if row['last_checkin_at'] else None
    overdue = row['role'] == 'volunteer' and row['revoked_at'] is None and (last is None or (at - last).total_seconds() > row['checkin_interval'])
    tracks = c.execute("SELECT status,COUNT(*) AS n FROM tracks WHERE mission=? AND summary LIKE ? GROUP BY status", (row['mission'], f'%"submitted_by":"{row["id"]}"%')).fetchall()
    return {'id': row['id'], 'role': row['role'], 'label': row['label'], 'created_at': row['created_at'],
            'consented': bool(row['consent_at']) and row['consent_version'] == CONSENT_VERSION,
            'consent_at': row['consent_at'], 'revoked': row['revoked_at'] is not None, 'revoked_at': row['revoked_at'],
            'last_checkin_at': row['last_checkin_at'], 'checkin_interval': row['checkin_interval'], 'overdue': bool(overdue),
            'zone': row['zone'], 'tracks': {t['status']: t['n'] for t in tracks}}


def zone_feature(c, mid, zone):
    from . import main
    if zone is None: return None
    _, v = main.version_row(c, mid)
    for f in json.loads(v['summary'])['zones']['features']:
        if f['properties']['id'] == zone:
            p = f['properties']
            return {'zone_id': zone, 'label': p['label'], 'geometry': f['geometry'], 'access': p['access'], 'decision': p.get('decision', 'PROPOSED'),
                    'hazard_note': '수역이나 급경사가 포함된 구역입니다. 일반 참여자는 진입하지 마세요.' if p['access'] == 'AGENCY_ONLY' else '현장 접근 가능 여부는 조정자와 확인하세요.'}
    return {'zone_id': zone, 'label': None, 'geometry': None, 'access': 'UNKNOWN', 'decision': None, 'hazard_note': '구역 정보를 찾을 수 없습니다.'}


# ---------------------------------------------------------------- coordinator

@router.post('/api/missions/{mid}/members')
def create_member(mid: str, data: MemberInput):
    from . import main
    code = new_code(); member_id = uuid.uuid4().hex
    with main.LOCK, main.connect() as c:
        main.get_mission(c, mid); require_active(c, mid)
        c.execute('INSERT INTO members VALUES(?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,?)',
                  (member_id, mid, data.role, data.label, code_hash(code), now(), data.checkin_interval))
        c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, mid, 'DECISION',
                  f"참여 코드 발급, {'봉사자' if data.role=='volunteer' else '가족'} '{data.label}'", '조정자', now(), now()))
    # The plain code exists only in this response; the database keeps a hash.
    return {'id': member_id, 'role': data.role, 'label': data.label, 'code': code,
            'join_path': '/?view=join#code=' + code, 'consent_version': CONSENT_VERSION}


@router.get('/api/missions/{mid}/members')
def list_members(mid: str):
    from . import main
    with main.connect() as c:
        main.get_mission(c, mid)
        rows = c.execute('SELECT * FROM members WHERE mission=? ORDER BY created_at', (mid,)).fetchall()
        return {'status': mission_status(c, mid), 'consent_version': CONSENT_VERSION, 'members': [member_view(c, r) for r in rows]}


@router.post('/api/missions/{mid}/members/{member_id}/revoke')
def revoke_member(mid: str, member_id: str):
    from . import main
    with main.LOCK, main.connect() as c:
        changed = c.execute('UPDATE members SET revoked_at=? WHERE mission=? AND id=? AND revoked_at IS NULL', (now(), mid, member_id)).rowcount
        if not changed: raise HTTPException(409, '이미 해지되었거나 없는 참여자입니다.')
    return {'ok': True}


@router.post('/api/missions/{mid}/members/{member_id}/assign')
def assign_member(mid: str, member_id: str, data: AssignInput):
    from . import main
    with main.LOCK, main.connect() as c:
        require_active(c, mid)
        row = c.execute('SELECT * FROM members WHERE mission=? AND id=?', (mid, member_id)).fetchone()
        if not row: raise HTTPException(404, '참여자를 찾을 수 없습니다.')
        if row['role'] != 'volunteer': raise HTTPException(422, '가족 참여자에게는 구역을 배정하지 않습니다.')
        if row['revoked_at']: raise HTTPException(409, '해지된 참여자입니다.')
        feature = zone_feature(c, mid, data.zone)
        if feature and feature['access'] == 'AGENCY_ONLY': raise HTTPException(422, '전문기관 검토 구역은 일반 참여자에게 배정할 수 없습니다.')
        c.execute('UPDATE members SET zone=? WHERE id=?', (data.zone, member_id))
        if feature:
            c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, mid, 'DECISION', f"봉사자 '{row['label']}'에게 구역 {feature['label']} 배정", '조정자', now(), now()))
    return {'ok': True, 'assignment': feature}


@router.get('/api/missions/{mid}/status')
def get_status(mid: str):
    from . import main
    with main.connect() as c:
        main.get_mission(c, mid)
        return mission_status(c, mid)


@router.post('/api/missions/{mid}/transition')
def transition(mid: str, data: TransitionInput):
    from . import main
    with main.LOCK, main.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        main.get_mission(c, mid)
        current = mission_status(c, mid)['status']
        if current == 'HANDED_OVER': raise HTTPException(409, '인계 완료 상태는 되돌릴 수 없습니다.')
        if data.status == current: return {'ok': True, 'status': mission_status(c, mid), 'purged_tracks': 0}
        purged = 0
        if data.status == 'HANDED_OVER':
            rows = c.execute('SELECT id,raw FROM tracks WHERE mission=? AND length(raw)>0', (mid,)).fetchall()
            for r in rows:
                c.execute('INSERT INTO track_purges VALUES(?,?,?,?)', (r['id'], mid, hashlib.sha256(r['raw']).hexdigest(), now()))
                c.execute("UPDATE tracks SET raw=X'' WHERE id=?", (r['id'],))
                purged += 1
            c.execute("UPDATE tracks SET status='REJECTED' WHERE mission=? AND status='UPLOADED'", (mid,))
            c.execute('UPDATE members SET revoked_at=? WHERE mission=? AND revoked_at IS NULL', (now(), mid))
        c.execute('INSERT INTO mission_status VALUES(?,?,?,?) ON CONFLICT(mission) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,note=excluded.note',
                  (mid, data.status, now(), data.note))
        label = {'ACTIVE': '수색 재개', 'PAUSED': '일괄 수색 중지', 'HANDED_OVER': f'기관 인계 완료, 원본 GPX {purged}건 삭제, 참여 코드 전체 해지'}[data.status]
        c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, mid, 'DECISION', label + (', ' + data.note if data.note else ''), '조정자', now(), now()))
        status = mission_status(c, mid)
    # Cached replays must not serve purged observations.
    main.cached_observations.cache_clear(); main.cached_forecast.cache_clear(); main.cached_plan_paths.cache_clear()
    return {'ok': True, 'status': status, 'purged_tracks': purged}


# ---------------------------------------------------------------- members

def authenticate(c, code, allow_revoked=False):
    digest = code_hash(normalize(code))
    row = c.execute('SELECT * FROM members WHERE code_hash=?', (digest,)).fetchone()
    if not row: raise HTTPException(403, '참여 코드가 올바르지 않습니다.')
    if row['revoked_at'] and not allow_revoked: raise HTTPException(403, '해지된 참여 코드입니다. 조정자에게 문의하세요.')
    return row


def view_for(c, row):
    from . import main
    m = main.get_mission(c, row['mission']); params = json.loads(m['params'])
    status = mission_status(c, row['mission'])
    me = member_view(c, row)
    base = {'role': row['role'], 'mission': {'id': row['mission'], 'name': m['name'], 'status': status['status'], 'status_note': status['note'], 'version': m['current_version']},
            'member': me, 'needs_consent': not me['consented'] and not me['revoked'], 'consent_version': CONSENT_VERSION, 'consent_text': CONSENT_TEXT,
            'paused': status['status'] == 'PAUSED', 'handed_over': status['status'] == 'HANDED_OVER'}
    if row['role'] == 'volunteer':
        base.update({'assignment': zone_feature(c, row['mission'], row['zone']), 'subject_brief': params.get('subject_brief') or '조정자가 아직 인상착의 요약을 입력하지 않았습니다.',
                     'safety_text': SAFETY_TEXT, 'can_upload': me['consented'] and not me['revoked'] and status['status'] != 'HANDED_OVER'})
    else:
        _, v = main.version_row(c, row['mission'])
        summary = json.loads(v['summary'])
        applied = c.execute("SELECT COUNT(*) AS n FROM tracks WHERE mission=? AND status='APPLIED'", (row['mission'],)).fetchone()['n']
        clues = c.execute("SELECT COUNT(*) AS n FROM ledger WHERE mission=? AND kind='CLUE'", (row['mission'],)).fetchone()['n']
        base.update({'progress': {'version': v['number'], 'updated_at': v['created_at'], 'searched_zones': summary['searched_zones'],
                                  'coverage_km2': summary['coverage_km2'], 'applied_tracks': applied, 'clues': clues},
                     'family_text': FAMILY_TEXT})
    return base


@router.post('/api/join')
def join(data: JoinInput):
    from . import main
    with main.LOCK, main.connect() as c:
        row = authenticate(c, data.code)
        if data.consent_version is not None:
            if data.consent_version != CONSENT_VERSION: raise HTTPException(422, '동의 문구 버전이 다릅니다. 화면을 새로고침한 뒤 다시 동의해 주세요.')
            if not row['consent_at'] or row['consent_version'] != CONSENT_VERSION:
                c.execute('UPDATE members SET consent_version=?,consent_at=? WHERE id=?', (CONSENT_VERSION, now(), row['id']))
                row = c.execute('SELECT * FROM members WHERE id=?', (row['id'],)).fetchone()
        return view_for(c, row)


@router.get('/api/join/state')
def join_state(x_role_code: str = Header(default='')):
    from . import main
    with main.connect() as c:
        return view_for(c, authenticate(c, x_role_code))


@router.post('/api/join/checkin')
def checkin(x_role_code: str = Header(default='')):
    from . import main
    with main.LOCK, main.connect() as c:
        row = authenticate(c, x_role_code)
        if row['role'] != 'volunteer': raise HTTPException(422, '체크인은 봉사자 역할에서만 사용합니다.')
        c.execute('UPDATE members SET last_checkin_at=? WHERE id=?', (now(), row['id']))
        return {'ok': True, 'checked_in_at': now(), 'checkin_interval': row['checkin_interval']}


@router.post('/api/join/tracks')
async def member_upload(file: UploadFile = File(...), team_size: int = Form(default=1), spacing_m: float = Form(default=15.), x_role_code: str = Header(default='')):
    from . import main
    with main.connect() as c:
        row = authenticate(c, x_role_code)
        if row['role'] != 'volunteer': raise HTTPException(403, '가족 역할은 수색 기록을 제출할 수 없습니다.')
        if not row['consent_at'] or row['consent_version'] != CONSENT_VERSION: raise HTTPException(403, '위치정보 이용 동의 후 제출할 수 있습니다.')
        mission = row['mission']; label = row['label']; member_id = row['id']
    raw = await file.read(2_000_001)
    from pathlib import Path
    result = main.insert_track(mission, raw, f"{label} {Path(file.filename or 'track.gpx').name}", main.track_team(team_size, spacing_m), member=member_id)
    return {**result, 'note': '조정자가 미발견을 확정할 때까지 확률지도는 바뀌지 않습니다.'}


@router.post('/api/join/ledger')
def member_clue(data: ClueInput, x_role_code: str = Header(default='')):
    from . import main
    observed = data.observed_at or now()
    try:
        parsed = datetime.fromisoformat(observed.replace('Z', '+00:00'))
        if parsed.tzinfo is None: raise ValueError()
    except ValueError: raise HTTPException(422, '관찰 시각에 시간대가 필요합니다.')
    with main.LOCK, main.connect() as c:
        row = authenticate(c, x_role_code); require_active(c, row['mission'])
        source = ('봉사자' if row['role'] == 'volunteer' else '가족') + ' ' + row['label']
        c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, row['mission'], 'CLUE', data.note, source, observed, now()))
    return {'ok': True, 'map_changed': False}


def package_files(c, mid):
    """Hand-over extras: who took part (no codes) and which raw tracks were purged."""
    members = [{k: v for k, v in member_view(c, r).items() if k != 'tracks'} for r in c.execute('SELECT * FROM members WHERE mission=? ORDER BY created_at', (mid,))]
    purges = [dict(r) for r in c.execute('SELECT track,gpx_sha256,purged_at FROM track_purges WHERE mission=? ORDER BY purged_at', (mid,))]
    return {'status': mission_status(c, mid), 'consent_version': CONSENT_VERSION, 'members': members, 'purged_tracks': purges}
