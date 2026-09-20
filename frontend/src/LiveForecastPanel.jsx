import React, { useEffect, useState } from 'react';
import { Clock3, Radio, Pause, LoaderCircle } from 'lucide-react';
import InfoTip from './InfoTip.jsx';
import {plain} from './text.js';
import './live.css';
import { DaylightSummary } from './TimeControls.jsx';

export default function LiveForecastPanel({mission,version,mode,onModeChange,forecast,onForecastChange,daylight}) {
  const [minutes,setMinutes]=useState(30),[pending,setPending]=useState(false),[error,setError]=useState('');
  const [clock,setClock]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(timer);},[]);
  useEffect(()=>{
    let cancelled=false,timer,controller;
    if(mode==='saved'){setError('');setPending(false);onForecastChange(null);return;}
    async function update() {
      if(cancelled)return;
      if(document.hidden&&mode==='live'){timer=setTimeout(update,15000);return;}
      controller=new AbortController();
      setPending(true);
      try {
        const query=new URLSearchParams({version:String(version)});
        if(mode==='preview')query.set('seconds',String(minutes*60));
        const response=await fetch('/api/missions/'+mission+'/forecast?'+query,{signal:controller.signal});
        if(!response.ok){
          const failure=await response.json().catch(()=>({}));
          throw new Error((typeof failure.detail==='string'?failure.detail:'시간 추정을 불러오지 못했습니다.')+' 저장된 기준 지도를 표시합니다.');
        }
        const value=await response.json();
        if(cancelled)return;
        onForecastChange(value);setError('');
      } catch(e) {
        if(cancelled||e.name==='AbortError')return;
        onForecastChange(null);setError(e.message);
      } finally {
        if(!cancelled){setPending(false);if(mode==='live')timer=setTimeout(update,15000);}
      }
    }
    timer=setTimeout(update,mode==='preview'?180:0);
    return()=>{cancelled=true;clearTimeout(timer);controller?.abort();};
  },[mission,version,mode,minutes,onForecastChange]);
  const age=forecast?Math.max(0,Math.floor((clock-new Date(forecast.computed_at).getTime())/1000)):0;
  const at=forecast?new Date(forecast.estimated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'';
  return <section className={'live-panel '+(mode==='live'?'live-active':'')} aria-label="시간에 따른 가능성 추정">
    <div className="live-main">
      <div className="live-title"><Clock3 size={16}/><strong>천라지망 AI</strong><span>실종자 이동, 학습 전</span><InfoTip label="시간 추정 설명"><p>기준 지도는 동일 시점으로 간주한 정적 수색 요약입니다. 시각을 지정한 임무의 시간 미리보기와 실시간 추정은 GPX 관찰 시각을 재생합니다.</p><p>시간 미리보기와 실시간 추정은 실제 위치 추적이 아닌 미검증 이동 추정입니다. 승인과 인계는 저장된 기준 버전에서 진행하세요.</p><p>이동은 위치, 경사, 토지피복, 도로에 따라 계산하며 교통정보는 연동되지 않습니다.</p>{forecast?.speed.observation_timing&&<p>{plain(forecast.speed.observation_timing.note)}</p>}</InfoTip></div>
      <div className="timeline-modes">
        <button aria-pressed={mode==='saved'} className={mode==='saved'?'active':''} onClick={()=>onModeChange('saved')}><Pause size={12}/> 기준 지도</button>
        <button aria-pressed={mode==='preview'} className={mode==='preview'?'active':''} onClick={()=>onModeChange('preview')}>시간 미리보기</button>
        <button aria-pressed={mode==='live'} className={mode==='live'?'active live-button':'live-button'} onClick={()=>onModeChange('live')}><Radio size={13}/> 실시간 추정</button>
      </div>
    </div>
    {mode==='preview'&&<label className="timeline-range">기준 버전 이후 <strong>+{minutes}분</strong><input aria-label="기준 버전 이후 시간" type="range" min="0" max="120" step="5" value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/><span>0분</span><span>2시간</span></label>}
    <div className="live-description" aria-live="polite">
      {pending?<><LoaderCircle size={13} className="spin"/> 지형 이동 계산 중…</>:
      forecast?<><span className="live-time">{at} KST 시점 추정 +{Math.floor(forecast.projection_seconds/60)}분</span><span>모형 평균속도 <b data-testid="live-speed">{forecast.speed.mean_kmh.toFixed(2)} km/h</b></span><span>{mode==='live'?age+'초 전 갱신 (15초 간격)':'가상 시간 미리보기'}</span></>:
      <span>위치, 경사, 토지피복, 도로에 따라 이동을 계산합니다.</span>}
    </div>
    <DaylightSummary initial={daylight} forecast={forecast}/>
    {forecast?.expired&&<p role="alert" className="live-error">기준 지도가 6시간보다 오래되었습니다. 6시간 예측까지만 표시하며 최신 상태가 아닙니다. 새 모의 수색으로 기준을 갱신하세요.</p>}
    {error&&<p role="alert" className="live-error">{error}</p>}
  </section>;
}
