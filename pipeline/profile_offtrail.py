"""Audit author LAS trajectories; never infer time units or emit training labels.

Run with .venv-ml/bin/python -m pipeline.profile_offtrail. The archive remains
unmodified and is read in memory. All numbers below describe the archive, not
the paper's final analysis sample or independent lost-person incidents.
"""
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'data/research/offtrail_lidar_2025'
NAME = re.compile(r't(?P<course_token>\d+[a-z]?)(?P<letter_token>[A-Z])_(?P<direction>up|down)\.las\.las')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def statistics(values):
    values = np.asarray(values, dtype=float).ravel()
    finite = values[np.isfinite(values)]
    result = {'count': len(values), 'nonfinite': int(len(values) - len(finite)),
              'unique_finite': int(len(np.unique(finite))),
              'zero': int(np.count_nonzero(finite == 0)),
              'negative': int(np.count_nonzero(finite < 0))}
    if not len(finite):
        return result
    result.update(min=float(finite.min()), max=float(finite.max()),
                  mean=float(finite.mean()), median=float(np.median(finite)),
                  population_std=float(finite.std()))
    result['percentiles'] = {str(q): float(np.percentile(finite, q))
                             for q in (1, 5, 25, 50, 75, 95, 99)}
    return result


def clock_diagnostic(offset, gps, pair_error_bound=128.):
    """Necessary pairwise scale bounds under an EXPLICIT, unverified clock model.

    Suppose gps is seconds rounded with <=64 s per-point error and true time
    is origin + scale*offset. Then each pair constrains scale by +/-128 s.
    This is a diagnostic, NOT proof of seconds, a confidence interval, or a
    correction. The assumed error mechanism has not been confirmed by authors.
    """
    offset, gps = np.asarray(offset, float), np.asarray(gps, float)
    if offset.shape != gps.shape or offset.ndim != 1:
        raise ValueError('Clock arrays must be equal-length vectors')
    lo, hi, pairs = 0., float('inf'), 0
    for i in range(len(offset) - 1):
        dx, dy = offset[i+1:] - offset[i], gps[i+1:] - gps[i]
        good = np.isfinite(dx) & np.isfinite(dy) & (dx > 0)
        if good.any():
            lo = max(lo, float(np.max((dy[good] - pair_error_bound) / dx[good])))
            hi = min(hi, float(np.min((dy[good] + pair_error_bound) / dx[good])))
            pairs += int(good.sum())
    return {'lower': lo, 'upper': hi if np.isfinite(hi) else None,
            'pairs_checked': pairs, 'compatible_under_assumption': lo <= hi}


def verified_archive(folder):
    manifest_raw = (folder / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest['license'] != 'CC-BY-4.0':
        raise ValueError('Unexpected source license')
    sources = []
    for entry in manifest['files']:
        if entry['path'] not in ('record.json', 'trajectories.zip'):
            raise ValueError('Unexpected source path')
        raw = (folder / entry['path']).read_bytes()
        if sha(raw) != entry['sha256']:
            raise ValueError('Source SHA-256 mismatch: ' + entry['path'])
        sources.append({'path': entry['path'], 'sha256': sha(raw)})
    raw = (folder / 'trajectories.zip').read_bytes()
    record = json.loads((folder / 'record.json').read_bytes())
    author = next(f for f in record['files'] if f['key'] == 'trajectories.zip')
    if len(raw) != author['size'] or 'md5:' + hashlib.md5(raw).hexdigest() != author['checksum']:
        raise ValueError('Author size/MD5 mismatch')
    sources.append({'path': 'manifest.json', 'sha256': sha(manifest_raw)})
    archive = zipfile.ZipFile(io.BytesIO(raw))
    entries = [e for e in archive.infolist() if not e.is_dir()]
    if len(set(e.filename for e in entries)) != len(entries):
        raise ValueError('Duplicate archive paths')
    if sum(e.file_size for e in entries) > 20_000_000:
        raise ValueError('Archive exceeds inspection limit')
    expected = {e['name']: e for e in manifest['archive_inventory']}
    if set(expected) != {e.filename for e in entries}:
        raise ValueError('Archive inventory mismatch')
    for entry in entries:
        body = archive.read(entry.filename)
        item = expected[entry.filename]
        if len(body) != item['bytes'] or sha(body) != item['sha256']:
            raise ValueError('Archive member mismatch: ' + entry.filename)
    return archive, sources


def profile(folder=FOLDER):
    archive, sources = verified_archive(folder)
    # This optional reader belongs to the training environment, not the webapp.
    import laspy

    arrays, schemas = defaultdict(list), Counter()
    rows, unparsed, hashes = [], [], defaultdict(list)
    crs_counts, formats, token_counts = Counter(), Counter(), defaultdict(Counter)
    offset_steps, gps_steps = [], []
    clock_low, clock_high, clock_pairs = 0., float('inf'), 0
    with archive:
        for filename in sorted(archive.namelist()):
            if filename.endswith('/'):
                continue
            raw = archive.read(filename)
            if raw[:4] != b'LASF':
                raise ValueError('Unexpected non-LAS member: ' + filename)
            las = laspy.read(io.BytesIO(raw))
            match = NAME.fullmatch(PurePosixPath(filename).name)
            tokens = match.groupdict() if match else {}
            if not match:
                unparsed.append(filename)
            for name, value in tokens.items():
                token_counts[name][value] += 1
            hashes[sha(raw)].append(filename)
            crs = las.header.parse_crs()
            crs_name = crs.to_string() if crs else 'UNSPECIFIED'
            crs_counts[crs_name] += 1
            formats[f'{las.header.version}/point-format-{las.point_format.id}'] += 1
            dimensions = {name: np.asarray(las[name], float)
                          for name in las.point_format.dimension_names}
            # Preserve raw integer coordinates and add scaled axes separately.
            dimensions.update(x=np.asarray(las.x), y=np.asarray(las.y), z=np.asarray(las.z))
            for name, values in dimensions.items():
                arrays[name].append(values)
            extra = [{'name': e.name, 'dtype': str(e.dtype), 'description': e.description}
                     for e in las.point_format.extra_dimensions]
            schemas[json.dumps(extra, sort_keys=True)] += 1
            if not {'GpsTime', 'OffsetTime'} <= set(dimensions):
                raise ValueError('Missing source time fields')
            offset, gps = dimensions['OffsetTime'], dimensions['GpsTime']
            do, dg = np.diff(offset), np.diff(gps)
            offset_steps.append(do)
            gps_steps.append(dg)
            bounds = clock_diagnostic(offset, gps)
            clock_low = max(clock_low, bounds['lower'])
            if bounds['upper'] is not None:
                clock_high = min(clock_high, bounds['upper'])
            clock_pairs += bounds['pairs_checked']
            xy = np.column_stack((las.x, las.y))
            rows.append({'path': filename, 'sha256': sha(raw), 'points': len(las.points),
                         'filename_tokens': tokens, 'crs': crs_name,
                         'coordinate_axis_units': [] if crs is None else [a.unit_name for a in crs.axis_info],
                         'offset_first_last': [float(offset[0]), float(offset[-1])],
                         'gps_first_last': [float(gps[0]), float(gps[-1])],
                         'offset_nonincreasing_pairs': int(np.count_nonzero(do <= 0)),
                         'gps_nonincreasing_pairs': int(np.count_nonzero(dg <= 0)),
                         'horizontal_path_length_crs_units': float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum()),
                         'clock_scale_diagnostic': bounds,
                         'speed_training_eligible': False, 'lost_person_training_eligible': False})
    do, dg = np.concatenate(offset_steps), np.concatenate(gps_steps)
    report = {
        'schema': 'offtrail-quality-v1', 'doi': '10.5281/zenodo.17081136',
        'source_files': sources, 'profiler_sha256': sha(Path(__file__).read_bytes()),
        'dependencies': {name: version(name) for name in ('laspy', 'numpy', 'pyproj')},
        'status': 'QUARANTINED_TIME_UNITS_AND_FEATURES_UNVERIFIED',
        'trained_bundle_modified': False, 'training_rows_emitted': 0,
        'grain': 'One scanner trajectory point, keyed by archive member and zero-based point index.',
        'files': len(rows), 'points': sum(r['points'] for r in rows),
        'consecutive_pairs': sum(max(0, r['points'] - 1) for r in rows),
        'crs_counts': dict(crs_counts), 'las_formats': dict(formats),
        'exact_duplicate_files': [names for names in hashes.values() if len(names) > 1],
        'unparsed_filenames': unparsed,
        'filename_token_counts': {key: dict(sorted(count.items())) for key, count in token_counts.items()},
        'token_semantics_verified': False,
        'extra_dimension_schemas': [{'files': n, 'dimensions': json.loads(s)} for s, n in schemas.items()],
        'numeric_fields': {name: statistics(np.concatenate(values)) for name, values in arrays.items()},
        'offset_deltas': statistics(do), 'gps_deltas': statistics(dg),
        'clock_diagnostic': {
            'assumption': 'GpsTime is seconds rounded with at most 64 seconds error per point; time is affine in OffsetTime.',
            'assumption_verified': False, 'units_verified': False,
            'pair_error_bound': 128, 'pairs_checked': clock_pairs,
            'necessary_scale_lower': clock_low,
            'necessary_scale_upper': clock_high if np.isfinite(clock_high) else None,
            'compatible_under_assumption': clock_low <= clock_high,
            'milliseconds_compatible_under_assumption': clock_low <= .001 <= clock_high,
            'seconds_compatible_under_assumption': clock_low <= 1 <= clock_high,
            'interpretation': 'Consistency diagnostic only. Not a confidence interval, unit confirmation, or conversion permission.'},
        'gates': [
            {'id': 'TIME_UNITS', 'passed': False,
             'reason': 'OffsetTime description says milliseconds; its relation to GpsTime requires author or processing-code confirmation.'},
            {'id': 'INDEPENDENT_TERRAIN_INPUTS', 'passed': False,
             'reason': 'Trajectory LAS contains no independent airborne terrain, vegetation-density or surface-roughness predictors. Future trajectory differences must not replace independent inputs.'},
            {'id': 'PERSON_COURSE_GROUPS', 'passed': False,
             'reason': 'Filename tokens are observed, but a participant/transect dictionary and analysis-file inclusion list are not verified.'},
            {'id': 'LOST_PERSON_TARGET', 'passed': False,
             'reason': 'Source is a controlled walking experiment, not lost-person paths, facility choices, or search detection outcomes.'}],
        'column_roles': {'identifier': ['archive_member', 'point_index'],
                         'unverified_dimensions': ['course_token', 'letter_token', 'direction'],
                         'temporal_unverified': ['GpsTime', 'OffsetTime'],
                         'spatial_measurements': ['x', 'y', 'z'],
                         'orientation_measurements': ['Pitch', 'Roll', 'yaw', 'rot.z']},
        'recommended_next_actions': [
            'Obtain a verified OffsetTime unit and GpsTime epoch/precision explanation.',
            'Obtain file-to-participant/course mapping and the paper analysis inclusion list.',
            'Join independent terrain rasters with source date, CRS and license; do not derive predictor slope from future path.',
            'Only then preregister grouped person and course evaluation for a walking-speed component, not a lost-person policy.'],
        'tracks': rows,
    }
    # Fail rather than publish NaN/Infinity as if they were valid audit data.
    json.dumps(report, allow_nan=False)
    return report


def main():
    report = profile()
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
    fd, tmp = tempfile.mkstemp(prefix='.quality-', dir=FOLDER)
    with os.fdopen(fd, 'wb') as out:
        out.write(raw)
    os.replace(tmp, FOLDER / 'quality_report.json')
    print(json.dumps({k: report[k] for k in ('status', 'files', 'points', 'consecutive_pairs', 'training_rows_emitted')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
