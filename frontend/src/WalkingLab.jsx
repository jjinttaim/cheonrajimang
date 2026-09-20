import React,{useEffect,useState} from 'react';
import './walking-lab.css';
import WalkingEvaluation from './WalkingEvaluation.jsx';

async function read(url,signal){
  const response=await fetch(url,{signal});const body=await response.json();
  if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'학습 모델을 불러오지 못했습니다.');
  return body;
}

function MotionPlot({data}){
  const paths=data?.paths||[];
  const extent=Math.max(200,Math.ceil(Math.max(...paths.flat().map(p=>Math.max(Math.abs(p[0]),Math.abs(p[1]))),...(data?.cells||[]).map(([x,y])=>Math.max(Math.abs(x),Math.abs(y))*40+40),0)/100)*100);
  const scale=250/extent,cx=x=>300+x*scale,cy=y=>300-y*scale;
  const max=Math.max(...(data?.cells||[]).map(c=>c[2]),.00001);
  return <svg viewBox="0 0 600 600" role="img" aria-label="학습 모델이 생성한 상대 이동 분포. 실제 실종자 위치가 아닙니다.">
    <rect width="600" height="600" fill="#f4f7f3"/>
    {[-1,-.5,0,.5,1].map(k=><g key={k}><line x1={cx(k*extent)} y1="50" x2={cx(k*extent)} y2="550" stroke="#dde5dd"/><line y1={cy(k*extent)} x1="50" y2={cy(k*extent)} x2="550" stroke="#dde5dd"/><text x={cx(k*extent)} y="577" textAnchor="middle">{k*extent}m</text></g>)}
    {data?.cells.map(([x,y,p])=><rect key={x+','+y} x={cx(x*40)} y={cy((y+1)*40)} width={40*scale+.2} height={40*scale+.2} fill="#19715c" opacity={.06+.8*Math.sqrt(p/max)}/>)}
    {paths.slice(0,12).map((points,i)=><polyline key={i} points={points.map(([x,y])=>`${cx(x)},${cy(y)}`).join(' ')} fill="none" stroke="#c0934d" strokeWidth="1.3" opacity=".65"/>)}
    <circle cx="300" cy="300" r="5" fill="#14382c" stroke="white" strokeWidth="2"/>
    <text x="312" y="292">가상 출발점</text><text x="300" y="27" textAnchor="middle">북쪽 ↑, 상대 좌표, 지형 반영 없음</text>
  </svg>;
}

export default function WalkingLab(){
  const [status,setStatus]=useState(null),[data,setData]=useState(null),[seconds,setSeconds]=useState(120),[seed,setSeed]=useState(42),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{const c=new AbortController();read('/api/ai/walking/status',c.signal).then(setStatus).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[]);
  useEffect(()=>{
    if(!status)return;const c=new AbortController();setBusy(true);setError('');setData(null);
    const timer=setTimeout(()=>read(`/api/ai/walking/simulate?seconds=${seconds}&seed=${seed}&model_id=${status.model_id}`,c.signal).then(v=>{if(!c.signal.aborted)setData(v);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!c.signal.aborted)setBusy(false);}),180);
    return()=>{clearTimeout(timer);c.abort();};
  },[status,seconds,seed]);
  return <main className="walking-lab">
    <header><a href="/">← 수색 워크스페이스</a><span>천라지망 학습 실험실</span><span className="walk-badge">실제 데이터 학습 완료</span></header>
    <div className="walk-intro"><div className="walk-eyebrow">GEOLIFE / ORDINARY WALKING</div><h1>데이터에서 배운<br/>사람의 다음 한 걸음.</h1><p>실제 보행 기록으로 다음 30초의 이동 속도와 방향 변화를 학습했습니다.<br/>아래는 그 규칙을 반복한 가상 시연이며, 실제 실종자 위치 예측이 아닙니다.</p></div>
    {error&&<p role="alert" className="walk-warning">{error}</p>}
    {!status&&!error&&<p role="status">학습 결과를 불러오는 중…</p>}
    {status&&<>
      <section className="walk-metrics" aria-label="학습 결과">
        <article><small>정제된 학습 및 평가 표본</small><strong>{status.rows.toLocaleString()}</strong><span>{status.people}명, 같은 사람은 집합 간 분리</span></article>
        <article><small>평가 예측 손실 개선</small><strong>{(status.nll_improvement*100).toFixed(1)}<em>%</em></strong><span>기준 모델 대비 (정확도 %가 아님)</span></article>
        <article><small>다음 속도 평균 오차</small><strong>{status.test.model_speed_mae_mps.toFixed(3)}<em>m/s</em></strong><span>기준 {status.test.baseline_speed_mae_mps.toFixed(3)} m/s</span></article>
      </section>
      <p className="walk-warning" data-testid="walking-status"><b>{status.status==='NOT_ADOPTED'?'실험용 (채택 기준 미충족)':'일반 보행 다음 30초에만 제한 채택'}</b> — 최종 평가 {status.test.people}명 / 최소 6명, 전체 {status.people}명 / 최소 30명. 수색 추천과 기존 지도는 변경하지 않습니다.</p>
      <div className="walk-workbench">
        <section className="walk-chart"><div className="walk-section-title"><h2>학습 규칙으로 만든 이동 분포</h2><span>{busy?'계산 중…':`${data?.particles.toLocaleString()||'—'}개 가상 경로`}</span></div><MotionPlot data={data}/><p>초록: 가상 도착점 분포, 황색: 경로 예시 12개<br/>축의 범위는 화면에 맞춰 변합니다. 실측 GPS나 실제 지도 위의 수색 범위가 아닙니다.</p></section>
        <section className="walk-controls"><div className="walk-eyebrow">RUN THE TRAINED MODEL</div><h2>직접 실행해 보기</h2>
          <label>가상 경과 시간 <b>{seconds}초</b><input aria-label="보행 실험 시간" type="range" min="0" max="300" step="30" value={seconds} onChange={e=>setSeconds(Number(e.target.value))}/></label>
          <div className="walk-presets">{[30,120,300].map(t=><button key={t} aria-pressed={seconds===t} onClick={()=>setSeconds(t)}>{t/60}분</button>)}</div>
          <button className="walk-run" onClick={()=>setSeed(v=>v+1)} disabled={busy}>다른 가상 경로 생성</button>
          {data&&<dl data-testid="walking-results"><dt>생성 시점</dt><dd>출발 후 {data.seconds}초</dd><dt>순이동 평균속도</dt><dd>{data.mean_net_speed_mps.toFixed(2)} m/s</dd><dt>출발점 거리 중앙값</dt><dd>{data.median_displacement_m.toFixed(0)} m</dd><dt>확률 총합</dt><dd>{data.mass.toFixed(6)}</dd><dt>재현 시드</dt><dd>{data.seed}</dd></dl>}
          <p className="walk-note">지형, 시설, 밤, 교통은 이 학습 모델의 입력이 아닙니다. 30초 성능 검사는 관측된 직전 속도를 사용합니다. 반복 생성에는 그 관측이 없어 장기 성능을 보장하지 않습니다.</p>
        </section>
      </div>
      <WalkingEvaluation modelId={status.model_id}/>
      <section className="walk-method"><h2>무엇을 실제로 학습했나요?</h2><p>{status.architecture}. 사람이 지정한 속도와 회전 확률을 그대로 쓰는 시연이 아니라, 학습 집합의 실제 기록으로 확률을 적합했습니다. 신경망은 아닙니다.</p>
        <div className="walk-splits">{[['train','학습'],['validation','검증'],['test','최종 평가']].map(([s,l])=><div key={s}><b>{l} {status.splits[s].people}명</b><span>{status.splits[s].rows.toLocaleString()}개 표본</span></div>)}</div>
        <p>NLL: {status.test.baseline_nll.toFixed(3)} → {status.test.model_nll.toFixed(3)} (낮을수록 좋음). 사람 단위 재표집 95% 개선 구간: {status.gain_95ci.map(x=>x.toFixed(3)).join(' ~ ')}. 개선이 보여도 사전 인원 기준을 낮추지 않았습니다.</p>
        {status.post_fit_persistence_check&&<p><b>추가 비교에서 드러난 한계:</b> “직전 속도를 그대로 유지”하는 단순 예측의 속도 오차는 {status.post_fit_persistence_check.persistence_mae_mps.toFixed(3)} m/s로 이 모델의 {status.test.model_speed_mae_mps.toFixed(3)} m/s보다 낮았습니다. 따라서 모든 기준 모델보다 우수하다고 할 수 없습니다. 이 비교는 학습 후 추가 점검이며 모델과 채택 기준을 변경하지 않았습니다.</p>}
        <p><a href={status.source_url} target="_blank" rel="noreferrer">Microsoft GeoLife 원본 출처 ↗</a>, {status.license}</p><small className="walk-hash">모델 SHA-256 {status.model_id}</small>
      </section>
    </>}
  </main>;
}
