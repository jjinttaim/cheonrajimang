"""Time-matched candidate effort on coherent weighted target paths.

Paths and detection widths are model assumptions, not observed lost people.
"""
from dataclasses import dataclass
import hashlib
import numpy as np
from . import core

BIN_SECONDS = 15
MAX_PACKETS = 131072
# Budget for retained NumPy path arrays, not a total process-RAM guarantee.
# The two-entry plan cache retains at most twice this amount of path arrays.
PATH_ARRAY_BUDGET_BYTES = 256 * 1024 * 1024
OUTSIDE = core.N*core.N


def sample_budgets(populations, frames, budget_bytes=PATH_ARRAY_BUDGET_BYTES):
    """Use all original paths if possible; water-fill a shared memory budget.

    A path needs two int32 cell arrays and one float64 fraction per frame, plus
    one float64 weight. Small scenarios keep all paths rather than wasting an
    equal allocation. Counts are numerical representation sizes, not incidents.
    """
    if (not isinstance(frames,(int,np.integer)) or isinstance(frames,bool) or frames<1 or
        not isinstance(budget_bytes,(int,np.integer)) or isinstance(budget_bytes,bool) or
        not populations or any(not isinstance(n,(int,np.integer)) or isinstance(n,bool) or
                               not 0<=n<=2*OUTSIDE for n in populations)):
        raise ValueError('잘못된 경로 메모리 예산 입력')
    populations = [int(n) for n in populations]
    slots = (budget_bytes - 8 * frames * len(populations)) // (16 * frames + 8)
    if slots < sum(n > 0 for n in populations):
        raise ValueError('시간 경로를 기록할 메모리 예산이 부족합니다.')
    low, high = 0, MAX_PACKETS
    while low < high:
        mid = (low + high + 1) // 2
        if sum(min(n, mid) for n in populations) <= slots: low = mid
        else: high = mid - 1
    counts = [min(n, low) for n in populations]
    remaining = slots - sum(counts)
    for i, n in enumerate(populations):
        if remaining and counts[i] < min(n, MAX_PACKETS):
            counts[i] += 1
            remaining -= 1
    return [None if count == n else count for count, n in zip(counts, populations)]


def cell_ids(rows, cols):
    inside=(rows>=0)&(rows<core.N)&(cols>=0)&(cols<core.N)
    return np.where(inside,rows*core.N+cols,OUTSIDE).astype(np.int32)


@dataclass
class Paths:
    times: np.ndarray
    left: np.ndarray
    right: np.ndarray
    fraction: np.ndarray
    weights: np.ndarray
    outside: float
    sampling: dict | None = None

    def check(self):
        shape=(len(self.times),len(self.weights))
        if (not len(self.times) or not np.isfinite(self.times).all() or np.any(np.diff(self.times)<=0) or
            self.left.shape!=shape or self.right.shape!=shape or self.fraction.shape!=shape or
            not np.isfinite(self.weights).all() or np.any(self.weights<0) or
            not np.isfinite(self.fraction).all() or np.any((self.fraction<0)|(self.fraction>1)) or
            np.any((self.left<0)|(self.left>OUTSIDE)) or np.any((self.right<0)|(self.right>OUTSIDE)) or
            not np.isfinite(self.outside) or self.outside<0 or
            not np.isclose(self.weights.sum()+self.outside,1,atol=1e-8)):
            raise ValueError('잘못된 시간 경로 자료')
        return self

    def distribution(self,index):
        frac=self.fraction[index]
        mass=np.bincount(self.left[index],self.weights*(1-frac),minlength=OUTSIDE+1)
        mass+=np.bincount(self.right[index],self.weights*frac,minlength=OUTSIDE+1)
        return core.Distribution(mass[:-1].reshape(core.N,core.N),float(mass[-1]+self.outside)).check()

    def signature(self):
        h=hashlib.sha256()
        for a in (self.times,self.left,self.right,self.fraction,self.weights,np.array([self.outside])):
            h.update(str(a.shape).encode());h.update(a.dtype.str.encode());h.update(a.tobytes())
        return h.hexdigest()


class Recorder:
    def __init__(self,times,rows,cols):
        self.times=np.asarray(times,float)
        shape=(len(times),len(rows))
        self.left=np.full(shape,OUTSIDE,dtype=np.int32)
        self.right=self.left.copy()
        self.fraction=np.zeros(shape,dtype=np.float64)
        for i,when in enumerate(self.times):
            if when==0:self.left[i]=self.right[i]=cell_ids(rows,cols)

    def record(self,ids,starts,ends,rows,cols,nr,nc,v=None,distance=None,clock=None):
        if not len(ids):return
        lower=np.searchsorted(self.times,float(np.min(starts)),side='right')
        upper=np.searchsorted(self.times,float(np.max(ends)),side='right')
        for i in range(lower,upper):
            when=self.times[i];select=(starts<when)&(when<=ends);chosen=ids[select]
            if not len(chosen):continue
            self.left[i,chosen]=cell_ids(rows[chosen],cols[chosen])
            if v is None:
                self.right[i,chosen]=self.left[i,chosen]
            else:
                elapsed=clock.effective(when)-clock.effective(starts[select]) if clock is not None else when-starts[select]
                self.right[i,chosen]=cell_ids(nr[select],nc[select])
                self.fraction[i,chosen]=np.clip(v[select]*elapsed/distance[select],0,1)

    def finish(self,weights,outside,sampling=None):
        return Paths(self.times,self.left,self.right,self.fraction,weights.copy(),outside,sampling).check()


def exposure(paths,cells,arrival_seconds,band=None,scale=1.):
    """Half each searched edge's effort at both ends, at its midpoint time.

    `band` (optional, shape [len(cells), k], -1 = none) lists the cells swept
    by a searcher line at each walked step; `scale` = n_searchers / k spreads
    the line's n sweep widths evenly over its k band rows. Without a band the
    walked cell alone is swept with scale 1 (single observer).
    """
    cells=np.asarray(cells);arrival=np.asarray(arrival_seconds,float)
    if (cells.ndim!=1 or cells.dtype.kind not in 'iu' or arrival.ndim!=1 or
        len(cells)!=len(arrival) or len(cells)<2 or not np.isfinite(arrival).all() or
        np.any(np.diff(arrival)<=0)):
        raise ValueError('수색 경로와 방문 시각이 일치하지 않습니다.')
    if np.any((cells<0)|(cells>=OUTSIDE)) or arrival[0]<0 or arrival[-1]>paths.times[-1]-paths.times[0]+1e-6:
        raise ValueError('수색 경로가 예측 범위를 벗어났습니다.')
    if not np.isfinite(scale) or scale<=0: raise ValueError('잘못된 탐지 노력 배율')
    rows,cols=np.divmod(cells,core.N)
    if np.any(np.abs(np.diff(rows.astype(int)))+np.abs(np.diff(cols.astype(int)))!=1):
        raise ValueError('수색 경로는 인접한 4방향 셀을 연결해야 합니다.')
    if band is None: band=cells[:,None]
    band=np.asarray(band)
    if band.ndim!=2 or band.shape[0]!=len(cells) or np.any(band>=OUTSIDE): raise ValueError('잘못된 수색 밴드')
    relative=paths.times-paths.times[0]
    mid=(arrival[:-1]+arrival[1:])/2
    bins=np.abs(relative[:,None]-mid).argmin(axis=0)
    result=np.zeros(len(paths.weights))
    for b in np.unique(bins):
        sel=bins==b
        points=np.r_[band[:-1][sel].ravel(),band[1:][sel].ravel()]
        points=points[points>=0]
        if not len(points): continue
        unique,counts=np.unique(points,return_counts=True)
        effort=counts*scale/(2*core.CELL)
        def lookup(ids):
            ix=np.searchsorted(unique,ids);safe=np.minimum(ix,len(unique)-1)
            return np.where((ix<len(unique))&(unique[safe]==ids),effort[safe],0.)
        result+=(1-paths.fraction[b])*lookup(paths.left[b])+paths.fraction[b]*lookup(paths.right[b])
    return result


class Scorer:
    def __init__(self,paths,widths):
        self.paths=[p.check() for p in paths]
        if not self.paths or any(not np.array_equal(p.times,self.paths[0].times) for p in self.paths):
            raise ValueError('시나리오 시간축 불일치')
        self.widths=np.asarray(widths,float)
        self.assigned=[np.zeros(len(p.weights)) for p in self.paths]

    def score(self,cells,arrival_seconds,band=None,scale=1.):
        extra=[exposure(p,cells,arrival_seconds,band,scale) for p in self.paths]
        gains=np.array([np.sum(p.weights[None,:]*np.exp(-self.widths[:,None]*old[None,:])*
                               (-np.expm1(-self.widths[:,None]*new[None,:])),axis=1)
                        for p,old,new in zip(self.paths,self.assigned,extra)])
        return gains,extra

    def accept(self,extra):
        for old,new in zip(self.assigned,extra):old+=new

    def candidate_priority(self):
        # Maximum over time of the scenario consensus (geometric mean), not just t=0.
        priority=np.zeros(1024)
        for i in range(len(self.paths[0].times)):
            masses=[]
            for p in self.paths:
                zones=[]
                for ids in (p.left[i],p.right[i]):
                    r,c=np.divmod(np.minimum(ids,OUTSIDE-1),core.N)
                    zones.append(np.where(ids==OUTSIDE,1024,(r//core.ZONE)*32+c//core.ZONE))
                frac=p.fraction[i]
                z=np.bincount(zones[0],p.weights*(1-frac),minlength=1025)
                z+=np.bincount(zones[1],p.weights*frac,minlength=1025)
                masses.append(z[:1024])
            priority=np.maximum(priority,core.consensus(np.stack(masses)))
        return priority

    def metadata(self):
        effective=[float(p.weights.sum()**2/max(np.square(p.weights).sum(),1e-300)) for p in self.paths]
        gaps=np.diff(self.paths[0].times)
        step=float(gaps.max()) if len(gaps) else 0.
        sampling=[p.sampling or {'method':'UNSPECIFIED','sampled':None} for p in self.paths]
        return {'method':'coherent-path-survival-v2-adaptive','time_step_seconds':step,
                'visit_time_rounding':'NEAREST_FRAME',
                'max_visit_time_error_seconds':step/2,
                'sampling':sampling,'any_sampled':any(s.get('sampled') for s in sampling),
                'path_array_bytes':sum(a.nbytes for p in self.paths for a in
                                       (p.times,p.left,p.right,p.fraction,p.weights)),
                'unique_paths':[len(p.weights) for p in self.paths],
                'effective_paths':effective,
                'low_effective_support':any(n<256 for n in effective),
                'support_warning_threshold':256,'support_threshold_calibrated':False,
                'path_sha256':[p.signature() for p in self.paths],
                'frames':[{'relative_seconds':float(t-self.paths[0].times[0]),
                           'outside_mass':[float(p.outside+np.sum(p.weights*((1-p.fraction[i])*(p.left[i]==OUTSIDE)+p.fraction[i]*(p.right[i]==OUTSIDE)))) for p in self.paths]}
                          for i,t in enumerate(self.paths[0].times)],
                'learned_target_motion':False,'calibrated_detection':False}
