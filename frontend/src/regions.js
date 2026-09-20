// Approximate reference points of 제주특별자치도 읍·면·동 clusters, used only to
// name the area around a mission. Boundaries are irregular, so the nearest
// reference point is a label, not an administrative lookup.
export const REGIONS = [
  { name: '제주시', lon: 126.53, lat: 33.50 },
  { name: '애월읍', lon: 126.33, lat: 33.46 },
  { name: '한림읍', lon: 126.27, lat: 33.41 },
  { name: '한경면', lon: 126.19, lat: 33.33 },
  { name: '대정읍', lon: 126.25, lat: 33.23 },
  { name: '안덕면', lon: 126.35, lat: 33.26 },
  { name: '서귀포시', lon: 126.56, lat: 33.25 },
  { name: '남원읍', lon: 126.72, lat: 33.28 },
  { name: '표선면', lon: 126.83, lat: 33.33 },
  { name: '성산읍', lon: 126.90, lat: 33.43 },
  { name: '구좌읍', lon: 126.79, lat: 33.52 },
  { name: '조천읍', lon: 126.65, lat: 33.52 },
  { name: '우도면', lon: 126.95, lat: 33.50 },
];

export function nearestRegion(lon, lat) {
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return '제주';
  const kx = 92.6, ky = 111.2; // km per degree near 33.4° N
  let best = null, bestD = Infinity;
  for (const r of REGIONS) {
    const d = Math.hypot((r.lon - lon) * kx, (r.lat - lat) * ky);
    if (d < bestD) { bestD = d; best = r; }
  }
  return best ? best.name : '제주';
}

export const autoMissionName = (lon, lat) => nearestRegion(lon, lat) + ' 모의 수색';
