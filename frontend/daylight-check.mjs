// Full form + forecast flow, with a disposable database and a non-Korean browser.
import {chromium,request} from '@playwright/test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
const root=path.resolve('..');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'searchproof-daylight-test-'));
const socket=net.createServer();
await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
const port=socket.address().port;
await new Promise(resolve=>socket.close(resolve));
const base='http://127.0.0.1:'+port;
const server=spawn(path.join(root,'.venv/bin/python'),['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,SEARCHPROOF_DB:path.join(temp,'test.sqlite3')},stdio:'ignore'});
let browser,api;
try {
  api=await request.newContext({baseURL:base});
  for(let i=0;i<30;i++) {
    try{if((await api.get('/api/health')).ok())break;}catch{}
    await new Promise(resolve=>setTimeout(resolve,300));
  }
  const legacyId=(await (await api.get('/api/missions')).json())[0].id;
  const legacy=await (await api.get('/api/missions/'+legacyId)).json();
  browser=await chromium.launch();
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2,timezoneId:'America/New_York'});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  // Ephemeral port is outside the fixed browser Origin allowlist. Relay the
  // form's exact POST body through the API test client only for this test DB.
  // The application's CSRF policy remains unchanged and has separate tests.
  await page.route(base+'/api/**',async route=>{
    if(route.request().method()==='POST') {
      const result=await api.post(route.request().url(),{data:route.request().postData(),headers:{'content-type':'application/json'}});
      await route.fulfill({response:result});
    } else await route.continue();
  });
  await page.goto(base);
  await page.getByRole('button',{name:'새 모의 수색',exact:true}).click();
  await page.getByLabel('실종 시각과 낮밤 반영',{exact:true}).check();
  await page.getByLabel('임무 이름',{exact:true}).fill('밤에서 아침으로 · 시각 검사');
  await page.getByLabel('마지막 확인 시각 (한국시간)',{exact:true}).fill('2026-09-19T05:00');
  await page.getByLabel('기준 지도 시각 (한국시간)',{exact:true}).fill('2026-09-19T06:00');
  await page.getByLabel('주변 시설 영향 가정',{exact:true}).uncheck();
  assert.equal(await page.getByLabel('시각 입력으로 경과시간 계산',{exact:true}).isDisabled(),true);
  assert.equal(await page.getByLabel('시각 입력으로 경과시간 계산',{exact:true}).inputValue(),'1');
  await page.screenshot({path:'../artifacts/daylight-form.png'});
  const created=page.waitForResponse(r=>r.url()===base+'/api/missions'&&r.request().method()==='POST');
  await page.getByRole('button',{name:'시나리오 계산 및 시작',exact:true}).click();
  const response=await created;assert.equal(response.status(),200,await response.text());
  const id=(await response.json()).id;
  await page.getByTestId('daylight-summary').waitFor();
  const initial=await (await api.get('/api/missions/'+id)).json();
  assert.equal(initial.params.hours,1);
  assert.equal(new Date(initial.params.missing_at).toISOString(),'2026-09-18T20:00:00.000Z');
  assert.equal(initial.daylight.start_phase,'NIGHT');
  const future=page.waitForResponse(r=>r.url().includes('/forecast?')&&r.url().includes('seconds=1800'));
  await page.getByRole('button',{name:'시간 미리보기',exact:true}).click();
  const f=await (await future).json();
  assert.equal(f.speed.daylight.end_phase,'DAY');
  assert.equal(new Date(f.estimated_at).toISOString(),'2026-09-18T21:30:00.000Z');
  await page.getByTestId('live-speed').waitFor();
  assert.match(await page.locator('.live-time').innerText(),/06:30:00 KST/);
  await page.screenshot({path:'../artifacts/daylight-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'../artifacts/daylight-mobile.png',fullPage:true});
  await page.getByRole('button',{name:'새 모의 수색',exact:true}).click();
  await page.screenshot({path:'../artifacts/daylight-form-mobile.png'});
  assert.equal(await page.locator('dialog').evaluate(el=>el.scrollWidth>el.clientWidth),false);
  await page.getByLabel('기준 지도 시각 (한국시간)',{exact:true}).fill('2026-09-19T04:00');
  await page.getByText('기준 지도는 마지막 확인 시각보다 15분–8시간 뒤여야 합니다.',{exact:true}).waitFor();
  await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  assert.equal((await (await api.get('/api/missions/'+id)).json()).receipt.hash,initial.receipt.hash);
  assert.equal((await (await api.get('/api/missions/'+legacyId)).json()).receipt.hash,legacy.receipt.hash);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,kstDespiteForeignBrowser:true,nightToDay:true,readOnlyForecast:true,mobile:true,legacyUnchanged:true,temporaryDatabase:temp,errors}));
} finally {
  await api?.dispose();await browser?.close();server.kill('SIGTERM');
}
