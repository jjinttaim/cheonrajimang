// End-to-end AI flow uses a disposable DB, never the user's mission records.
import {chromium,request} from '@playwright/test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
const root=path.resolve('..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'cheonrajimang-ai-test-'));
const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
const port=socket.address().port;await new Promise(r=>socket.close(r));
const base='http://127.0.0.1:'+port;
const server=spawn(path.join(root,'.venv/bin/python'),['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,SEARCHPROOF_DB:path.join(temp,'test.sqlite3')},stdio:'ignore'});
let browser,api;
try{
  api=await request.newContext({baseURL:base});
  for(let i=0;i<40;i++){try{if((await api.get('/api/health')).ok())break;}catch{}await new Promise(r=>setTimeout(r,250));}
  const mid=(await (await api.get('/api/missions')).json())[0].id;
  const original=await (await api.get('/api/missions/'+mid)).json();
  browser=await chromium.launch();
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let holdPlanSeconds=null,releaseHeld,markHeld,delayStatus=0;
  let activePlans=0,maxActivePlans=0;
  const planRequests=[];
  const held=new Promise(resolve=>{markHeld=resolve;});
  const heldRelease=new Promise(resolve=>{releaseHeld=resolve;});
  await page.route(base+'/api/**',async route=>{
    if(route.request().method()==='POST'){
      const isPlan=route.request().url()===base+'/api/missions/'+mid+'/plans';
      const seconds=isPlan?JSON.parse(route.request().postData()).projection_seconds:null;
      if(isPlan){activePlans++;maxActivePlans=Math.max(maxActivePlans,activePlans);planRequests.push(seconds);}
      try{
        const r=await api.post(route.request().url(),{data:route.request().postData(),headers:{'content-type':'application/json'}});
        if(isPlan&&seconds===holdPlanSeconds){holdPlanSeconds=null;markHeld();await heldRelease;}
        if(isPlan)activePlans--;
        await route.fulfill({response:r});
      }catch(error){if(isPlan)activePlans=Math.max(0,activePlans-1);throw error;}
    }else{
      if(route.request().url()===base+'/api/ai/status'&&delayStatus){const delay=delayStatus;delayStatus=0;await new Promise(r=>setTimeout(r,delay));}
      await route.continue();
    }
  });
  await page.goto(base);
  await page.getByText('학습 모델 연결',{exact:true}).waitFor();
  await page.getByLabel('AI 수색팀 수').selectOption('2');
  await page.getByLabel('AI 계획 시간').fill('60');
  const first=page.waitForResponse(r=>r.url()===base+'/api/missions/'+mid+'/plans'&&r.request().method()==='POST');
  await page.getByRole('button',{name:'AI 수색 계획 계산',exact:true}).click();
  const response=await first;assert.equal(response.status(),200,await response.text());
  const proposal=await response.json();assert.equal(proposal.assignments.length,2);
  await page.getByTestId('ai-results').waitFor();
  await page.locator('.ai-route-card').first().click();
  await page.waitForTimeout(800);
  await page.screenshot({path:'../artifacts/ai-plan-desktop.png'});
  await page.getByRole('button',{name:'현장 검토 후 모의 계획 확정',exact:true}).click();
  await page.getByRole('button',{name:'모의 계획 확정됨',exact:true}).waitFor();
  const after=await (await api.get('/api/missions/'+mid)).json();
  assert.equal(after.receipt.hash,original.receipt.hash);
  assert.equal(after.coverage_km2,original.coverage_km2);
  assert.equal(after.ledger.filter(l=>l.kind==='AI_PLAN').length,1);
  delayStatus=2000;
  await page.reload();
  await page.locator('.ai-history-item').filter({hasText:'확정'}).first().click();
  await page.getByRole('button',{name:'모의 계획 확정됨',exact:true}).waitFor();
  await page.getByText('학습 모델 연결',{exact:true}).waitFor();
  await page.getByRole('button',{name:'모의 계획 확정됨',exact:true}).waitFor();
  await page.getByTestId('ai-map-caption').waitFor();
  assert.equal((await (await api.get('/api/missions/'+mid+'/plans/'+proposal.id)).json()).status,'APPROVED');
  await page.getByLabel('AI 계획 위치 분포').selectOption('ENDPOINT_REFERENCE');
  await page.getByRole('button',{name:'AI 수색 계획 계산',exact:true}).click();
  await page.getByTestId('ai-results').waitFor();
  assert.match(await page.getByTestId('ai-results').innerText(),/현재 시점 존재확률이 아닙니다/);
  assert.match(await page.getByTestId('ai-map-caption').innerText(),/학습 발견점 참고 분포/);
  await page.getByText('학습 발견점 분포 (시간 예측 아님)',{exact:true}).waitFor();
  await page.getByLabel('발견점 학습 자료').selectOption('yosar_interval_lognorm');
  const yosarResponse=page.waitForResponse(r=>r.url()===base+'/api/missions/'+mid+'/plans'&&r.request().method()==='POST');
  await page.getByRole('button',{name:'AI 수색 계획 계산',exact:true}).click();
  const yr=await yosarResponse;assert.equal(yr.status(),200,await yr.text());
  const yp=await yr.json();assert.equal(yp.endpoint_model,'yosar_interval_lognorm');
  await page.getByTestId('ai-endpoint-source').waitFor();
  assert.match(await page.getByTestId('ai-endpoint-source').innerText(),/YOSAR 132개 사건 키/);
  assert.match(await page.getByTestId('ai-endpoint-source').innerText(),/CC-BY-NC-SA-4.0/);
  await page.screenshot({path:'../artifacts/ai-yosar-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'../artifacts/ai-plan-mobile.png',fullPage:true});
  await page.getByLabel('AI 계획 위치 분포').selectOption('TIME_SCENARIOS');
  await page.getByLabel('새 지도와 시간으로 추천 자동 갱신').check();
  const future=page.waitForResponse(async r=>{
    if(r.url()!==base+'/api/missions/'+mid+'/plans'||r.request().method()!=='POST')return false;
    return JSON.parse(r.request().postData()).projection_seconds===1800;
  });
  await page.getByRole('button',{name:'시간 미리보기',exact:true}).click();
  const f=await future;assert.equal(f.status(),200,await f.text());
  const fp=await f.json();assert.equal(fp.projection_seconds,1800);
  assert.equal(fp.moving_target,true);
  assert.equal(fp.assumptions.within_plan_target,'COHERENT_TIMED_PATHS');
  assert.equal(fp.target_motion.frames.at(-1).relative_seconds,1800);
  assert.equal(fp.target_motion.time_step_seconds,15);
  assert.equal(fp.target_motion.max_visit_time_error_seconds,7.5);
  assert(fp.target_motion.path_array_bytes<=256*1024*1024);
  await page.getByTestId('ai-motion-caption').waitFor();
  assert.match(await page.getByTestId('ai-motion-caption').innerText(),/15초 간격/);
  assert.match(await page.getByTestId('ai-motion-caption').innerText(),fp.target_motion.any_sampled?/경로 표집 사용/:/원래 가중 경로 전체 사용/);
  await page.getByTestId('ai-motion-caption').scrollIntoViewIfNeeded();
  await page.screenshot({path:'../artifacts/ai-moving-target-numerics-mobile.png'});
  await page.screenshot({path:'../artifacts/ai-moving-target-mobile.png',fullPage:true});
  // Hold a real plan response while two newer forecasts arrive. The browser
  // must finish the first request and then submit only the newest forecast.
  holdPlanSeconds=2100;
  const beforeSlow=planRequests.length;
  const slider=page.getByLabel('기준 버전 이후 시간');
  await slider.fill('35');
  let heldTimeout;
  try{await Promise.race([held,new Promise((_,reject)=>{heldTimeout=setTimeout(()=>reject(new Error('Held plan request did not arrive')),30000);})]);}
  finally{clearTimeout(heldTimeout);}
  for(const minute of [40,45]){
    await slider.fill(String(minute));
    await page.locator('.live-time').filter({hasText:'+'+minute+'분'}).waitFor();
    await page.waitForTimeout(600);
  }
  assert.deepEqual(planRequests.slice(beforeSlow),[2100]);
  const newest=page.waitForResponse(r=>r.url()===base+'/api/missions/'+mid+'/plans'&&r.request().method()==='POST'&&JSON.parse(r.request().postData()).projection_seconds===2700);
  releaseHeld();
  const newestResponse=await newest;assert.equal(newestResponse.status(),200,await newestResponse.text());
  await page.getByTestId('ai-results').locator('.ai-time').first().filter({hasText:'+45분'}).waitFor();
  await page.getByLabel('새 지도와 시간으로 추천 자동 갱신').uncheck();
  assert.deepEqual(planRequests.slice(beforeSlow),[2100,2700]);
  assert.equal(maxActivePlans,1);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,actualModels:true,twoTeams:true,approvalNotObservation:true,endpointMode:true,yosarModel:true,timeReplan:true,movingTarget:true,dynamicComputeSeconds:fp.compute_seconds,slowResponseLatestOnly:true,maxActivePlans,mobile:true,errors,temporaryDatabase:temp}));
}finally{await api?.dispose();await browser?.close();server.kill('SIGTERM');}
