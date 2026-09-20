"""Numerical core. All behavior/detection parameters are uncalibrated demo assumptions."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import io
import os
import json
import hashlib
from datetime import datetime, timezone, timedelta
import numpy as np
from pyproj import Transformer
from scipy.ndimage import gaussian_filter
from PIL import Image
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
# Island-wide master grid (제주도, 30 m). Every mission computes on a 512×512
# window cut from it; the window origin sits on a 64-cell lattice so the
# original 한림읍 window (row 704, col 448) is one of the lattice positions.
DATA = ROOT / "data/jeju"
LEGACY_DATA = ROOT / "data/hallim"
ENGINE = "searchproof-0.4.0"
N, CELL, ZONE = 512, 30, 16
LATTICE = 64
LEGACY_WINDOW = (704, 448)
MASTER_FILES = ("dem.npz", "landcover.npz", "slope.npz", "road_distance.npz", "road_mask.npz", "meta.json")
WEIGHTS = np.array([.5, .25, .25])
MODELS = ("a", "b", "b2")
ASSUMPTIONS = {
    "status": "UNCALIBRATED_DEMO",
    "radial_sigma_m_per_sqrt_hour": 700,
    "walking_mobility": .42,
    "detection_width_m": [8, 15, 22],
    "gps_sigma_m": 10,
    "independent_sorties": True,
    "model_family_weights": [0.5, 0.25, 0.25],
    # Rest-of-world (ROW) share: probability the subject is outside every hypothesis
    # the map can represent (vehicle, transport, false last-seen point). Standard
    # search-theory bookkeeping; the default value is a team assumption.
    "row_share_default": .10,
    # Learned distance rings are time-free find distances; the reach cap only
    # removes distances a walker could not have covered in the elapsed time.
    "reach_speed_kmh": 5.0,
    # A team of n searchers abreast contributes n sweep widths spread over a
    # band of (n-1)*spacing metres. The recorder is assumed to walk mid-band.
    "team_band": "n_searchers * sweep_width spread over (n-1)*spacing",
    "note": "수색 훈련용 가정입니다. 문헌 적합, 탐지확률 보정, 실제 사건 검증 전입니다.",
}
DISTANCE_MODELS = ("learned_lognormal", "assumed_sqrt_time")
# "research" keeps every component; "commercial-ready" hides parts whose data or
# tile licences are non-commercial (YOSAR model, EOX imagery, GeoLife lab).
DEPLOY_PROFILE = os.environ.get("SEARCHPROOF_DEPLOY_PROFILE", "research")
if DEPLOY_PROFILE not in ("research", "commercial-ready"): raise RuntimeError("SEARCHPROOF_DEPLOY_PROFILE must be research or commercial-ready")
NONCOMMERCIAL_ENABLED = DEPLOY_PROFILE == "research"

MAX_TEAM_SIZE, MIN_SPACING_M, MAX_SPACING_M = 12, 3., 60.

@dataclass
class Distribution:
    grid: np.ndarray
    outside: float

    def check(self):
        if not np.isfinite(self.grid).all() or not np.isfinite(self.outside):
            raise ValueError("Non-finite probability")
        if self.grid.min() < -1e-12 or self.outside < -1e-12:
            raise ValueError("Negative probability")
        if not np.isclose(self.grid.sum(dtype=np.float64)+self.outside, 1, atol=1e-8):
            raise ValueError("Probability mass not conserved")
        return self

def update(prior, intensity):
    if intensity.shape != prior.grid.shape or not np.isfinite(intensity).all() or (intensity<0).any():
        raise ValueError("Invalid intensity")
    q=prior.grid*np.exp(-intensity)
    z=float(q.sum()+prior.outside)
    if z<=0: raise ValueError("Observation incompatible with all states")
    return Distribution(q/z,prior.outside/z).check()

def combine(distributions):
    return Distribution(sum(w*d.grid for w,d in zip(WEIGHTS,distributions)),float(sum(w*d.outside for w,d in zip(WEIGHTS,distributions)))).check()

def with_row(dist,row):
    """Reserve a rest-of-world share. Negative evidence inside the map can only
    raise this share (see update); it is never redistributed onto the grid."""
    row=float(row)
    if not np.isfinite(row) or not 0<=row<=.5: raise ValueError("모형 밖 잔여확률(ROW)은 0~50% 범위여야 합니다.")
    return Distribution(dist.grid*(1-row),float(row+(1-row)*dist.outside)).check()

def team_band(team_size=1,spacing_m=15.):
    """Validated searcher-line geometry shared by GPX coverage and the planner."""
    n=int(team_size); s=float(spacing_m)
    if not 1<=n<=MAX_TEAM_SIZE: raise ValueError(f"팀 인원은 1~{MAX_TEAM_SIZE}명이어야 합니다.")
    if not np.isfinite(s) or not MIN_SPACING_M<=s<=MAX_SPACING_M: raise ValueError(f"대원 간격은 {MIN_SPACING_M:g}~{MAX_SPACING_M:g} m 범위여야 합니다.")
    width=(n-1)*s
    return {"team_size":n,"spacing_m":s,"band_width_m":width,"band_rows":max(1,int(round(width/CELL))) if n>1 else 1,
            "effort_multiplier":n,"assumption":"RECORDER_MID_BAND_UNIFORM"}

class Master:
    """Whole-island rasters. Never simulated on directly; windows are cut from it."""
    def __init__(self):
        self.meta=json.loads((DATA/"meta.json").read_text())
        self.dem=np.load(DATA/"dem.npz")["dem"]
        self.lc=np.load(DATA/"landcover.npz")["landcover"]
        self.slope=np.load(DATA/"slope.npz")["slope"]
        self.roads=np.load(DATA/"road_distance.npz")["road_distance"]
        packed=np.load(DATA/"road_mask.npz")
        self.road_mask=np.unpackbits(packed["bits"],axis=1)[:,:int(packed["shape"][1])].astype(bool)
        self.height,self.width=self.dem.shape
        for arr in (self.lc,self.slope,self.roads,self.road_mask):
            if arr.shape!=(self.height,self.width): raise ValueError("Master terrain rasters disagree in shape")
        if (self.height-N)%LATTICE or (self.width-N)%LATTICE: raise ValueError("Master grid is not aligned to the window lattice")
        self.fwd=Transformer.from_crs(4326,32652,always_xy=True)
        self.inv=Transformer.from_crs(32652,4326,always_xy=True)

    def rc(self,lon,lat):
        x,y=self.fwd.transform(lon,lat)
        return (self.meta["y0"]-np.asarray(y))/CELL,(np.asarray(x)-self.meta["x0"])/CELL

    def lonlat(self,row,col):
        return self.inv.transform(self.meta["x0"]+np.asarray(col)*CELL,self.meta["y0"]-np.asarray(row)*CELL)

    def bounds(self):
        """Coarse lon/lat box of the master grid, rounded outward to 0.01° so form
        inputs with a fixed decimal step accept every point on the grid."""
        lons,lats=zip(*self.meta["corners"])
        f=lambda v:float(np.floor(v*100)/100); c=lambda v:float(np.ceil(v*100)/100)
        return {"lon":[f(min(lons)),c(max(lons))],"lat":[f(min(lats)),c(max(lats))]}

    def window_origin(self,lon,lat):
        """Lattice window whose centre is nearest the point (within ±32 cells)."""
        r,c=self.rc(lon,lat)
        if not (np.isfinite([r,c]).all() and 0<=r<self.height and 0<=c<self.width):
            raise ValueError("마지막 확인 위치는 제주도 분석 영역 안에 지정해 주세요.")
        row0=int(np.clip(round((float(r)-N/2)/LATTICE)*LATTICE,0,self.height-N))
        col0=int(np.clip(round((float(c)-N/2)/LATTICE)*LATTICE,0,self.width-N))
        return row0,col0

    def window_info(self,row0,col0):
        row0,col0=int(row0),int(col0)
        if row0%LATTICE or col0%LATTICE or not (0<=row0<=self.height-N and 0<=col0<=self.width-N):
            raise ValueError("분석 창 위치가 올바르지 않습니다.")
        x0=self.meta["x0"]+col0*CELL; y0=self.meta["y0"]-row0*CELL
        corners=[list(self.inv.transform(x,y)) for x,y in [(x0,y0),(x0+N*CELL,y0),(x0+N*CELL,y0-N*CELL),(x0,y0-N*CELL)]]
        center=list(self.inv.transform(x0+N*CELL/2,y0-N*CELL/2))
        return {"row0":row0,"col0":col0,"x0":x0,"y0":y0,"nx":N,"ny":N,"res_m":CELL,"size_m":N*CELL,
                "corners":corners,"center":center,"legacy":(row0,col0)==LEGACY_WINDOW}

@lru_cache(maxsize=1)
def master(): return Master()

class Terrain:
    """One 512×512 analysis window (30 m cells) cut from the island master grid."""
    def __init__(self,row0=LEGACY_WINDOW[0],col0=LEGACY_WINDOW[1]):
        m=master()
        self.window=m.window_info(row0,col0)
        self.row0,self.col0=self.window["row0"],self.window["col0"]
        sl=(slice(self.row0,self.row0+N),slice(self.col0,self.col0+N))
        self.meta={**m.meta,**{k:self.window[k] for k in ("x0","y0","nx","ny","res_m","corners","center")},"window":self.window}
        self.dem=np.ascontiguousarray(m.dem[sl])
        self.lc=np.ascontiguousarray(m.lc[sl])
        self.slope=np.ascontiguousarray(m.slope[sl])
        self.roads=np.ascontiguousarray(m.roads[sl])
        self.road_mask=np.ascontiguousarray(m.road_mask[sl])
        self.fwd=m.fwd; self.inv=m.inv
        # Water remains a possible terminal location; access and movement are separate.
        self.water=self.lc==80
        self.hazard=self.water | (self.slope>30) | (self.lc==0)
        self.speed=np.ones((N,N))
        self.speed[self.lc==10]=.48
        self.speed[self.lc==20]=.65
        self.speed[self.lc==40]=.8
        self.speed[self.roads<30]=1.05

    def rc(self,lon,lat):
        x,y=self.fwd.transform(lon,lat)
        return (self.meta["y0"]-np.asarray(y))/CELL,(np.asarray(x)-self.meta["x0"])/CELL

    def lonlat(self,row,col):
        return self.inv.transform(self.meta["x0"]+np.asarray(col)*CELL,self.meta["y0"]-np.asarray(row)*CELL)

    def geometry(self,z):
        zr,zc=divmod(z,32)
        r,c=zr*ZONE,zc*ZONE
        return {"type":"Polygon","coordinates":[[list(self.lonlat(rr,cc)) for rr,cc in [(r,c),(r,c+ZONE),(r+ZONE,c+ZONE),(r+ZONE,c),(r,c)]]]}

@lru_cache(maxsize=12)
def _terrain(row0,col0): return Terrain(row0,col0)

def terrain(row0=None,col0=None):
    """Cached analysis window; no arguments means the original 한림읍 window."""
    if row0 is None or col0 is None: row0,col0=LEGACY_WINDOW
    return _terrain(int(row0),int(col0))

def window_of(params):
    """Window origin recorded in mission params; missions created before the
    island grid carry no window and always mean the original 한림읍 window."""
    w=(params or {}).get("window")
    if not w: return LEGACY_WINDOW
    return int(w["row0"]),int(w["col0"])

def terrain_for(params): return terrain(*window_of(params))

def assign_window(params):
    """Choose and record the analysis window for a new mission from its last-seen point."""
    row0,col0=master().window_origin(params["lon"],params["lat"])
    t=terrain(row0,col0)
    params["window"]=t.window
    return t

@lru_cache(maxsize=1)
def data_file_hashes():
    return {name:hashlib.sha256((DATA/name).read_bytes()).hexdigest() for name in MASTER_FILES}

@lru_cache(maxsize=8)
def basemap_png(row0,col0):
    """Offline context raster for one window (land cover tint, hillshade, roads)."""
    t=terrain(row0,col0)
    palette={10:(179,196,181),20:(195,205,181),30:(211,216,186),40:(221,222,200),50:(222,218,209),60:(227,225,207),80:(175,211,220),90:(177,203,196),95:(177,203,196),100:(215,215,190)}
    rgb=np.full((N,N,3),220.0)
    for code,color in palette.items(): rgb[t.lc==code]=color
    gy,gx=np.gradient(t.dem.astype(float),CELL)
    shade=np.clip(0.95+(gx-gy)*0.13,0.72,1.08)
    rgb*=shade[:,:,None]
    rgb[t.road_mask]=[246,244,230]
    out=io.BytesIO()
    Image.fromarray(np.clip(rgb,0,255).astype("uint8")).resize((1536,1536)).save(out,format="PNG")
    return out.getvalue()

def zone_sum(arr):
    return arr.reshape(32,ZONE,32,ZONE).sum(axis=(1,3)).ravel()

def consensus(masses):
    """Log-linear pooling (geometric mean) of per-scenario zone masses.

    A zone must hold mass under every hypothesis to score at all (any zero
    scenario gives zero), but a single pessimistic hypothesis no longer caps
    the score the way a plain minimum does. This is the ranking rule used for
    zone cards and planner candidates; it is not a probability.
    """
    m=np.asarray(masses,dtype=float)
    if m.ndim!=2 or (m<0).any() or not np.isfinite(m).all(): raise ValueError("Invalid scenario masses")
    with np.errstate(divide="ignore"):
        pooled=np.exp(np.mean(np.log(np.where(m>0,m,np.nan)),axis=0))
    return np.where(np.isnan(pooled),0.,pooled)

def histogram(row,col,total):
    rr,cc=np.floor(row).astype(int),np.floor(col).astype(int)
    inside=(rr>=0)&(rr<N)&(cc>=0)&(cc<N)
    grid=np.bincount(rr[inside]*N+cc[inside],minlength=N*N).reshape(N,N)/total
    return Distribution(grid,float(1-inside.sum()/total)).check()

def radial(t,lon,lat,hours,sigma,seed):
    rng=np.random.default_rng(seed)
    r,c=t.rc(lon,lat)
    n=100000
    spread=np.sqrt((ASSUMPTIONS["radial_sigma_m_per_sqrt_hour"]*np.sqrt(hours))**2+sigma**2)/CELL
    # Samples include outside outcomes; no clipping, mask renormalization, or reach cutoff.
    return histogram(r+rng.normal(0,spread,n),c+rng.normal(0,spread,n),n)

def learned_radial(t,lon,lat,hours,sigma,seed,lognorm_params):
    """Scenario A from the fitted find-distance lognormal (real incidents).

    The fitted distribution has no elapsed-time information; ISRID-style ring
    statistics are used as-is, except that distances no walker could have
    covered in the elapsed time (reach_speed_kmh) are removed by rejection.
    Direction is uniform: the learned model carries no bearing information.
    """
    s,loc,scale=[float(v) for v in lognorm_params]
    if not np.isfinite([s,loc,scale]).all() or s<=0 or loc!=0 or scale<=0: raise ValueError("잘못된 학습 거리 모델 파라미터")
    rng=np.random.default_rng(seed)
    r,c=t.rc(lon,lat)
    n=100000
    reach=ASSUMPTIONS["reach_speed_kmh"]*1000/3600*hours*3600
    distance=np.empty(0)
    for _ in range(64):
        draw=rng.lognormal(np.log(scale),s,n)
        distance=np.concatenate([distance,draw[draw<=reach]])
        if len(distance)>=n: break
    else:
        raise ValueError("학습 거리 분포가 도달 가능 반경 안에 충분히 들어오지 않습니다.")
    distance=distance[:n]
    angle=rng.uniform(0,2*np.pi,n)
    rows=r+(np.sin(angle)*distance+rng.normal(0,sigma,n))/CELL
    cols=c+(np.cos(angle)*distance+rng.normal(0,sigma,n))/CELL
    return histogram(rows,cols,n)

def walk(t,lon,lat,hours,sigma,seed,behavior=False,facility_field=None,facility_strength=0,clock=None):
    rng=np.random.default_rng(seed)
    count=20000
    r0,c0=t.rc(lon,lat)
    r=np.floor(r0+rng.normal(0,sigma/CELL,count)).astype(int)
    c=np.floor(c0+rng.normal(0,sigma/CELL,count)).astype(int)
    heading=rng.integers(0,8,count)
    remaining=np.full(count,hours*3600,dtype=float)
    dr=np.array([-1,-1,0,1,1,1,0,-1])
    dc=np.array([0,1,1,1,0,-1,-1,-1])
    distances=CELL*np.hypot(dr,dc)
    alive=(r>=0)&(r<N)&(c>=0)&(c<N)
    stopped=np.zeros(count,dtype=bool)
    for _ in range(1800):
        ids=np.where(alive & ~stopped & (remaining>=18))[0]
        if not len(ids): break
        rr,cc=r[ids],c[ids]
        terminal=t.water[rr,cc] | (t.slope[rr,cc]>45)
        stopped[ids[terminal]]=True
        ids=ids[~terminal]
        if not len(ids): continue
        rr,cc=r[ids],c[ids]
        turn=rng.choice([-2,-1,0,1,2],size=len(ids),p=[.08,.14,.56,.14,.08] if behavior else [.14,.20,.32,.20,.14])
        d=(heading[ids]+turn)%8
        nr,nc=rr+dr[d],cc+dc[d]
        inside=(nr>=0)&(nr<N)&(nc>=0)&(nc<N)
        sr,sc=np.clip(nr,0,N-1),np.clip(nc,0,N-1)
        gradient=(t.dem[sr,sc]-t.dem[rr,cc])/distances[d]
        v=1.667*np.exp(-3.5*np.abs(gradient+.05))*t.speed[sr,sc]*ASSUMPTIONS["walking_mobility"]
        tau=distances[d]/np.maximum(v,.025)
        if clock is not None:
            tau=clock.travel_seconds(hours*3600-remaining[ids],tau)
        accept=tau<=remaining[ids]
        if behavior:
            # Explicit uncalibrated route-following / downhill preference.
            from .facilities import bias
            attraction=bias(facility_field,facility_strength,rr,cc,nr,nc)
            favor=np.exp(np.clip((t.roads[rr,cc]-t.roads[sr,sc])/90-gradient+attraction,-2,2))
            accept &= rng.random(len(ids))<np.minimum(1,favor)
        # Rejection consumes a pause, never an over-budget completed step.
        remaining[ids]-=np.where(accept,tau,np.minimum(remaining[ids],30))
        moving=ids[accept]
        r[moving],c[moving]=nr[accept],nc[accept]
        heading[ids]=d
        alive[ids[~inside & accept]]=False
    return histogram(r,c,count)

def distance_model_spec(params):
    """Resolve the scenario-A distance model, recording exactly what was used.

    The learned model is the default; it degrades to the assumed √time Gaussian
    only when no verified model bundle is readable, and says so in params.
    """
    choice=params.get("distance_model") or DISTANCE_MODELS[0]
    if choice not in DISTANCE_MODELS: raise ValueError("지원되지 않는 거리 시나리오 모델입니다.")
    spec={"requested":choice,"used":"assumed_sqrt_time","source":"TEAM_ASSUMPTION_700_SQRT_H"}
    if choice=="learned_lognormal":
        from . import ml_models
        try:
            bundle=ml_models.load_bundle()
            model=bundle["endpoint"]["models"]["lognorm"]
            spec.update({"used":"learned_lognormal","bundle_id":bundle["bundle_id"],"params":[float(v) for v in model["params"]],
                         "cases":int(bundle["evaluation"]["endpoint"]["summary"]["cases"]),
                         "source":"FITTED_FIND_DISTANCES_US_HIKERS","reach_speed_kmh":ASSUMPTIONS["reach_speed_kmh"]})
        except (ValueError,KeyError) as e:
            spec["fallback_reason"]=str(e)
    return spec

def compute(params):
    from .facilities import resolve
    from .daylight import clock_for
    t=assign_window(params)
    facility_field=resolve(params,t)
    clock=clock_for(params,params.get("missing_at"),params["hours"]*3600)
    r,c=t.rc(params["lon"],params["lat"])
    if not (0<=r<N and 0<=c<N): raise ValueError("마지막 확인 위치는 제주도 분석 영역 안에 지정해 주세요.")
    spec=distance_model_spec(params)
    params["distance_model_used"]=spec
    if spec["used"]=="learned_lognormal":
        a=learned_radial(t,params["lon"],params["lat"],params["hours"],params["sigma"],params["seed"],spec["params"])
    else:
        a=radial(t,params["lon"],params["lat"],params["hours"],params["sigma"],params["seed"])
    row=params.get("row_share",ASSUMPTIONS["row_share_default"])
    params["row_share"]=float(row)
    priors=[a,
            walk(t,params["lon"],params["lat"],params["hours"],params["sigma"],params["seed"]+1,clock=clock),
            walk(t,params["lon"],params["lat"],params["hours"],params["sigma"],params["seed"]+1,True,
                 facility_field,params.get("facility_strength",0),clock=clock)]
    return [with_row(d,row) for d in priors]

def pack(priors,coverage):
    b=io.BytesIO()
    np.savez_compressed(b,**{m:d.grid for m,d in zip(MODELS,priors)},outside=np.array([d.outside for d in priors]),coverage=coverage)
    return b.getvalue()

def unpack(blob):
    with np.load(io.BytesIO(blob),allow_pickle=False) as f:
        priors=[Distribution(f[m].copy(),float(f["outside"][i])).check() for i,m in enumerate(MODELS)]
        return priors,f["coverage"].copy()

def summarize(priors,coverage,t=None):
    t=t or terrain()
    post=[[update(d,coverage[k]) for d in priors] for k in range(3)]
    mixed=[combine(v) for v in post]
    before=combine(priors)
    ps=np.stack([zone_sum(d.grid) for d in post[1]])
    bands=np.stack([zone_sum(d.grid) for d in mixed])
    prior_mass=zone_sum(before.grid)
    pos=np.stack([zone_sum(before.grid*(-np.expm1(-coverage[k]))) for k in range(3)])
    pod=np.divide(pos,prior_mass[None,:],out=np.zeros_like(pos),where=prior_mass[None,:]>0)
    hazards=zone_sum(t.hazard.astype(float))/(ZONE*ZONE)
    has_track=zone_sum((coverage[1]>.05).astype(float))>0
    # Consensus score across the three dependent scenarios (geometric mean); not confidence votes.
    scores=consensus(ps)*.35
    agreement=np.divide(np.min(ps,axis=0),np.max(ps,axis=0),out=np.zeros(1024),where=np.max(ps,axis=0)>0)
    eligible=hazards<.02
    scores[~eligible]=0
    order=np.argsort(-scores,kind="stable")
    ranks=np.zeros(1024,dtype=int); ranks[order]=np.arange(1,1025)
    features=[]
    for z in range(1024):
        if prior_mass[z]<1e-7 and not has_track[z]: continue
        zr,zc=divmod(z,32)
        props={"id":z,"label":f"{chr(65+zr//26)}{zr%26+1:02d}-{zc+1:02d}","poa":float(bands[1,z]),
               "poa_min":float(bands[:,z].min()),"poa_max":float(bands[:,z].max()),
               "prior_poa":float(prior_mass[z]),"pod_low":float(pod[0,z]),"pod_high":float(pod[2,z]),
               "pos":float(pos[1,z]),"scenario":[float(x) for x in ps[:,z]],
               "rank":int(ranks[z]),"score":float(scores[z]),"agreement":float(agreement[z]),"access":"REVIEW_REQUIRED" if eligible[z] else "AGENCY_ONLY",
               "has_track":bool(has_track[z]),"area_m2":230400}
        features.append({"type":"Feature","properties":props,"geometry":t.geometry(z)})
    features.sort(key=lambda f:f["properties"]["rank"])
    # Comparison is reported per scenario; no independent-model consensus claim.
    outside=[float(d.outside) for d in post[1]]
    water=float(mixed[1].grid[t.water].sum())
    return {"zones":{"type":"FeatureCollection","features":features},"outside":outside,
            "outside_mixed":float(mixed[1].outside),"water_mass":water,
            "inside_mass":float(mixed[1].grid.sum()),"coverage_km2":float(np.count_nonzero(coverage[1]>.01)*CELL*CELL/1e6),
            "searched_zones":int(has_track.sum()),"total_zones":len(features)},post,mixed

def png_for(priors,coverage,layer,phase,reference=None):
    distributions=priors if phase=="prior" else [update(d,coverage[1]) for d in priors]
    if layer=="shadow":
        q=-np.expm1(-coverage[1]); rgb=np.array([19,147,135]); strength=np.sqrt(q)
    elif layer=="consensus":
        masks=[]
        for d in distributions:
            order=np.argsort(d.grid.ravel())[::-1]
            target=d.grid.sum()*.5
            mask=np.zeros(N*N)
            if target>0:
                k=np.searchsorted(np.cumsum(d.grid.ravel()[order]),target)
                mask[order[:k+1]]=1
            masks.append(mask.reshape(N,N))
        votes=sum(masks)
        rgba=np.zeros((N,N,4),dtype="uint8")
        rgba[votes==3]=[36,144,125,150]
        rgba[(votes>0)&(votes<3)]=[239,165,50,105]
        out=io.BytesIO();Image.fromarray(rgba).save(out,format="PNG");return out.getvalue()
    else:
        idx={"a":0,"b":1,"b2":2}.get(layer)
        # Use the prior's scale for both views so opacity isn't silently rescaled.
        d=combine(distributions) if idx is None else distributions[idx]
        color_reference=priors if reference is None else reference
        ref=combine(color_reference) if idx is None else color_reference[idx]
        scale=max(float(np.quantile(ref.grid[ref.grid>0],.97)) if (ref.grid>0).any() else 1,1e-12)
        q=d.grid;strength=np.clip(q/scale,0,1)**.65;rgb=np.array([237,127,46])
    rgba=np.zeros((N,N,4),dtype="uint8");rgba[:,:,:3]=rgb
    rgba[:,:,3]=(np.clip(strength,0,1)*175).astype("uint8")
    out=io.BytesIO();Image.fromarray(rgba).save(out,format="PNG");return out.getvalue()

def parse_gpx(raw):
    if len(raw)>2_000_000: raise ValueError("GPX 파일은 2 MB 이하로 올려 주세요.")
    try: root=ElementTree.fromstring(raw)
    except Exception as e: raise ValueError("올바른 GPX XML 파일이 아닙니다.") from e
    tracks=[]
    for segment in root.findall(".//{*}trkseg"):
        pts=[]
        for point in segment.findall("{*}trkpt"):
            try:
                lon,lat=float(point.attrib["lon"]),float(point.attrib["lat"])
                ts=point.findtext("{*}time")
                time=datetime.fromisoformat(ts.replace("Z","+00:00"))
                if time.tzinfo is None: raise ValueError()
                if not np.isfinite([lon,lat]).all() or not (-180<=lon<=180 and -90<=lat<=90): raise ValueError()
                pts.append([lon,lat,time.timestamp()])
            except Exception as e: raise ValueError("모든 트랙 점에 유효한 좌표와 시간대가 포함된 시간이 필요합니다.") from e
        if len(pts)>1:
            if np.any(np.diff(np.array(pts)[:,2])<=0): raise ValueError("트랙 구간 안의 시각은 증가해야 합니다.")
            tracks.append(pts)
    count=sum(len(p) for p in tracks)
    if count<2 or count>20000: raise ValueError("GPX에는 2~20,000개의 트랙 점이 필요합니다.")
    canonical=json.dumps([[[round(p[0],6),round(p[1],6)] for p in s] for s in tracks],separators=(",",":")).encode()
    return tracks,hashlib.sha256(canonical).hexdigest()

def coverage_for(segments,team_size=1,spacing_m=15.,t=None):
    band=team_band(team_size,spacing_m)
    t=t or terrain()
    effort=np.zeros((N,N))
    distance=0.; gaps=0; excluded=0; inside_points=0
    lines=[]
    for points in segments:
        arr=np.asarray(points)
        x,y=t.fwd.transform(arr[:,0],arr[:,1])
        for i in range(len(arr)-1):
            length=float(np.hypot(x[i+1]-x[i],y[i+1]-y[i]))
            dt=arr[i+1,2]-arr[i,2]
            if dt>120: gaps+=1;continue
            speed=length/dt
            if speed<.08 or speed>3 or length>250:
                excluded+=1;continue
            # Do not draw a solid line across rejected or missing GPS intervals.
            lines.append(arr[i:i+2,:2].tolist())
            steps=max(1,int(np.ceil(length/5)))
            a=(np.arange(steps)+.5)/steps
            xx=x[i]+a*(x[i+1]-x[i]); yy=y[i]+a*(y[i+1]-y[i])
            rr=(t.meta["y0"]-yy)/CELL-.5; cc=(xx-t.meta["x0"])/CELL-.5
            ri,ci=np.floor(rr).astype(int),np.floor(cc).astype(int)
            for dy,dx in [(0,0),(0,1),(1,0),(1,1)]:
                rows,cols=ri+dy,ci+dx
                weight=(1-np.abs(rr-rows))*(1-np.abs(cc-cols))*length/steps
                valid=(rows>=0)&(rows<N)&(cols>=0)&(cols<N)
                np.add.at(effort,(rows[valid],cols[valid]),weight[valid]/(CELL*CELL))
            inside_points+=int(np.count_nonzero((rr>=0)&(rr<N)&(cc>=0)&(cc<N)))
            distance+=length
    if inside_points==0 or effort.sum()==0: raise ValueError("분석 영역 안에 시간과 속도 조건을 만족하는 수색 구간이 없습니다.")
    # Normalized kernel spreads uncertain GPS effort without adding search intensity.
    # Width factors give assumption bands, never a statistical confidence interval.
    # A searcher line adds n sweep widths of effort, spread laterally over its band
    # (uniform band width b has standard deviation b/sqrt(12); combined with GPS error).
    lateral=np.sqrt(ASSUMPTIONS["gps_sigma_m"]**2+band["band_width_m"]**2/12)/CELL
    smooth=gaussian_filter(effort*band["effort_multiplier"],sigma=max(.4,lateral),mode="constant",truncate=3)
    C=np.stack([smooth*w for w in ASSUMPTIONS["detection_width_m"]])
    return C,{"distance_m":round(distance,1),"points":sum(map(len,segments)),"gaps":gaps,"excluded_segments":excluded,
              "coverage_km2":round(float(np.count_nonzero(C[1]>.01)*CELL*CELL/1e6),4),
              "team":band,"geometry":{"type":"MultiLineString","coordinates":lines}}

def demo_gpx(params):
    t=terrain_for(params)
    r,c=t.rc(params["lon"],params["lat"])
    # Synthetic exercise track; never presented as a real team's observation.
    rr=int(r)//ZONE*ZONE+2
    cc=int(c)//ZONE*ZONE+2
    points=[]
    for j in range(7):
        cols=np.linspace(cc,cc+11,34) if j%2==0 else np.linspace(cc+11,cc,34)
        for col in cols: points.append(t.lonlat(rr+j*1.6,col))
    start=(datetime.fromisoformat(params['analysis_at'].replace('Z','+00:00'))+timedelta(seconds=1)) if params.get('analysis_at') else datetime(2026,9,18,0,0,tzinfo=timezone.utc)
    xml=['<?xml version="1.0"?><gpx version="1.1" creator="SearchProof synthetic exercise" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>합성 훈련 트랙</name><trkseg>']
    for i,(lon,lat) in enumerate(points):
        ts=(start+timedelta(seconds=i*25)).isoformat()
        xml.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><time>{ts}</time></trkpt>')
    xml.append("</trkseg></trk></gpx>")
    return "".join(xml).encode()
