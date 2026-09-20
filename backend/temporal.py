"""Timestamped negative evidence for read-only dynamic projections.

The saved ledger keeps its historical static summary. Dynamic projections replay
only approved observations from the original analysis time, not approval time.
GPS edges are grouped at the END of fixed 60-second bins (at most 60s delay).
Motion/detection remain assumptions, not learned lost-person behavior.
"""
from dataclasses import dataclass
from collections import defaultdict
import math
import numpy as np
from . import core, daylight

MODEL = 'timed-path-survival-v1'
BIN_SECONDS = 60


@dataclass
class Observation:
    seconds: float
    indices: np.ndarray
    values: np.ndarray

    def at(self, rows, cols):
        rows, cols = np.asarray(rows), np.asarray(cols)
        result = np.zeros(rows.shape, dtype=float)
        if not len(self.indices): return result
        valid = (rows >= 0) & (rows < core.N) & (cols >= 0) & (cols < core.N)
        ids = np.flatnonzero(valid)
        cells = rows[ids]*core.N+cols[ids]
        pos = np.searchsorted(self.indices, cells)
        clipped = np.minimum(pos, len(self.indices)-1)
        found = (pos < len(self.indices)) & (self.indices[clipped] == cells)
        result[ids[found]] = self.values[clipped[found]]
        return result


def observations(raw_tracks, base_at, horizon, terrain=None):
    """raw_tracks: GPX bytes, or (bytes, team) pairs where team holds the
    searcher-line geometry recorded with the track (team_size, spacing_m).
    terrain: the mission's analysis window (default: the original 한림읍 window)."""
    base = daylight.aware(base_at).timestamp()
    grouped = defaultdict(list)
    items = [(raw, {}) if isinstance(raw, (bytes, bytearray)) else (raw[0], raw[1] or {}) for raw in raw_tracks]
    # Sorting file bytes makes replay independent of approval/insertion order.
    for raw, team in sorted(items, key=lambda item: item[0]):
        segments, _ = core.parse_gpx(raw)
        if min(p[2] for s in segments for p in s) < base:
            raise ValueError('기준 지도보다 이전에 관찰한 수색 기록이 있습니다. 시간 재생에는 수색 시작 전 기준 지도가 필요합니다. 저장된 정적 요약은 유지됩니다.')
        key = (int(team.get('team_size', 1)), float(team.get('spacing_m', 15.)))
        for segment in segments:
            for first, last in zip(segment, segment[1:]):
                at = math.ceil((last[2]-base)/BIN_SECONDS)*BIN_SECONDS
                if at <= horizon:
                    grouped[(at, key)].append([first, last])
    merged = defaultdict(lambda: np.zeros(core.N*core.N))
    for (at, (size, spacing)), segments in grouped.items():
        coverage, _ = core.coverage_for(segments, size, spacing, terrain)
        merged[at] += coverage[1].ravel()
    events = []
    for at, intensity in sorted(merged.items()):
        indices = np.flatnonzero(intensity)
        if len(indices): events.append(Observation(at, indices, intensity[indices]))
    return events
