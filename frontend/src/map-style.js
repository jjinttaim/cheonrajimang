// The basemap is visual context; it never changes the 30 m analysis grid.
// Official public style: https://openfreemap.org/quick_start/
export const VECTOR_STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

// `window` is one mission's 512×512 analysis window ({corners, basemap_url}); the
// default image is the original 한림읍 window.
export function localAnalysisStyle(window) {
  return {
    version: 8,
    sources: { basemap: { type: 'image', url: window.basemap_url || '/api/base/basemap.png', coordinates: window.corners } },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#b3d5df' } },
      { id: 'basemap', type: 'raster', source: 'basemap', paint: { 'raster-fade-duration': 0 } },
    ],
  };
}

// Offline fallback without an analysis raster (volunteer phone view).
export function plainStyle() {
  return { version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#dfe9e4' } }] };
}

export function readableStyle(input) {
  const style = structuredClone(input);
  if (style.version !== 8 || !style.sources?.openmaptiles || !Array.isArray(style.layers)) {
    throw new Error('Unsupported basemap style');
  }
  // Flat building outlines are clearer for the search coordinator than 3D volumes.
  style.layers = style.layers.filter(layer => layer.type !== 'fill-extrusion');
  for (const layer of style.layers) {
    if (layer.id === 'building') {
      delete layer.maxzoom;
      layer.minzoom = 13;
      layer.paint = { 'fill-color': '#d6d3cd', 'fill-outline-color': '#a8aaa3', 'fill-opacity': .9 };
    }
    if (layer.type !== 'symbol' || !layer.layout?.['text-field']) continue;
    // Keep numeric route shields; prefer Korean/local names over transliteration.
    if (layer.id.includes('shield')) continue;
    layer.layout['text-field'] = ['coalesce', ['get', 'name:ko'], ['get', 'name:nonlatin'], ['get', 'name'], ['get', 'name:latin']];
    layer.layout['text-font'] = [layer.id.includes('country') ? 'Noto Sans Bold' : 'Noto Sans Regular'];
    layer.paint = { ...layer.paint, 'text-halo-color': '#ffffff', 'text-halo-width': 1.5, 'text-halo-blur': .25 };
    if (layer['source-layer'] === 'transportation_name') {
      layer.layout['text-size'] = ['interpolate', ['linear'], ['zoom'], 12, 12, 16, 14, 20, 16];
      layer.paint['text-color'] = '#47544b';
    }
    if (layer['source-layer'] === 'poi') {
      layer.layout['text-size'] = 13;
      layer.paint['text-color'] = '#455649';
    }
    if (layer.id === 'label_other') layer.layout['text-size'] = 13;
  }
  return style;
}

export async function loadDetailedStyle(signal) {
  const response = await fetch(VECTOR_STYLE_URL, { signal, referrerPolicy: 'no-referrer' });
  if (!response.ok) throw new Error('Basemap style unavailable');
  return readableStyle(await response.json());
}

