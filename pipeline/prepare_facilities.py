"""Fetch a bounded OSM POI extract for the island master grid and freeze it.

The snapshot holds the POI GeoJSON (with island-wide usability flags) and the
hashes of the terrain rasters it was checked against. The walking-time
potential itself is computed per analysis window at run time
(backend.facilities.window_field), so nothing 512×512 is frozen here.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import urllib.parse
import urllib.request
from backend import core, facilities
from backend.facilities import travel_graph, make_potential  # noqa: F401 (re-exported for tests)

OVERPASS = ["https://overpass.openstreetmap.fr/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass-api.de/api/interpreter"]


def main():
    m = core.master()
    lons, lats = zip(*m.meta["corners"])
    bbox = f"{min(lats)},{min(lons)},{max(lats)},{max(lons)}"
    query = f'''[out:json][timeout:180];(
      nwr["shop"~"^(convenience|supermarket)$"]({bbox});
      nwr["amenity"~"^(restaurant|cafe|fast_food|drinking_water|bus_station|shelter|hospital|clinic|pharmacy|police|fire_station)$"]({bbox});
      nwr["highway"="bus_stop"]({bbox});
      nwr["tourism"~"^(hotel|hostel|guest_house)$"]({bbox});
    );out center tags;'''
    result = None
    for endpoint in OVERPASS:
        try:
            request = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"data": query}).encode(),
                                             headers={"User-Agent": "Cheonrajimang local educational prototype"})
            with urllib.request.urlopen(request, timeout=240) as response: result = json.load(response)
            if result.get("remark"): raise ValueError("Incomplete Overpass response: " + result["remark"])
            break
        except Exception as e:
            result = None
            print(type(e).__name__, str(e)[:150], flush=True)
    if result is None: raise RuntimeError("POI download failed; previous snapshot untouched")
    features = []
    for item in result.get("elements", []):
        tags = item.get("tags", {}); kind = facilities.classify(tags); center = item.get("center", item)
        if not kind or "lon" not in center: continue
        lon, lat = center["lon"], center["lat"]; r, c = m.rc(lon, lat)
        if not (0 <= r < m.height and 0 <= c < m.width): continue
        features.append({"type": "Feature", "properties": {
            "osm_id": f'{item["type"]}/{item["id"]}', "category": kind, "category_label": facilities.CATEGORIES[kind],
            "name": tags.get("name:ko", tags.get("name", tags.get("brand", "이름 미등록 시설"))),
            "opening_hours": tags.get("opening_hours", ""), "access": tags.get("access", "unknown"),
            "coordinate_kind": "node" if item["type"] == "node" else "geometry_center",
        }, "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    if not features: raise RuntimeError("No POIs returned; not substituting invented facilities")
    features.sort(key=lambda f: f["properties"]["osm_id"])
    facilities.eligibility(features)
    terrain_sha = {name: core.data_file_hashes()[name] for name in ("dem.npz", "landcover.npz", "slope.npz", "road_distance.npz")}
    geo = json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(geo + json.dumps(terrain_sha, sort_keys=True).encode()).hexdigest()
    folder = facilities.DATA / digest; folder.mkdir(parents=True, exist_ok=True)
    manifest = {"available": True, "snapshot": digest, "count": len(features), "region": m.meta.get("region", "제주도"),
                "eligible_count": sum(f["properties"]["model_eligible"] for f in features),
                "categories": dict(Counter(f["properties"]["category_label"] for f in features)),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "osm_timestamp": result.get("osm3s", {}).get("timestamp_osm_base"),
                "source": endpoint, "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
                "source_url": "https://www.openstreetmap.org/copyright", "query": query,
                "potential_scale_seconds": facilities.SCALE_SECONDS, "potential_scope": "per analysis window, 64-cell margin",
                "category_weights": "equal, nearest facility only",
                "status": "UNCALIBRATED_ASSUMPTION", "opening_status": "UNKNOWN_NOT_LIVE",
                "terrain_sha256": terrain_sha}
    (folder / "facilities.geojson").write_bytes(geo)
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary = facilities.DATA / "latest.tmp"; temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary.replace(facilities.DATA / "latest.json")
    print(json.dumps({"count": manifest["count"], "eligible": manifest["eligible_count"], "categories": manifest["categories"], "snapshot": digest}, ensure_ascii=False), flush=True)


if __name__ == "__main__": main()
