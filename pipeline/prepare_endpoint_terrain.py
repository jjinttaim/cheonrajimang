"""Collect independent DSM inputs for endpoint learning; never alter app data.

The published GLO-90 tile list distinguishes ocean absence from fetch failure.
Only provider-hosted public research terrain is downloaded. Coordinates are not
sent to an API: full named public tiles are fetched, then sampled locally.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import csv
import hashlib
import json
import os
import re
import tempfile
import urllib.request

import numpy as np
import rasterio
import pyproj

from pipeline.terrain_endpoint_model import GEOD, CELLS, centers, target_cell
from pipeline.profile_offtrail import statistics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/endpoint_terrain_2026'
DATA = ROOT / 'data/research/prepared/incident_endpoints.csv'
PROTOCOL = ROOT / 'docs/AI-지형끝점-사전검증계획.md'
SOURCE_HASH = 'afa73cc1cec264aad7b65f8da9cfd19fdbf702b29ad683f9d8372d9149781e21'
BASE = 'https://copernicus-dem-90m.s3.amazonaws.com/'
LICENSE_URL = 'https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf'
STEP = 1 / 1200
TILE_PATTERN = re.compile(r'Copernicus_DSM_COG_30_[NS]\d{2}_00_[EW]\d{3}_00_DEM')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def fetch(name, url, cap=20_000_000):
    assets = OUT / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    folder = assets / name
    if folder.exists():
        receipt = json.loads((folder / 'receipt.json').read_text())
        raw = (folder / 'source').read_bytes()
        if receipt['url'] != url or receipt['sha256'] != sha(raw):
            raise ValueError('Changed cached source: ' + name)
        return folder / 'source', receipt
    request = urllib.request.Request(url, headers={'User-Agent': 'SearchProof educational research'})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read(cap + 1)
        headers = {key: response.headers.get(key) for key in ('ETag', 'Last-Modified', 'Content-Length')}
    if len(raw) > cap or (headers['Content-Length'] and len(raw) != int(headers['Content-Length'])):
        raise ValueError('Source size limit or content length mismatch')
    receipt = {'url': url, 'sha256': sha(raw), 'bytes': len(raw), 'headers': headers,
               'retrieved_at': datetime.now(timezone.utc).isoformat()}
    tmp = Path(tempfile.mkdtemp(prefix='.collect-', dir=assets))
    (tmp / 'source').write_bytes(raw)
    (tmp / 'receipt.json').write_bytes(canonical(receipt))
    os.rename(tmp, folder)
    return folder / 'source', receipt


def tile_name(lat_degree, lon_degree):
    return (f'Copernicus_DSM_COG_30_{"N" if lat_degree >= 0 else "S"}{abs(int(lat_degree)):02d}_00_'
            f'{"E" if lon_degree >= 0 else "W"}{abs(int(lon_degree)):03d}_00_DEM')


def tile_keys(lon, lat):
    # Provider removed the south/east shared rows. Pixel centers are on degree
    # boundaries; coverage is shifted by half a pixel, not simple floor(lat).
    lon, lat = np.asarray(lon), np.asarray(lat)
    if lon.shape != lat.shape or not np.isfinite(lon).all() or not np.isfinite(lat).all():
        raise ValueError('Invalid sample coordinates')
    if (np.abs(lat) >= 50).any() or (np.abs(lon) >= 179).any():
        raise ValueError('This experiment supports the source cases only, below 50 degrees')
    return np.column_stack((np.floor(lat - STEP / 2).astype(int), np.floor(lon + STEP / 2).astype(int)))


def query_points(lat, lon):
    x, y = centers(lat, lon)
    xs, ys = [x], [y]
    for bearing in (90., 270., 0., 180.):
        xx, yy, _ = GEOD.fwd(x, y, np.full(CELLS, bearing), np.full(CELLS, 90.))
        xs.append(xx)
        ys.append(yy)
    xs.append(np.array([lon]))
    ys.append(np.array([lat]))
    return np.concatenate(xs), np.concatenate(ys)


class Sampler:
    def __init__(self, catalog):
        self.catalog = catalog
        self.ocean_samples = 0

    @lru_cache(maxsize=8)
    def read(self, name):
        folder = OUT / 'assets' / name
        receipt = json.loads((folder / 'receipt.json').read_text())
        path = folder / 'source'
        if sha(path.read_bytes()) != receipt['sha256']:
            raise ValueError('Changed tile')
        with rasterio.open(path) as src:
            if src.crs.to_epsg() != 4326 or src.count != 1 or src.width != 1200 or src.height != 1200:
                raise ValueError('Unexpected raster geometry: ' + name)
            if not np.allclose([src.transform.a, src.transform.e], [STEP, -STEP], rtol=0, atol=1e-12):
                raise ValueError('Unexpected pixel spacing')
            return src.read(1, masked=True), src.transform

    def sample(self, lon, lat):
        keys = tile_keys(lon, lat)
        output = np.full(len(lon), np.nan)
        for key in np.unique(keys, axis=0):
            mask = np.all(keys == key, axis=1)
            name = tile_name(*key)
            if name not in self.catalog:
                output[mask] = 0.
                self.ocean_samples += int(mask.sum())
                continue
            data, transform = self.read(name)
            col = np.floor((lon[mask] - transform.c) / transform.a).astype(int)
            row = np.floor((lat[mask] - transform.f) / transform.e).astype(int)
            if ((col < 0) | (row < 0) | (col >= data.shape[1]) | (row >= data.shape[0])).any():
                raise ValueError('Sample outside selected tile; do not clip coordinates')
            values = data[row, col]
            if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                raise ValueError('NoData terrain sample; no zero imputation')
            output[mask] = values
        if not np.isfinite(output).all():
            raise ValueError('Unfilled sample')
        return output


def main():
    raw = DATA.read_bytes()
    if sha(raw) != SOURCE_HASH:
        raise ValueError('Endpoint source changed')
    rows = list(csv.DictReader(raw.decode().splitlines()))
    if len(rows) != 65 or len({r['incident_index'] for r in rows}) != 65:
        raise ValueError('Unexpected endpoint sample')
    protocol_hash = sha(PROTOCOL.read_bytes())
    list_path, list_receipt = fetch('tile-list', BASE + 'tileList.txt', 5_000_000)
    license_path, license_receipt = fetch('license', LICENSE_URL, 5_000_000)
    _, readme_receipt = fetch('provider-readme', BASE + 'readme.html', 1_000_000)
    if not license_path.read_bytes().startswith(b'%PDF'):
        raise ValueError('License was not a PDF')
    catalog = {line.strip().rstrip('/') for line in list_path.read_text().splitlines() if line.strip()}
    if len(catalog) < 10000 or not all(TILE_PATTERN.fullmatch(x) for x in catalog):
        raise ValueError('Unrecognized provider tile catalog')
    needed = set()
    for r in rows:
        lon, lat = query_points(float(r['IPP_lat']), float(r['IPP_lon']))
        needed.update(tile_name(*key) for key in np.unique(tile_keys(lon, lat), axis=0))
    present = sorted(needed & catalog)
    if len(present) > 100:
        raise ValueError('More than 100 tiles requested')
    print(f'Preparing 65 observed cases; {len(present)} DSM tiles; {len(needed-catalog)} catalog-absent ocean tiles', flush=True)
    def download(name):
        _, receipt = fetch(name, BASE + name + '/' + name + '.tif')
        print('Verified DSM tile', name, receipt['bytes'], flush=True)
        return {'name': name, **receipt}
    with ThreadPoolExecutor(max_workers=3) as executor:
        tile_receipts = list(executor.map(download, present))
    sampler = Sampler(catalog)
    all_features, cases = [], []
    for r in rows:
        lat, lon = float(r['IPP_lat']), float(r['IPP_lon'])
        x, y = query_points(lat, lon)
        samples = sampler.sample(x, y)
        z, east, west, north, south = samples[:-1].reshape(5, CELLS)
        slope = np.degrees(np.arctan(np.hypot((east-west)/180, (north-south)/180)))
        features = np.column_stack((np.clip((z-samples[-1])/500, -4, 4), np.clip(slope/30, 0, 3)))
        label, distance = target_cell(lat, lon, float(r['find_lat']), float(r['find_lon']))
        all_features.append(features)
        cases.append({'incident_index': r['incident_index'], 'ipp_lat': lat, 'ipp_lon': lon,
                      'spatial_group': f'{np.floor(lat):.0f}:{np.floor(lon):.0f}',
                      'target_cell': label, 'distance_m': distance,
                      'ipp_height_m': float(samples[-1]), 'center_height_m': statistics(z),
                      'slope_deg': statistics(slope)})
    features = np.array(all_features)
    manifest = {'schema': 'terrain-endpoint-prepared-v1', 'protocol_sha256': protocol_hash,
                'source_sha256': SOURCE_HASH, 'prepare_code_sha256': sha(Path(__file__).read_bytes()),
                'model_code_sha256': sha((ROOT/'pipeline/terrain_endpoint_model.py').read_bytes()),
                'provider': 'Copernicus GLO-90 AWS COG, 2021 release, DSM not bare-earth DTM',
                'source_licenses': ['Incident endpoints: CC-BY-4.0', 'COP-DEM-GLO-90-F free and open license, see assets/license/source'],
                'provider_files': [list_receipt, license_receipt, readme_receipt], 'tiles': tile_receipts,
                'ocean_absent_tiles': sorted(needed-catalog), 'ocean_zero_samples': sampler.ocean_samples,
                'cases': cases, 'shape': list(features.shape),
                'feature_profiles': [statistics(features[:, :, i]) for i in range(2)],
                'features': ['dsm_height_minus_ipp_over_500m_clip4', 'dsm_central90m_slope_over30deg_clip3'],
                'spatial_groups': len({c['spatial_group'] for c in cases}),
                'dependencies': {'rasterio': rasterio.__version__, 'pyproj': pyproj.__version__, 'numpy': np.__version__},
                'not_learned': ['time', 'night', 'facilities', 'roads', 'POD'],
                'source_case_date': 'UNAVAILABLE_TERRAIN_DATE_MATCH_NOT_VERIFIED'}
    temp = Path(tempfile.mkdtemp(prefix='.prepared-', dir=OUT))
    np.savez_compressed(temp/'features.npz', features=features)
    manifest['features_sha256'] = sha((temp/'features.npz').read_bytes())
    data_id = sha(canonical(manifest))
    (temp/'manifest.json').write_bytes(canonical(manifest))
    final = OUT / data_id
    if final.exists():
        # Preserve an existing identical artifact; never overwrite it.
        if (final/'manifest.json').read_bytes() != canonical(manifest):
            raise ValueError('Artifact identity conflict')
    else:
        os.rename(temp, final)
    pointer = OUT/'prepared.json'
    fd, path = tempfile.mkstemp(prefix='.pointer-', dir=OUT)
    with os.fdopen(fd, 'wb') as f:
        f.write(canonical({'data_id': data_id}))
    os.replace(path, pointer)
    print(json.dumps({'prepared': data_id, 'cases': len(cases), 'cells_each': CELLS,
                      'tiles': len(present), 'nonfinite': int((~np.isfinite(features)).sum())}), flush=True)


if __name__ == '__main__':
    main()
