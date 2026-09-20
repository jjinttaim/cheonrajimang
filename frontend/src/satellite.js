// Display-only imagery. It never enters the probability engine or training data.
// Public noncommercial service and attribution: https://maps.eox.at/
// https://cloudless.eox.at/documentation/license
export const SATELLITE = {
  id: 'satellite-imagery',
  year: 2025,
  resolutionM: 10,
  maxzoom: 14,
  tiles: ['https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2025_3857/default/g/{z}/{y}/{x}.jpg'],
  attribution: 'EOxCloudless <a href="https://cloudless.eox.at/">https://cloudless.eox.at</a> by <a href="https://eox.at/">EOX IT Services GmbH</a> (Contains modified Copernicus Sentinel data 2025)',
};

export function satelliteSource() {
  return {type:'raster',tiles:[...SATELLITE.tiles],tileSize:256,maxzoom:SATELLITE.maxzoom,attribution:SATELLITE.attribution};
}

export function isSatelliteReference(layer) {
  // Preserve road lines/names, borders, and POI labels, not opaque ground fills.
  return layer.type==='symbol' || (layer.type==='line'&&['transportation','boundary','building'].includes(layer['source-layer']));
}

export function basemapVisibility(layer, satellite, labels=true) {
  const original=layer.layout?.visibility||'visible';
  return satellite ? (labels&&isSatelliteReference(layer)?original:'none') : original;
}

export function displaySatellite(map, baseLayers, satellite, labels=true) {
  for(const layer of baseLayers) {
    if(map.getLayer(layer.id))map.setLayoutProperty(layer.id,'visibility',basemapVisibility(layer,satellite,labels));
  }
  if(map.getLayer(SATELLITE.id))map.setLayoutProperty(SATELLITE.id,'visibility',satellite?'visible':'none');
}
