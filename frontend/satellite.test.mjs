import test from 'node:test';
import assert from 'node:assert/strict';
import {SATELLITE,satelliteSource,basemapVisibility,displaySatellite} from './src/satellite.js';

test('imagery uses HTTPS XYZ row/column order and native resolution ceiling',()=>{
  const s=satelliteSource();
  assert.equal(s.type,'raster');
  assert.equal(s.maxzoom,14);
  assert.equal(s.tileSize,256);
  assert.match(s.tiles[0],/^https:.*\/g\/\{z\}\/\{y\}\/\{x\}\.jpg$/);
  assert.match(s.attribution,/Copernicus Sentinel data 2025/);
  assert.match(s.attribution,/EOX IT Services GmbH/);
});

test('satellite hides opaque fills and can keep roads/labels, normal restores visibility',()=>{
  for(const layer of [{id:'background',type:'background'},{id:'water',type:'fill'},
                     {id:'basemap',type:'raster'},{id:'building',type:'fill','source-layer':'building'}]) {
    assert.equal(basemapVisibility(layer,true),'none');
    assert.equal(basemapVisibility(layer,false),'visible');
  }
  const road={id:'road',type:'line','source-layer':'transportation'};
  assert.equal(basemapVisibility(road,true),'visible');
  assert.equal(basemapVisibility(road,true,false),'none');
  const hidden={id:'hidden',type:'symbol',layout:{visibility:'none'}};
  assert.equal(basemapVisibility(hidden,true),'none');
  assert.equal(basemapVisibility(hidden,false),'none');
});

test('basemap switching does not touch analysis/track/selection/facility layers',()=>{
  const changes=[];
  const map={getLayer:()=>true,setLayoutProperty:(...args)=>changes.push(args)};
  displaySatellite(map,[{id:'background',type:'background'},{id:'road',type:'line','source-layer':'transportation'}],true);
  assert.deepEqual(changes.map(c=>c[0]),['background','road',SATELLITE.id]);
});
