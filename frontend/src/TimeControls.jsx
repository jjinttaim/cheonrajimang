import React from 'react';
import { koreanLocal, timeDefaults } from './time-input.js';
import InfoTip from './InfoTip.jsx';
import './time.css';

export const LIGHT_LABEL={DAY:'낮',TWILIGHT:'해 뜰녘과 해 질녘',NIGHT:'밤'};

export default function TimeControls({form,setForm}) {
  const hours=(new Date((form.analysis_local||'')+':00+09:00')-new Date((form.missing_local||'')+':00+09:00'))/3600000;
  return <section className="time-controls tip-host" aria-label="실종 시각과 낮밤 가정">
    <label className="time-enable"><input type="checkbox" aria-label="실종 시각과 낮밤 반영" checked={!!form.daylight_enabled} onChange={e=>setForm(f=>({...timeDefaults(),...f,daylight_enabled:e.target.checked}))}/><strong>실종 시각과 낮밤 반영</strong><span>검증 전 가정</span></label>
    <InfoTip corner label="낮밤 가정 설명"><p>켜면 날짜와 위치의 태양고도, 야간 이동 가정을 사용합니다. 끄면 기존 경과시간 계산을 유지합니다.</p><p>마지막 확인 시각부터 기준 지도 시각까지 계산하며 입력 시각은 모두 한국시간(KST)입니다. 지원 구간은 15분에서 8시간입니다.</p><p>야간 이동계수는 지형과 행동(B, B′)의 이동속도에 적용되고 일출과 일몰을 지나는 동안에도 변합니다. 거리(A)는 비교 기준으로 유지합니다. 야간 계수는 학습값이 아니며 수면, 조명, 탐지확률은 반영하지 않습니다.</p></InfoTip>
    {form.daylight_enabled?<>
      <div className="form-grid">
        <label>마지막 확인 시각 (한국시간)<input aria-label="마지막 확인 시각 (한국시간)" type="datetime-local" min="1900-01-01T00:00" max="2100-12-31T23:59" required value={form.missing_local||''} onChange={e=>setForm({...form,missing_local:e.target.value})}/></label>
        <label>기준 지도 시각 (한국시간)<input aria-label="기준 지도 시각 (한국시간)" type="datetime-local" min="1900-01-01T00:00" max="2100-12-31T23:59" required value={form.analysis_local||''} onChange={e=>setForm({...form,analysis_local:e.target.value})}/></label>
      </div>
      <div className="time-duration"><span>{Number.isFinite(hours)?'입력 구간 '+hours.toFixed(2)+'시간':'날짜와 시각을 입력하세요'}</span><button type="button" onClick={()=>setForm({...form,analysis_local:koreanLocal()})}>기준을 현재 시각으로</button></div>
      {Number.isFinite(hours)&&(hours<.25||hours>8)&&<p role="alert" className="time-invalid">기준 지도는 마지막 확인 시각보다 15분–8시간 뒤여야 합니다.</p>}
      <label className="night-factor">야간 이동계수 <strong>낮 대비 {Math.round((form.night_factor??.6)*100)}%</strong><input aria-label="야간 이동계수" type="range" min=".3" max="1" step=".1" value={form.night_factor??.6} onChange={e=>setForm({...form,night_factor:Number(e.target.value)})}/></label>
    </>:null}
  </section>;
}

export function DaylightSummary({initial,forecast}) {
  const light=forecast?.speed.daylight||initial;
  if(!light?.enabled)return <p className="daylight-summary">낮밤 가정 꺼짐</p>;
  const period=forecast?'시간 추정 구간':'마지막 확인 → 최초 기준 지도';
  const time=at=>new Date(at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  return <div className="daylight-summary" data-testid="daylight-summary">
    <span>{period}: {LIGHT_LABEL[light.start_phase]} → {LIGHT_LABEL[light.end_phase]}</span>
    <span>{time(light.start_at)} → {time(light.end_at)} KST</span>
    <span>낮 {Math.round(light.day_minutes)}분, 해 뜰녘과 질녘 {Math.round(light.twilight_minutes)}분, 밤 {Math.round(light.night_minutes)}분</span>
    <span className="daylight-assumption">야간 이동 {Math.round(light.night_factor*100)}% 가정 (학습 전)</span>
  </div>;
}
