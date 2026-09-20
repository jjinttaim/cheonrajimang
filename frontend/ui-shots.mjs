// Screenshot walkthrough of the new features against a running 8000 server (isolated DB).
// 서버의 local_boundary(backend/main.py)가 브라우저 Origin을 8000·5173 포트로 제한하므로 SEARCHPROOF_TEST_URL은 그 두 포트만 쓴다.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const base = process.env.SEARCHPROOF_TEST_URL || 'http://127.0.0.1:8000';
// 기본 저장 위치는 저장소 밖으로 나가지 않는 artifacts/shots (gitignore 대상). SHOT_DIR로 바꿀 수 있다.
const out = process.env.SHOT_DIR || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../artifacts/shots');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const errors = [];
async function page(width, height) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, locale: 'ko-KR' });
  const p = await ctx.newPage();
  p.on('pageerror', e => errors.push('pageerror: ' + e.message));
  p.on('console', m => { if (m.type() === 'error' && !/tiles\.openfreemap|maps\.eox|net::ERR|Failed to load resource|WebGL/.test(m.text())) errors.push('console: ' + m.text()); });
  return p;
}
// --- desktop coordinator
const d = await page(1440, 1000);
await d.goto(base + '/', { waitUntil: 'networkidle' });
await d.waitForSelector('.summary-strip', { timeout: 30000 });
await d.waitForTimeout(2500);
await d.screenshot({ path: out + '/new-desktop-ai.png' });
// components table
await d.getByRole('button', { name: /학습과 가정 구분/ }).click();
await d.waitForSelector('[data-testid=ai-components]');
await d.screenshot({ path: out + '/new-desktop-components.png' });
// new mission modal
await d.getByRole('button', { name: /새 모의 수색/ }).first().click();
await d.waitForSelector('dialog[open]');
await d.screenshot({ path: out + '/new-mission-form.png' });
await d.getByLabel('거리 시나리오 모델').selectOption('learned_lognormal');
await d.getByRole('button', { name: /시나리오 계산 및 시작/ }).click();
await d.waitForSelector('dialog[open]', { state: 'detached', timeout: 60000 });
await d.waitForTimeout(1500);
// tracks tab with team inputs, demo track with 6 people
await d.locator('.side-tabs button', { hasText: '수색 기록' }).click();
await d.getByLabel('팀 인원').fill('6');
await d.getByRole('button', { name: /합성 훈련 트랙으로 체험/ }).click();
await d.waitForSelector('.track-card', { timeout: 30000 });
await d.screenshot({ path: out + '/new-tracks-team.png' });
await d.getByRole('button', { name: /미발견 확정/ }).first().click();
await d.getByRole('dialog').getByRole('button', { name: /미발견 확정/ }).click();
await d.waitForSelector('.toast', { timeout: 30000 });
await d.waitForTimeout(1500);
// AI plan with team 6
await d.locator('.side-tabs button', { hasText: 'AI 계획' }).click();
await d.getByLabel('AI 계획 시간').fill('60');
await d.getByRole('button', { name: /AI 수색 계획 계산/ }).click();
await d.waitForSelector('[data-testid=ai-results]', { timeout: 120000 });
await d.waitForTimeout(1500);
await d.screenshot({ path: out + '/new-ai-plan-team.png' });
// members tab
await d.locator('.side-tabs button', { hasText: '참여자' }).click();
await d.getByLabel('참여자 이름').fill('2조 김OO');
await d.getByRole('button', { name: /코드 만들기/ }).click();
await d.waitForSelector('.issued-code', { timeout: 30000 });
const code = (await d.locator('.issued-code code').textContent()).replace('-', '');
await d.getByLabel('참여자 역할').selectOption('family');
await d.getByLabel('참여자 이름').fill('가족 A');
await d.getByRole('button', { name: /코드 만들기/ }).click();
await d.waitForTimeout(1000);
const famCode = (await d.locator('.issued-code code').textContent()).replace('-', '');
await d.locator('select[aria-label="2조 김OO 배정 구역"]').selectOption({ index: 1 });
await d.waitForTimeout(1200);
await d.screenshot({ path: out + '/new-members.png' });
// report
const rep = await page(1000, 1400);
const mid = await d.evaluate(async () => (await (await fetch('/api/missions')).json())[0].id);
await rep.goto(base + '/api/missions/' + mid + '/report', { waitUntil: 'networkidle' });
await rep.screenshot({ path: out + '/new-report.png', fullPage: true });
// --- mobile volunteer join
const m = await page(390, 844);
await m.goto(base + '/?view=join#code=' + code, { waitUntil: 'networkidle' });
await m.waitForSelector('.consent-check', { timeout: 30000 });
await m.screenshot({ path: out + '/join-consent-mobile.png', fullPage: true });
await m.locator('.consent-check input').check();
await m.getByRole('button', { name: /동의하고 시작/ }).click();
await m.waitForSelector('.checkin', { timeout: 30000 });
await m.waitForTimeout(3000);
await m.screenshot({ path: out + '/join-volunteer-mobile.png', fullPage: true });
await m.getByRole('button', { name: /지금 체크인/ }).click();
await m.waitForSelector('.toast', { timeout: 20000 });
// pause from coordinator, volunteer sees banner
await d.getByRole('button', { name: /일괄 중지/ }).click();
await d.waitForTimeout(1200);
await m.reload({ waitUntil: 'networkidle' });
await m.waitForSelector('.join-banner.paused', { timeout: 30000 });
await m.screenshot({ path: out + '/join-paused-mobile.png' });
// family
const f = await page(390, 844);
await f.goto(base + '/?view=join#code=' + famCode, { waitUntil: 'networkidle' });
await f.waitForSelector('.progress-grid', { timeout: 30000 });
await f.screenshot({ path: out + '/join-family-mobile.png', fullPage: true });
// mobile coordinator
const mc = await page(390, 844);
await mc.goto(base + '/', { waitUntil: 'networkidle' });
await mc.waitForSelector('.summary-strip', { timeout: 30000 });
await mc.waitForTimeout(2000);
await mc.screenshot({ path: out + '/new-mobile-coordinator.png', fullPage: true });
const overflow = await mc.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
console.log(JSON.stringify({ code, famCode, overflow, errors }, null, 1));
await browser.close();
