import test from 'node:test';
import assert from 'node:assert/strict';
import {koreanLocal,missionPayload,copyTime,elapsedHours} from './src/time-input.js';

test('explicit Korean timestamps do not depend on browser or machine timezone',()=>{
  assert.equal(koreanLocal('2026-09-19T18:00:00Z'),'2026-09-20T03:00');
  const p=missionPayload({missing_local:'2026-09-19T23:30',analysis_local:'2026-09-20T02:30',daylight_enabled:true,night_factor:.6,hours:3});
  assert.equal(p.missing_at,'2026-09-19T23:30:00+09:00');
  assert.equal(new Date(p.analysis_at)-new Date(p.missing_at),3*3600000);
  assert.equal('missing_local' in p,false);
  assert.equal(elapsedHours({missing_local:'2026-09-19T23:30',analysis_local:'2026-09-20T00:30'}),1);
});
test('legacy mode does not infer dates; facility comparisons preserve time settings',()=>{
  assert.equal(missionPayload({daylight_enabled:false,missing_local:'2026-09-19T12:00'}).missing_at,null);
  const settings=copyTime({daylight_enabled:true,night_factor:.5,missing_at:'2026-09-19T20:00:00+09:00',analysis_at:'2026-09-19T21:00:00+09:00'});
  assert.equal(settings.missing_local,'2026-09-19T20:00');
  assert.equal(settings.night_factor,.5);
  assert.equal(settings.daylight_enabled,true);
});
