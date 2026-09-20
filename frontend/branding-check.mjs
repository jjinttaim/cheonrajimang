// Read-only smoke check against the local app; never create or alter missions.
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const browser=await chromium.launch();
try {
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:2});
  const errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{
    if(r.url().includes('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method())) mutations.push(r.url());
  });
  await page.goto('http://127.0.0.1:8000/');
  await page.locator('.wordmark').waitFor();
  assert.match(await page.title(),/천라지망/);
  assert.match(await page.locator('.wordmark').innerText(),/천라지망/);
  assert.match(await page.locator('.live-title').innerText(),/천라지망 AI/);
  assert.match(await page.locator('.live-title').innerText(),/학습 전/);
  await page.waitForTimeout(1200);
  await page.screenshot({path:'../artifacts/cheonrajimang-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'../artifacts/cheonrajimang-mobile.png',fullPage:true});
  const missions=await (await page.request.get('http://127.0.0.1:8000/api/missions')).json();
  assert.ok(missions.length>0);
  const report=await page.request.get('http://127.0.0.1:8000/api/missions/'+missions[0].id+'/report');
  assert.equal(report.status(),200);
  assert.match(await report.text(),/천라지망 수색 상황보고서/);
  assert.deepEqual(errors,[]);
  assert.deepEqual(mutations,[]);
  console.log(JSON.stringify({passed:true,desktop:true,mobile:true,report:true,readOnly:true,errors}));
} finally {
  await browser.close();
}
