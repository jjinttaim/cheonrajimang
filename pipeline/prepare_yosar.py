"""Prepare uncertain endpoint distances, never invented paths or elapsed times.

Run in the app environment: .venv/bin/python -m pipeline.prepare_yosar.
The original collection and quarantine report are immutable historical records.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from urllib.parse import urlencode
from pyproj import Geod
from pipeline.collect_yosar import ROOT, OUT, BASE, fetch

PROTOCOL = ROOT/'docs/AI-YOSAR-사전검증계획.md'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(data, hiker_ids):
    attrs = [r['attributes'] for r in data['features']]
    if len({a['OBJECTID'] for a in attrs}) != len(attrs):
        raise ValueError('Duplicate source OBJECTID')
    if len(set(hiker_ids)) != len(hiker_ids) or not set(hiker_ids) <= {a['OBJECTID'] for a in attrs}:
        raise ValueError('Incomplete or changed source selection')
    counts = Counter(a['CaseNumb'] for a in attrs)
    geod = Geod(ellps='GRS80')  # Source paper describes NAD83; no fabricated datum correction.
    rows, excluded = [], []
    for a in attrs:
        if a['OBJECTID'] not in hiker_ids:
            continue
        case = hashlib.sha256(('YOSAR:'+str(a['CaseNumb'])).encode()).hexdigest()[:20]
        flags = []
        if a['CaseNumb'] != a['CaseNumb_1']: flags.append('CROSS_CASE_JOIN')
        if counts[a['CaseNumb']] != 1: flags.append('MULTIPLE_ENDPOINTS_UNRESOLVED')
        if a['Type'] != 'IPP' or a['Type_1'] != 'Found': flags.append('NON_PRIMARY_ENDPOINT')
        coords = [a[k] for k in ('Long', 'Lat', 'Long_1', 'Lat_1')]
        errors = [a[k] for k in ('Georef_U', 'Georef_U_1')]
        if any(v is None or not math.isfinite(v) for v in coords):
            flags.append('INVALID_COORDINATE')
        elif not (-180 <= coords[0] <= 180 and -90 <= coords[1] <= 90 and
                  -180 <= coords[2] <= 180 and -90 <= coords[3] <= 90):
            flags.append('INVALID_COORDINATE')
        if any(v is None or not math.isfinite(v) or v <= 0 for v in errors):
            flags.append('INVALID_UNCERTAINTY')
        if flags:
            excluded.append({'source_oid': a['OBJECTID'], 'case_key': case, 'reasons': flags})
            continue
        distance = float(geod.inv(*coords)[2])
        uncertainty = float(sum(errors))
        rows.append({'source_oid': a['OBJECTID'], 'case_key': case,
                     'distance_m': distance, 'uncertainty_sum_m_assumed': uncertainty,
                     'lower_m': max(0., distance-uncertainty), 'upper_m': distance+uncertainty,
                     'spatial_block': f'{math.floor(coords[0]*10)}:{math.floor(coords[1]*10)}'})
    report = {'hiker_source_rows': len(hiker_ids), 'hiker_case_keys': len({a['CaseNumb'] for a in attrs if a['OBJECTID'] in hiker_ids}),
              'used_case_keys': len(rows), 'zero_distance_retained': sum(r['distance_m']==0 for r in rows),
              'excluded_rows': excluded, 'spatial_blocks': len({r['spatial_block'] for r in rows}),
              'paper_hiker_n': 130, 'paper_service_count_match': False,
              'scope': 'SINGLE_PRIMARY_HIKER_ENDPOINTS_NOT_TIME_TRAJECTORIES',
              'uncertainty_units': 'METERS_INFERRED_FROM_SOURCE_PAPER_NOT_FIELD_DICTIONARY',
              'interval_interpretation': 'GEOMETRIC_BOUNDS_NOT_CONFIDENCE_INTERVAL'}
    if len({r['case_key'] for r in rows}) != len(rows):
        raise ValueError('Case duplication after selection')
    return rows, report


def main():
    original_manifest = json.loads((OUT/'manifest.json').read_text())
    for f in original_manifest['files']:
        if digest(OUT/f['path']) != f['sha256']:
            raise ValueError('Original source changed: '+f['path'])
    # Read protocol before requesting the final selection; hash it into provenance.
    protocol_sha = digest(PROTOCOL)
    query = BASE+'/2/query?'+urlencode({'f': 'json', 'where': "SubjectC='Hiker'", 'returnIdsOnly': 'true'})
    selected, selection_source = fetch(query, 'hiker_object_ids.json')
    if set(selected)-{'objectIdFieldName', 'objectIds'}:
        raise ValueError('Unexpected selection response')
    source = OUT/'endpoint_links.minimized.json'
    rows, report = prepare(json.loads(source.read_text()), selected['objectIds'])
    output = OUT/'hiker_intervals.json'
    output.write_text(json.dumps({'rows': rows, 'report': report}, ensure_ascii=False, indent=2))
    adoption = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'source_selection': selection_source,
                'source_sha256': digest(source), 'prepared_sha256': digest(output),
                'protocol_sha256': protocol_sha, 'code_sha256': digest(ROOT/'pipeline/prepare_yosar.py'),
                'attribution': original_manifest['attribution'], 'license': original_manifest['license'],
                'license_url': original_manifest['license_url'], 'report': report,
                'changes': 'Hiker-only single primary endpoint intervals; GRS80 distance; zero distances retained. No time labels.',
                'status': 'PREPARED_FOR_SEPARATE_EXPERIMENTAL_ENDPOINT_FIT'}
    (OUT/'hiker_adoption.json').write_text(json.dumps(adoption, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
