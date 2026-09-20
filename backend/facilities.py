"""Optional, uncalibrated POI attraction for the behavior scenario only.

Data is content-addressed. Existing missions keep their original assumptions.
Potential is based on walkable terrain travel time, not straight-line distance.

Island snapshots (data/jeju/facilities/<digest>) hold the POI extract for the
whole master grid; the travel-time potential is computed per analysis window
(with a 64-cell margin so facilities just outside the window still attract).
Legacy 한림읍 snapshots (data/hallim/facilities/<digest>) keep their frozen
512×512 potential and stay valid for the original window only.
"""
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import json
import re
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from . import core

DATA = core.DATA / "facilities"
LEGACY = core.LEGACY_DATA / "facilities"
CATEGORIES = {
    "supplies": "편의점·마트",
    "food": "식당·카페",
    "water": "음수대",
    "transit": "정류장·터미널",
    "shelter": "쉼터·숙박",
    "medical": "병원·약국",
    "help": "경찰·소방",
}
# Not calibrated from incidents. Identical category weights avoid invented preferences.
SCALE_SECONDS = 900.0
MAX_BIAS = 0.7
MARGIN = core.LATTICE   # cells of master terrain kept around a window while routing to facilities


def classify(tags):
    if tags.get("shop") in ("convenience", "supermarket"): return "supplies"
    if tags.get("amenity") in ("restaurant", "cafe", "fast_food"): return "food"
    if tags.get("amenity") == "drinking_water": return "water"
    if tags.get("highway") == "bus_stop" or tags.get("amenity") == "bus_station": return "transit"
    if tags.get("amenity") == "shelter" or tags.get("tourism") in ("hotel", "hostel", "guest_house"): return "shelter"
    if tags.get("amenity") in ("hospital", "clinic", "pharmacy"): return "medical"
    if tags.get("amenity") in ("police", "fire_station"): return "help"
    return None


def latest():
    path = DATA/"latest.json"
    return json.loads(path.read_text()) if path.exists() else {
        "available": False, "count": 0, "categories": {}, "status": "NOT_PREPARED"}


def travel_graph(t):
    """Directed walking-time graph over a terrain-like object (water/slope/lc/dem/speed)."""
    n = t.water.shape[0]
    valid = ~t.water & (t.slope <= 45) & (t.lc != 0)
    rr, cc = np.indices(t.water.shape)
    sources, targets, costs = [], [], []
    h, w = t.water.shape
    for dr, dc in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)]:
        nr, nc = rr+dr, cc+dc
        sr, sc = np.clip(nr,0,h-1), np.clip(nc,0,w-1)
        keep = valid & (nr>=0)&(nr<h)&(nc>=0)&(nc<w)&valid[sr,sc]
        if dr and dc:
            keep &= valid[sr,cc] & valid[rr,sc]  # No diagonal shortcut through barriers.
        distance = core.CELL*np.hypot(dr,dc)
        grade = (t.dem[sr,sc]-t.dem)/distance
        speed = np.maximum(.025, 1.667*np.exp(-3.5*np.abs(grade+.05))*t.speed[sr,sc]*core.ASSUMPTIONS["walking_mobility"])
        sources.append((rr*w+cc)[keep]); targets.append((sr*w+sc)[keep])
        costs.append((distance/speed)[keep])
    return coo_matrix((np.concatenate(costs), (np.concatenate(sources),np.concatenate(targets))),
                      shape=(h*w,h*w)).tocsr(), valid


def make_potential(t, features):
    """exp(-walking seconds to the nearest usable facility / SCALE_SECONDS) on t's grid.
    Sets properties.model_eligible on each feature (in place)."""
    graph, valid = travel_graph(t)
    h, w = valid.shape
    seeds = []
    for f in features:
        r,c = t.rc(*f["geometry"]["coordinates"]); r,c = int(np.floor(r)),int(np.floor(c))
        usable = 0<=r<h and 0<=c<w and valid[r,c] and f["properties"]["access"] not in ("private","no")
        f["properties"]["model_eligible"] = bool(usable)
        if usable: seeds.append(r*w+c)
    if not seeds: return np.zeros((h,w),dtype="float32")
    # Transpose the directed graph: distance FROM each cell TO its nearest facility.
    seconds = dijkstra(graph.T.tocsr(), directed=True, indices=np.unique(seeds), min_only=True)
    return np.exp(-seconds.reshape(h,w)/SCALE_SECONDS).astype("float32")


def eligibility(features):
    """Island-wide usability of each POI on the master grid (used at snapshot time)."""
    m = core.master()
    valid = ~(m.lc==80) & (m.slope<=45) & (m.lc!=0)
    for f in features:
        r,c = m.rc(*f["geometry"]["coordinates"]); r,c = int(np.floor(r)),int(np.floor(c))
        usable = 0<=r<m.height and 0<=c<m.width and bool(valid[r,c]) and f["properties"].get("access","unknown") not in ("private","no")
        f["properties"]["model_eligible"] = bool(usable)
    return features


def _valid_digest(digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("시설 데이터 버전이 올바르지 않습니다.")


@lru_cache(maxsize=8)
def snapshot(digest):
    """Returns (frozen legacy potential or None, geojson, manifest)."""
    _valid_digest(digest)
    folder = DATA/digest if (DATA/digest).exists() else LEGACY/digest
    try:
        manifest = json.loads((folder/"manifest.json").read_text())
        raw = (folder/"facilities.geojson").read_bytes()
    except OSError as e:
        raise ValueError("이 임무의 시설 데이터가 없습니다. 기존 기록은 보존됩니다.") from e
    potential = None
    if (folder/"potential.npy").exists():
        # Legacy 한림읍 snapshot: geojson + frozen window potential are hashed together.
        field = (folder/"potential.npy").read_bytes()
        if hashlib.sha256(raw+field).hexdigest() != digest:
            raise ValueError("시설 데이터가 변경되어 계산할 수 없습니다.")
        potential = np.load(folder/"potential.npy", allow_pickle=False)
        if potential.shape != (core.N, core.N) or not np.isfinite(potential).all() or potential.min() < 0 or potential.max() > 1.000001:
            raise ValueError("시설 영향 격자가 올바르지 않습니다.")
        potential.setflags(write=False)
    else:
        if hashlib.sha256(raw+json.dumps(manifest.get("terrain_sha256",{}),sort_keys=True).encode()).hexdigest() != digest:
            raise ValueError("시설 데이터가 변경되어 계산할 수 없습니다.")
    return potential, json.loads(raw), manifest


def window_mask(features, row0, col0, margin_cells=0):
    """Boolean mask of features whose point lies in the window (+margin), vectorised."""
    if not features: return np.zeros(0, bool)
    m = core.master()
    coords = np.array([f["geometry"]["coordinates"] for f in features], dtype=float)
    r, c = m.rc(coords[:,0], coords[:,1])
    return ((row0-margin_cells <= r) & (r < row0+core.N+margin_cells) & (col0-margin_cells <= c) & (c < col0+core.N+margin_cells))


def features_in_window(geo, row0, col0, margin_cells=0):
    keep = window_mask(geo["features"], row0, col0, margin_cells)
    return [f for f, k in zip(geo["features"], keep) if k]


@lru_cache(maxsize=16)
def window_field(digest, row0, col0):
    """Potential for one analysis window, routed on a margin-padded cut of the master grid."""
    potential, geo, manifest = snapshot(digest)
    if potential is not None:
        if (row0, col0) != core.LEGACY_WINDOW:
            raise ValueError("이 시설 데이터는 원래 한림읍 분석 창에서만 쓸 수 있습니다. 새 시설 버전으로 임무를 만들어 주세요.")
        return potential
    m = core.master()
    r0, r1 = max(0, row0-MARGIN), min(m.height, row0+core.N+MARGIN)
    c0, c1 = max(0, col0-MARGIN), min(m.width, col0+core.N+MARGIN)
    sl = (slice(r0,r1), slice(c0,c1))
    lc = m.lc[sl]
    speed = np.ones(lc.shape)
    speed[lc==10]=.48; speed[lc==20]=.65; speed[lc==40]=.8; speed[m.roads[sl]<30]=1.05
    padded = SimpleNamespace(water=lc==80, slope=m.slope[sl].astype(float), lc=lc, dem=m.dem[sl].astype(float), speed=speed,
                             rc=lambda lon,lat: tuple(v-o for v,o in zip(m.rc(lon,lat),(r0,c0))))
    features = [copy.deepcopy(f) for f in features_in_window(geo, row0, col0, MARGIN) if f["properties"].get("model_eligible")]
    field = make_potential(padded, features)[row0-r0:row0-r0+core.N, col0-c0:col0-c0+core.N]
    field = np.ascontiguousarray(field); field.setflags(write=False)
    return field


def check_terrain(manifest, legacy):
    expected = manifest.get("terrain_sha256", {})
    if legacy:
        for name, digest in expected.items():
            if name not in ("dem.npy","landcover.npy","slope.npy","road_distance.npy"):
                raise ValueError("시설 데이터의 지형 출처가 올바르지 않습니다.")
            path = core.LEGACY_DATA/name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError("시설 격자를 만든 지형과 현재 지형이 다릅니다. 새 시설 버전을 준비해 주세요.")
        return
    current = core.data_file_hashes()
    for name, digest in expected.items():
        if name not in current: raise ValueError("시설 데이터의 지형 출처가 올바르지 않습니다.")
        if current[name]!=digest: raise ValueError("시설 격자를 만든 지형과 현재 지형이 다릅니다. 새 시설 버전을 준비해 주세요.")


def resolve(params, terrain=None):
    strength = float(params.get("facility_strength", 0))
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("시설 영향 강도는 0~1이어야 합니다.")
    if not params.get("facility_influence", False) or strength == 0:
        return None
    digest = params.get("facility_snapshot")
    if not digest:
        raise ValueError("시설 영향 계산에는 고정된 시설 데이터 버전이 필요합니다.")
    potential, _, manifest = snapshot(digest)
    check_terrain(manifest, legacy=potential is not None)
    row0, col0 = (terrain.row0, terrain.col0) if terrain is not None else core.window_of(params)
    return window_field(digest, row0, col0)


def bias(field, strength, r, c, nr, nc):
    if field is None or strength == 0: return 0.0
    # Do not discourage exits using a fabricated boundary facility value.
    inside = (nr >= 0) & (nr < field.shape[0]) & (nc >= 0) & (nc < field.shape[1])
    sr, sc = np.clip(nr, 0, field.shape[0]-1), np.clip(nc, 0, field.shape[1]-1)
    delta = field[sr, sc]-field[r, c]
    return np.where(inside, np.clip(8*strength*delta, -MAX_BIAS, MAX_BIAS), 0.0)
