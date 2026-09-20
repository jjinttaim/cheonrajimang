// Dedicated disposable database; never adds test missions to the user's workspace.
import { chromium, request } from '@playwright/test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
const root=path.resolve('..');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'searchproof-facility-test-'));
const socket=net.createServer();
await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
const port=socket.address().port;
await new Promise(resolve=>socket.close(resolve));
const base='http://127.0.0.1:'+port;
const server=spawn(path.join(root,'.venv/bin/python'),['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',String(port)],{cwd:root,env:{...process.env,SEARCHPROOF_DB:path.join(temp,'test.sqlite3')},stdio:'ignore'});
let browser;
try {
  const api=await request.newContext({baseURL:base});
  for(let i=0;i<30;i++) {
    try {if((await api.get('/api/health')).ok())break;}catch{}
    await new Promise(resolve=>setTimeout(resolve,300));
  }
  const facilities=await (await api.get('/api/facilities')).json();
  const poi=facilities.features.find(f=>f.properties.category==='supplies'&&f.properties.model_eligible);
  const [lon,lat]=poi.geometry.coordinates;
  const created=await api.post('/api/missions',{data:{name:'시설 가정 화면 검사',lon,lat,hours:.5,facility_influence:true,facility_strength:.6}});
  assert.equal(created.status(),200);
  const id=(await created.json()).id;
  const initial=await (await api.get('/api/missions/'+id)).json();
  assert.ok(initial.params.facility_snapshot);
  browser=await chromium.launch();
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  await page.getByRole('button',{name:'우선 구역',exact:true}).click();
  await page.getByText('B′ 가정 반영',{exact:true}).waitFor();
  await page.getByTestId('basemap-status').filter({hasText:'고해상도 벡터'}).waitFor({timeout:45000});
  await page.getByText(/시설 종류 \d+개/).click();
  await page.locator('.facility-counts').waitFor();
  assert.ok((await page.locator('.facility-counts').innerText()).includes('편의점, 마트'));
  const windowed=await (await api.get('/api/facilities?mission='+id)).json();
  assert.equal(await page.locator('.facility-counts b').allTextContents().then(a=>a.reduce((n,v)=>n+Number(v),0)),windowed.metadata.window_count);
  await page.getByRole('button',{name:'시간 미리보기',exact:true}).click();
  await page.getByTestId('live-speed').waitFor();
  await page.screenshot({path:'../artifacts/facilities-desktop.png'});
  await page.getByRole('button',{name:'새 모의 수색',exact:true}).click();
  assert.equal(await page.getByLabel('주변 시설 영향 가정',{exact:true}).isChecked(),true);
  await page.getByLabel('시설 영향 강도',{exact:true}).fill('0.3');
  await page.getByLabel('주변 시설 영향 가정',{exact:true}).uncheck();
  assert.equal(await page.getByLabel('시설 영향 강도',{exact:true}).count(),0);
  await page.getByLabel('주변 시설 영향 가정',{exact:true}).check();
  await page.screenshot({path:'../artifacts/facilities-form.png'});
  await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'../artifacts/facilities-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  const after=await (await api.get('/api/missions/'+id)).json();
  assert.equal(after.receipt.hash,initial.receipt.hash);
  console.log(JSON.stringify({facilities:facilities.metadata.count,mission_snapshot:initial.params.facility_snapshot,unchanged_receipt:true,errors,temporary_database:temp}));
  await api.dispose();
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
