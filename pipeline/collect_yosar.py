"""Read-only, field-minimized YOSAR endpoint collection and quarantine report.

Journey lines are NOT movement trajectories. This collection never changes the
trained bundle and is kept separate because its license is CC-BY-NC-SA-4.0.
Run with .venv/bin/python -m pipeline.collect_yosar.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import csv
import hashlib
import json
import urllib.request
import numpy as np
from pyproj import Geod

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/yosar_endpoints_2000_2010'
BASE = 'https://services5.arcgis.com/ZY2TU3F6m1lSbSrn/ArcGIS/rest/services/YOSAR_MissingPersonData_v1/FeatureServer'
# No names, narratives, contacts, ages, medical information, or attachments.
FIELDS = ['OBJECTID', 'CaseNumb', 'CaseNumb_1', 'Type', 'Type_1',
          'Lat', 'Long', 'Lat_1', 'Long_1', 'Georef_U', 'Georef_U_1',
          'DateTime', 'DateTime_1', 'DateTime_2', 'DateTIme_3', 'TotalTim', 'TotalSea']


def fetch(url, name):
    path = OUT / name
    if not path.exists():
        req = urllib.request.Request(url, headers={'User-Agent': 'SearchProof educational research'})
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError('Dataset exceeds download limit')
        data = json.loads(raw)
        if 'error' in data or data.get('exceededTransferLimit'):
            raise ValueError('Incomplete ArcGIS response')
        path.write_bytes(raw)
    raw = path.read_bytes()
    return json.loads(raw), {'path': name, 'url': url, 'bytes': len(raw),
                             'sha256': hashlib.sha256(raw).hexdigest()}


def profile(data):
    rows = data['features']
    attrs = [r['attributes'] for r in rows]
    counts = Counter(a['CaseNumb'] for a in attrs)
    geod = Geod(ellps='WGS84')
    prepared = []
    for row in rows:
        a = row['attributes']
        reasons = []
        paths = row.get('geometry', {}).get('paths', [])
        if a['CaseNumb'] != a['CaseNumb_1']: reasons.append('CROSS_CASE_JOIN')
        if counts[a['CaseNumb']] != 1: reasons.append('MULTIPLE_PAIRS_PER_CASE')
        if a['Type'] != 'IPP' or a['Type_1'] != 'Found': reasons.append('NON_PRIMARY_ENDPOINT_TYPE')
        coords = [a[k] for k in ['Long', 'Lat', 'Long_1', 'Lat_1']]
        if any(v is None for v in coords) or not np.isfinite(coords).all():
            distance = None
            reasons.append('MISSING_COORDINATE')
        else:
            distance = float(geod.inv(*coords)[2])
            if distance <= 0: reasons.append('ZERO_DISTANCE_CENSORED')
        if len(paths) != 1 or len(paths[0]) != 2: reasons.append('UNEXPECTED_GEOMETRY')
        prepared.append({'source_oid': a['OBJECTID'],
                         'case_key': hashlib.sha256(('YOSAR:'+str(a['CaseNumb'])).encode()).hexdigest()[:20],
                         'distance_m': distance, 'ipp_uncertainty_source': a['Georef_U'],
                         'found_uncertainty_source': a['Georef_U_1'],
                         'endpoint_candidate': not reasons, 'flags': reasons,
                         'time_training_eligible': False, 'trajectory_training_eligible': False})
    date_fields = ['DateTime', 'DateTime_1', 'DateTime_2', 'DateTIme_3']
    summary = {
        'source_rows': len(rows), 'unique_source_case_keys': len(counts),
        'cases_with_multiple_pairs': sum(n > 1 for n in counts.values()),
        'all_lines_are_two_vertex_endpoint_links': all(len(r.get('geometry', {}).get('paths', [])) == 1 and
                                                      len(r['geometry']['paths'][0]) == 2 for r in rows),
        'cross_case_joins': sum(a['CaseNumb'] != a['CaseNumb_1'] for a in attrs),
        'candidate_single_pair_endpoints': sum(r['endpoint_candidate'] for r in prepared),
        'row_exclusion_counts': dict(Counter(flag for r in prepared for flag in r['flags'])),
        'date_fields': {k: {'non_null': sum(a[k] is not None for a in attrs),
                            'at_utc_midnight': sum(a[k] is not None and a[k] % 86400000 == 0 for a in attrs)} for k in date_fields},
        'total_fields': {k: {'negative': sum(a[k] is not None and a[k] < 0 for a in attrs),
                             'zero': sum(a[k] == 0 for a in attrs),
                             'units_verified': False} for k in ['TotalTim', 'TotalSea']},
        'training_status': 'QUARANTINED_NOT_USED_TO_FIT_OR_SELECT_MODEL',
        'reasons': ['Two-vertex links are not actual paths.',
                    'Time-of-day is not preserved in date fields; total-time fields require source clarification.',
                    'Multiple endpoint pairs are not independent incidents.',
                    'Georeferencing uncertainty must be modeled; zero distances are not arbitrary epsilon values.',
                    'Overlap with existing 65 incident endpoints must be checked before external evaluation.',
                    'CC BY-NC-SA 4.0 restrictions differ from existing training sources.'],
    }
    # Coordinate overlap screen is evidence only; a non-match does not establish independence.
    with (ROOT/'data/research/prepared/incident_endpoints.csv').open(newline='') as f:
        existing = list(csv.DictReader(f))
    near_pairs = 0
    for a in attrs:
        if any(v is None for v in [a['Long'], a['Lat'], a['Long_1'], a['Lat_1']]): continue
        for old in existing:
            start = geod.inv(a['Long'], a['Lat'], float(old['IPP_lon']), float(old['IPP_lat']))[2]
            end = geod.inv(a['Long_1'], a['Lat_1'], float(old['find_lon']), float(old['find_lat']))[2]
            if start <= 1000 and end <= 1000:
                near_pairs += 1
                break
    summary['possible_existing_overlap_within_1km_both_endpoints'] = near_pairs
    return summary, prepared


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    service, source = fetch(BASE+'?f=json', 'service.json')
    if 'NonCommercial-ShareAlike 4.0' not in service.get('description', ''):
        raise ValueError('Expected license absent; manual review required')
    layer, layer_source = fetch(BASE+'/2?f=json', 'layer.json')
    query = BASE+'/2/query?'+urlencode({'f': 'json', 'where': '1=1', 'outFields': ','.join(FIELDS),
                                       'returnGeometry': 'true', 'outSR': '4326', 'orderByFields': 'OBJECTID'})
    data, original = fetch(query, 'endpoint_links.minimized.json')
    report, rows = profile(data)
    (OUT/'quality_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (OUT/'endpoint_candidates.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    manifest = {'retrieved_at': datetime.now(timezone.utc).isoformat(), 'source': BASE,
                'attribution': 'Paul J. Doherty and Jared Doke, NSF #1031914',
                'license': 'CC-BY-NC-SA-4.0', 'license_url': 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
                'changes': 'Selected numeric/location/time fields only; derived QA flags and hashed case keys.',
                'files': [source, layer_source, original], 'trained_bundle_modified': False,
                'status': report['training_status']}
    (OUT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
