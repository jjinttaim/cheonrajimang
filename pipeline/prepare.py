"""Fetch only the Hallim window of public COGs; preserve provenance."""
from pathlib import Path
import hashlib
import json
import datetime
import urllib.request
import urllib.parse
import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.features import rasterize
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/hallim"
N, RES = 512, 30
DEM_URL = "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N33_00_E126_00_DEM/Copernicus_DSM_COG_10_N33_00_E126_00_DEM.tif"
LC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N33E126_Map.tif"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fwd = Transformer.from_crs(4326, 32652, always_xy=True)
    inv = Transformer.from_crs(32652, 4326, always_xy=True)
    cx, cy = fwd.transform(126.265, 33.395)
    x0, y0 = round((cx-N*RES/2)/RES)*RES, round((cy+N*RES/2)/RES)*RES
    transform = from_origin(x0, y0, RES, RES)
    corners = [list(inv.transform(x, y)) for x, y in [(x0,y0),(x0+N*RES,y0),(x0+N*RES,y0-N*RES),(x0,y0-N*RES)]]
    sources, arrays = [], {}
    for name, url, method, dtype in [("dem",DEM_URL,Resampling.bilinear,"float32"),("landcover",LC_URL,Resampling.nearest,"uint8")]:
        path = OUT / (name+".npy")
        if path.exists():
            arr = np.load(path)
        else:
            print("Reading remote window:", name, flush=True)
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_TIMEOUT="45", GDAL_HTTP_MAX_RETRY="2"):
                with rasterio.open(url) as src:
                    with WarpedVRT(src, crs="EPSG:32652", transform=transform, width=N, height=N, resampling=method) as vrt:
                        arr = vrt.read(1).astype(dtype)
            if not np.isfinite(arr).all() or (name=="landcover" and not np.any(arr)):
                raise RuntimeError("Invalid terrain window: "+name)
            np.save(path, arr)
        arrays[name] = arr
        sources.append({"name":name,"url":url,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    roads_path = OUT/"roads.geojson"
    if roads_path.exists():
        roads = json.loads(roads_path.read_text())
    else:
        lons, lats = zip(*corners)
        q = f'[out:json][timeout:40];way["highway"]({min(lats)},{min(lons)},{max(lats)},{max(lons)});out geom;'
        roads = {"type":"FeatureCollection","features":[]}
        for endpoint in ["https://overpass-api.de/api/interpreter","https://overpass.kumi.systems/api/interpreter"]:
            try:
                print("Requesting OSM roads", flush=True)
                req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data":q}).encode(), headers={"User-Agent":"SearchProof student prototype"})
                result=json.load(urllib.request.urlopen(req,timeout=55))
                for way in result.get("elements",[]):
                    coords=[[p["lon"],p["lat"]] for p in way.get("geometry",[])]
                    if len(coords)>1:
                        roads["features"].append({"type":"Feature","properties":{"kind":way["tags"].get("highway"),"name":way["tags"].get("name","")},"geometry":{"type":"LineString","coordinates":coords}})
                break
            except Exception as e:
                print("OSM source unavailable:",str(e),flush=True)
        roads_path.write_text(json.dumps(roads,ensure_ascii=False))
    sources.append({"name":"roads","url":"https://www.openstreetmap.org/copyright","sha256":hashlib.sha256(roads_path.read_bytes()).hexdigest(),"hash_scope":"roads.geojson local OSM extract"})
    shapes = []
    for feature in roads["features"]:
        xy=[list(fwd.transform(*p)) for p in feature["geometry"]["coordinates"]]
        shapes.append(({"type":"LineString","coordinates":xy},1))
    road_mask = rasterize(shapes,out_shape=(N,N),transform=transform,all_touched=True).astype(bool) if shapes else np.zeros((N,N),bool)
    np.save(OUT/"road_distance.npy",distance_transform_edt(~road_mask)*RES if shapes else np.full((N,N),1e6))
    dem, lc = arrays["dem"],arrays["landcover"]
    gy,gx=np.gradient(dem,RES)
    slope=np.degrees(np.arctan(np.hypot(gx,gy)))
    np.save(OUT/"slope.npy",slope.astype("float32"))
    palette={10:(179,196,181),20:(195,205,181),30:(211,216,186),40:(221,222,200),50:(222,218,209),60:(227,225,207),80:(175,211,220),90:(177,203,196),95:(177,203,196),100:(215,215,190)}
    rgb=np.full((N,N,3),220.0)
    for code,color in palette.items(): rgb[lc==code]=color
    shade=np.clip(0.95 + (gx-gy)*0.13,0.72,1.08)
    rgb*=shade[:,:,None]
    rgb[road_mask]=[246,244,230]
    Image.fromarray(np.clip(rgb,0,255).astype("uint8")).resize((1536,1536)).save(OUT/"basemap.png")
    meta={"nx":N,"ny":N,"res_m":RES,"crs":"EPSG:32652","x0":x0,"y0":y0,"center":[126.265,33.395],"corners":corners,"sources":sources,"road_count":len(shapes),"prepared_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"attribution":"Copernicus DEM GLO-30 © DLR e.V. 2010–2014 / © Airbus Defence and Space GmbH 2014–2018; ESA WorldCover 2021 © ESA (CC BY 4.0); © OpenStreetMap contributors (ODbL)","limitations":["DSM에는 건물·식생 높이가 포함됩니다.","WorldCover 2021은 현재 토지 상태와 다를 수 있습니다.","접근성은 현장 확인이 필요한 지형 기반 초안입니다."]}
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    print(json.dumps({"ready":True,"shape":[N,N],"roads":len(shapes),"dem_range":[float(dem.min()),float(dem.max())]}),flush=True)

if __name__=="__main__": main()
