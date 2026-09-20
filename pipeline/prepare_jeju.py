"""Build the island-wide master terrain grid for 제주도 (30 m, EPSG:32652).

The master grid is aligned to the original 한림읍 window so that window is an
exact sub-block (row 704, col 448). Missions cut a 512×512 window from it
(see backend.core.Terrain); the master itself is never simulated on directly.

Outputs (data/jeju):
  dem.npz, landcover.npz, slope.npz, road_distance.npz, road_mask.npz (packed bits)
  roads.geojson.gz, meta.json
Facilities are prepared separately by prepare_facilities.py.
"""
from pathlib import Path
import datetime
import gzip
import hashlib
import json
import sys
import urllib.parse
import urllib.request
import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.features import rasterize
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/jeju"
RES = 30
# Master origin = legacy 한림읍 origin (237930, 3706110) moved 448 cells west and 704 cells north.
X0, Y0 = 237930 - 448 * RES, 3706110 + 704 * RES
WIDTH, HEIGHT = 3200, 2368          # 96.0 km × 71.0 km; multiples of the 64-cell window lattice
LEGACY = {"row0": 704, "col0": 448}  # where the original 512×512 한림읍 window sits in the master
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N33_00_E126_00_DEM/Copernicus_DSM_COG_10_N33_00_E126_00_DEM.tif"
LC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N33E126_Map.tif"
OVERPASS = ["https://overpass.openstreetmap.fr/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass-api.de/api/interpreter"]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def overpass(query, timeout=240):
    last = None
    for endpoint in OVERPASS:
        try:
            print("Overpass:", endpoint, flush=True)
            req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": query}).encode(),
                                         headers={"User-Agent": "Cheonrajimang student prototype"})
            result = json.load(urllib.request.urlopen(req, timeout=timeout))
            if result.get("remark") and "timed out" in result["remark"].lower():
                raise ValueError(result["remark"])
            return result, endpoint
        except Exception as e:
            last = e; print("  unavailable:", type(e).__name__, str(e)[:120], flush=True)
    raise RuntimeError(f"Overpass unavailable: {last}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fwd = Transformer.from_crs(4326, 32652, always_xy=True)
    inv = Transformer.from_crs(32652, 4326, always_xy=True)
    transform = from_origin(X0, Y0, RES, RES)
    corners = [list(inv.transform(x, y)) for x, y in [(X0, Y0), (X0 + WIDTH * RES, Y0), (X0 + WIDTH * RES, Y0 - HEIGHT * RES), (X0, Y0 - HEIGHT * RES)]]
    sources, arrays = [], {}
    for name, url, method, dtype in [("dem", DEM_URL, Resampling.bilinear, "float32"), ("landcover", LC_URL, Resampling.nearest, "uint8")]:
        path = OUT / (name + ".npz")
        if path.exists():
            arr = np.load(path)[name]
        else:
            print("Warping remote COG:", name, flush=True)
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_TIMEOUT="120", GDAL_HTTP_MAX_RETRY="4"):
                with rasterio.open(url) as src:
                    with WarpedVRT(src, crs="EPSG:32652", transform=transform, width=WIDTH, height=HEIGHT, resampling=method) as vrt:
                        arr = vrt.read(1).astype(dtype)
            if name == "landcover":
                arr[arr == 0] = 80   # east of 127°E the tile ends over open sea
            if not np.isfinite(arr).all(): raise RuntimeError("Invalid terrain raster: " + name)
            np.savez_compressed(path, **{name: arr})
        arrays[name] = arr
        sources.append({"name": name, "url": url, "sha256": sha(path), "file": path.name})
    lons, lats = zip(*corners)
    bbox = (min(lats), min(lons), max(lats), max(lons))
    roads_path = OUT / "roads.geojson.gz"
    if roads_path.exists():
        roads = json.loads(gzip.decompress(roads_path.read_bytes()))
        endpoint = roads.get("source", "cached")
    else:
        # Four quadrants keep each Overpass response and the server's runtime bounded.
        feats = []; seen = set()
        midlat, midlon = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        for (s, w, n, e) in [(bbox[0], bbox[1], midlat, midlon), (bbox[0], midlon, midlat, bbox[3]), (midlat, bbox[1], bbox[2], midlon), (midlat, midlon, bbox[2], bbox[3])]:
            q = f'[out:json][timeout:200];way["highway"]({s},{w},{n},{e});out geom;'
            result, endpoint = overpass(q)
            for way in result.get("elements", []):
                if way["id"] in seen: continue
                seen.add(way["id"])
                coords = [[p["lon"], p["lat"]] for p in way.get("geometry", [])]
                if len(coords) > 1:
                    feats.append({"type": "Feature", "properties": {"id": way["id"], "kind": way.get("tags", {}).get("highway"), "name": way.get("tags", {}).get("name", "")},
                                  "geometry": {"type": "LineString", "coordinates": coords}})
            print("  ways so far:", len(feats), flush=True)
        feats.sort(key=lambda f: f["properties"]["id"])
        roads = {"type": "FeatureCollection", "source": endpoint, "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "features": feats}
        roads_path.write_bytes(gzip.compress(json.dumps(roads, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    sources.append({"name": "roads", "url": "https://www.openstreetmap.org/copyright", "sha256": sha(roads_path), "file": roads_path.name, "hash_scope": "roads.geojson.gz local OSM extract", "endpoint": endpoint})
    shapes = []
    for feature in roads["features"]:
        xy = [list(fwd.transform(*p)) for p in feature["geometry"]["coordinates"]]
        shapes.append(({"type": "LineString", "coordinates": xy}, 1))
    print("Rasterizing", len(shapes), "ways", flush=True)
    road_mask = rasterize(shapes, out_shape=(HEIGHT, WIDTH), transform=transform, all_touched=True).astype(bool) if shapes else np.zeros((HEIGHT, WIDTH), bool)
    np.savez_compressed(OUT / "road_mask.npz", bits=np.packbits(road_mask, axis=1), shape=np.array(road_mask.shape))
    print("Distance transform", flush=True)
    road_distance = (distance_transform_edt(~road_mask) * RES).astype("float32") if shapes else np.full((HEIGHT, WIDTH), 1e6, "float32")
    np.savez_compressed(OUT / "road_distance.npz", road_distance=road_distance)
    dem = arrays["dem"]
    gy, gx = np.gradient(dem, RES)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
    np.savez_compressed(OUT / "slope.npz", slope=slope)
    meta = {"region": "제주도", "nx": WIDTH, "ny": HEIGHT, "res_m": RES, "crs": "EPSG:32652", "x0": X0, "y0": Y0,
            "corners": corners, "legacy_window": LEGACY, "window_cells": 512, "window_lattice_cells": 64,
            "sources": sources, "road_count": len(shapes),
            "prepared_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "attribution": "Copernicus DEM GLO-30 © DLR e.V. 2010–2014 / © Airbus Defence and Space GmbH 2014–2018; ESA WorldCover 2021 © ESA (CC BY 4.0); © OpenStreetMap contributors (ODbL)",
            "limitations": ["DSM에는 건물과 식생 높이가 포함됩니다.", "WorldCover 2021은 현재 토지 상태와 다를 수 있습니다.", "접근성은 현장 확인이 필요한 지형 기반 초안입니다.", "127°E 동쪽 가장자리(우도 동쪽 바다)는 표고 타일 밖이라 해수면·물로 채웠습니다."]}
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps({"ready": True, "shape": [HEIGHT, WIDTH], "roads": len(shapes), "dem_range": [float(dem.min()), float(dem.max())],
                      "land_cells": int((arrays["landcover"] != 80).sum())}), flush=True)


if __name__ == "__main__": main()
