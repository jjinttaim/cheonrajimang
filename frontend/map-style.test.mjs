import test from 'node:test';
import assert from 'node:assert/strict';
import {readableStyle,localAnalysisStyle} from './src/map-style.js';

test('retain attribution, road shields, and sharp buildings at high zoom',()=>{
  const source={version:8,sources:{openmaptiles:{type:'vector',url:'https://tiles.openfreemap.org/planet',attribution:'OpenStreetMap'}},layers:[
    {id:'building',type:'fill',maxzoom:14},
    {id:'building-3d',type:'fill-extrusion'},
    {id:'highway-name-minor',type:'symbol','source-layer':'transportation_name',layout:{'text-field':['get','name_en']}},
    {id:'highway-shield-non-us',type:'symbol',layout:{'text-field':['get','ref']}},
  ]};
  const s=readableStyle(source);
  assert.equal(source.layers[0].maxzoom,14);
  assert.equal(s.layers.find(l=>l.id==='building').maxzoom,undefined);
  assert.ok(s.layers.every(l=>l.type!=='fill-extrusion'));
  assert.equal(s.sources.openmaptiles.attribution,'OpenStreetMap');
  assert.deepEqual(s.layers.find(l=>l.id.includes('shield')).layout['text-field'],['get','ref']);
  assert.deepEqual(s.layers.find(l=>l.id==='highway-name-minor').layout['text-field'][1],['get','name:ko']);
});
test('local fallback uses only the existing image, without offline tile prefetch',()=>{
  const s=localAnalysisStyle({corners:[[0,1],[1,1],[1,0],[0,0]]});
  assert.equal(s.sources.basemap.url,'/api/base/basemap.png');
  assert.equal(s.sources.openmaptiles,undefined);
});

