import React,{useEffect,useRef,useState} from 'react';
import {Radar,LoaderCircle,Check,ArrowUpRight} from 'lucide-react';
import {createLatestPlanQueue} from './plan-queue.js';
import InfoTip from './InfoTip.jsx';
import {plain} from './text.js';
import './ai-plan.css';

async function request(path,body,signal) {
  const r=await fetch('/api'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal}:{signal});
  const data=await r.json();
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력값 또는 연결을 확인해 주세요.');
  return data;
}

export default function AIPlanPanel({state,bounds,projection,mode,plan,onPlan,onSelect,onRefresh,onOpenVersion}) {
  const lonMin=bounds?.lon?.[0]??126.02,lonMax=bounds?.lon?.[1]??127.08,latMin=bounds?.lat?.[0]??33.0,latMax=bounds?.lat?.[1]??33.67;
  const [status,setStatus]=useState(null),[error,setError]=useState('');
  const [computing,setComputing]=useState(false),[actionBusy,setActionBusy]=useState(false);
  const busy=computing||actionBusy;
  const [minutes,setMinutes]=useState(30),[distribution,setDistribution]=useState('TIME_SCENARIOS');
  const [endpointModel,setEndpointModel]=useState('lognorm');
  const [movingTarget,setMovingTarget]=useState(true);
  const [teams,setTeams]=useState(()=>[{name:'1팀',lon:state.params.lon,lat:state.params.lat,search_type:'sweep',team_size:4,spacing_m:15}]);
  const [showComponents,setShowComponents]=useState(false);
  const [auto,setAuto]=useState(false),[details,setDetails]=useState(false),[clock,setClock]=useState(Date.now());
  const [history,setHistory]=useState([]);
  const generation=useRef(0),queue=useRef(null),latest=useRef(null),callbacks=useRef(null);
  callbacks.current={onPlan};
  useEffect(()=>{
    queue.current=createLatestPlanQueue({onResult:v=>{setError('');callbacks.current.onPlan(v);},onError:e=>setError(e.message),onBusy:setComputing});
    return()=>{generation.current++;queue.current.close();};
  },[]);
  useEffect(()=>{let active=true;request('/ai/status').then(v=>{if(active)setStatus(v);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[]);
  useEffect(()=>{const timer=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(timer);},[]);
  useEffect(()=>{setTeams([{name:'1팀',lon:state.params.lon,lat:state.params.lat,search_type:'sweep',team_size:4,spacing_m:15}]);onPlan(null);setAuto(false);},[state.id]);
  useEffect(()=>{let active=true;request('/missions/'+state.id+'/plans').then(v=>{if(active)setHistory(v);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[state.id,state.version,plan?.id,plan?.status]);
  const expired=plan&&new Date(plan.expires_at).getTime()<clock;
  const disabled=!status?.available||state.version!==state.current_version||projection?.expired||(mode!=='saved'&&!projection);
  const hardContext=JSON.stringify([state.id,state.version,state.current_version,minutes,distribution,endpointModel,movingTarget,teams,mode,!!projection,!!projection?.expired]);
  const context=JSON.stringify([hardContext,projection?.projection_seconds||0]);
  useEffect(()=>{
    generation.current++;queue.current.invalidate();setActionBusy(false);onPlan(null);
  },[hardContext]);
  useEffect(()=>{
    if(!auto||disabled){queue.current.clearPending();return;}
    const timer=setTimeout(()=>latest.current?.(),350);
    return()=>clearTimeout(timer);
  },[context,auto,disabled]);
  function compute() {
    if(disabled||actionBusy)return;
    setError('');
    const body={
        base_version:state.version,model_id:status.bundle_id,teams,minutes,
        distribution_mode:distribution,projection_seconds:distribution==='TIME_SCENARIOS'?(projection?.projection_seconds||0):0,
        temporal_projection:distribution==='TIME_SCENARIOS'&&!!projection,
        endpoint_model:distribution==='ENDPOINT_REFERENCE'?endpointModel:'lognorm',
        moving_target:distribution==='TIME_SCENARIOS'&&!!projection&&movingTarget,
        idempotency_key:crypto.randomUUID(),
    };
    const path='/missions/'+state.id+'/plans';
    queue.current.submit(context,()=>request(path,body));
  }
  latest.current=compute;
  useEffect(()=>{
    if(!auto)return;
    const timer=setInterval(()=>{if(!document.hidden&&!busy)latest.current?.();},30000);
    return()=>clearInterval(timer);
  },[auto,busy]);
  async function approve() {
    const seq=++generation.current;setActionBusy(true);setError('');
    try{await request('/missions/'+state.id+'/plans/'+plan.id+'/approve',{expected_version:state.version});if(seq===generation.current){onPlan({...plan,status:'APPROVED'});await onRefresh();}}
    catch(e){if(seq===generation.current)setError(e.message);}finally{if(seq===generation.current)setActionBusy(false);}
  }
  async function openPlan(id) {
    const seq=++generation.current;queue.current.invalidate();setAuto(false);setActionBusy(true);setError('');
    try {const result=await request('/missions/'+state.id+'/plans/'+id);if(seq===generation.current)onPlan(result);}
    catch(e){if(seq===generation.current)setError(e.message);}finally{if(seq===generation.current)setActionBusy(false);}
  }
  function editTeam(i,key,value){setTeams(list=>list.map((t,j)=>j===i?{...t,[key]:value}:t));}
  return <section className="ai-plan-panel" aria-label="학습 모델 수색 계획">
    <div className="ai-model-card tip-host"><div><Radar size={18}/><strong>천라지망 AI</strong><span>{status?.available?'학습 모델 연결':'모델 확인 중'}</span></div>
      {status?.available&&<InfoTip corner label="학습 모델 설명"><p>학습 거리 분포가 기본 지도의 거리 시나리오를, 학습 수색대 속도가 경로 시간을 정합니다.</p><p>실종자의 시간별 이동과 탐지율은 아직 가정입니다.</p>{status.yosar_endpoint&&<p>YOSAR {status.yosar_endpoint.cases}개 사건 키의 거리 모델은 별도로 두며, 두 자료를 합치거나 사건 수로 더하지 않습니다.</p>}</InfoTip>}
      {status?.available?<p>실제 사건 {status.endpoint.cases}건, 수색대 {status.searcher_speed.tracks}트랙 학습</p>:<p>{plain(status?.reason)||'학습 상태를 불러오고 있습니다.'}</p>}
      <div className="ai-links"><button className="ai-text-button" onClick={()=>setDetails(v=>!v)} aria-expanded={details}>학습 결과와 한계 {details?'접기':'보기'}</button>
      {status?.components&&<button className="ai-text-button" onClick={()=>setShowComponents(v=>!v)} aria-expanded={showComponents}>학습과 가정 구분 {showComponents?'접기':'보기'}</button>}</div>
      {showComponents&&status?.components&&<table className="ai-components" data-testid="ai-components"><thead><tr><th>요소</th><th>상태</th></tr></thead><tbody>{status.components.map(c=><tr key={c.key}><td><strong>{plain(c.name)}</strong><br/><small>{plain(c.detail)}</small><br/><small className="muted">쓰이는 곳: {plain(c.used_in)}</small>{c.caveat&&<><br/><small className="caveat">{plain(c.caveat)}</small></>}</td><td><span className={'component-status '+c.status}>{({LEARNED:'학습',LEARNED_SEPARATE:'학습(별도)',NOT_ADOPTED:'연구, 미채택',PHYSICS_ASSUMPTION:'물리 가정',ASSUMPTION:'가정',DISABLED_BY_PROFILE:'비활성'})[c.status]||c.status}</span></td></tr>)}</tbody></table>}
      {details&&status?.available&&<div className="ai-evaluation"><p>속도 오차(트랙별 평균)<br/>단순 기준 {status.searcher_speed.baseline_track_macro_mae_mps.toFixed(3)} → 학습 모델 {status.searcher_speed.forest_track_macro_mae_mps.toFixed(3)} m/s</p><p>발견점 80% 반경의 평가 포함률: {(status.endpoint.models.lognorm.leave_one_case_out.coverage[1]*100).toFixed(1)}%<br/>보정 완료된 80% 확률이 아닙니다.</p><ul>{status.limitations.map(v=><li key={v}>{plain(v)}</li>)}</ul><small>모델 {status.bundle_id.slice(0,16)}…</small></div>}
      {details&&status?.yosar_endpoint&&<div className="ai-evaluation"><p>YOSAR 지역 분리 평가<br/>80% 반경의 가능한 포함률: {(status.yosar_endpoint.spatial_group_5fold.coverage_lower[1]*100).toFixed(1)}–{(status.yosar_endpoint.spatial_group_5fold.coverage_upper[1]*100).toFixed(1)}%</p><small>좌표 오차에 따른 하한과 상한이며 신뢰구간이 아닙니다. 보정 완료나 국내 성능을 뜻하지 않습니다.</small></div>}
    </div>
    <form onSubmit={e=>{e.preventDefault();compute();}} className="ai-plan-form">
      <div className="ai-form-head"><span>계획 입력</span><InfoTip label="계획 입력 설명"><p>팀 인원과 간격으로 유효 탐지 밴드를 만듭니다(가정). 복귀 시간도 포함합니다. 출발 위치는 이 임무의 분석 창(마지막 확인 위치 주변 15.36 km × 15.36 km) 안이어야 합니다.</p><p>수색 중 대상 이동 계산은 시간 미리보기와 실시간 추정에서 적용되며, 기준 지도에서는 고정 분포를 씁니다. 15초 간격으로 계산하고 규모가 크면 경로를 표집합니다.</p><p>자동 갱신은 지도 위 실시간 추정과 함께 쓰며, 새 추천이 확정 배정을 바꾸지 않습니다. 계산 중에는 최신 요청 하나만 기다렸다 이어 계산하므로 표시된 계획의 기준 시각을 확인하세요.</p></InfoTip></div>
      <label>위치 분포<select aria-label="AI 계획 위치 분포" value={distribution} onChange={e=>setDistribution(e.target.value)}><option value="TIME_SCENARIOS">현재 지도 (시간 이동 가정)</option><option value="ENDPOINT_REFERENCE">학습 발견점 참고 (시간 예측 아님)</option></select></label>
      {distribution==='ENDPOINT_REFERENCE'&&<p className="ai-warning">계산 후 학습 발견점 분포를 지도에 표시합니다. 현재 시점 위치가 아니라 발견점 거리의 참고 분포입니다.</p>}
      {distribution==='ENDPOINT_REFERENCE'&&<label>학습 자료<select aria-label="발견점 학습 자료" value={endpointModel} onChange={e=>setEndpointModel(e.target.value)}>{(status?.endpoint_options||[{key:'lognorm',label:'기존 65건 거리 모델'}]).map(o=><option key={o.key} value={o.key}>{o.label}</option>)}</select></label>}
      {distribution==='ENDPOINT_REFERENCE'&&endpointModel==='yosar_interval_lognorm'&&<p className="ai-warning">CC BY-NC-SA 4.0, 비상업적 연구 참고. 좌표 오차의 미터 단위는 논문 기반 추론이며, 현재 서비스의 표본은 원 논문과 수가 다릅니다. 국내 실종 사건 성능은 검증 전입니다.</p>}
      {distribution==='TIME_SCENARIOS'&&<label className="ai-auto"><input type="checkbox" checked={movingTarget} onChange={e=>setMovingTarget(e.target.checked)}/>수색 중 대상 이동도 계산</label>}
      <div className="ai-input-row"><label>계획 시간 (분)<input aria-label="AI 계획 시간" type="number" min="5" max="120" required value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/></label><label>수색팀 수<select aria-label="AI 수색팀 수" value={teams.length} onChange={e=>{const n=Number(e.target.value);setTeams(old=>Array.from({length:n},(_,i)=>old[i]||{name:(i+1)+'팀',lon:state.params.lon,lat:state.params.lat,search_type:'sweep',team_size:4,spacing_m:15}));}}>{[1,2,3,4].map(n=><option key={n}>{n}</option>)}</select></label></div>
      {teams.map((t,i)=><fieldset key={i}><legend>{t.name} 출발 위치</legend><div className="ai-input-row"><label>위도<input aria-label={t.name+' 출발 위도'} type="number" step="0.000001" min={latMin} max={latMax} required value={t.lat} onChange={e=>editTeam(i,'lat',Number(e.target.value))}/></label><label>경도<input aria-label={t.name+' 출발 경도'} type="number" step="0.000001" min={lonMin} max={lonMax} required value={t.lon} onChange={e=>editTeam(i,'lon',Number(e.target.value))}/></label></div><label>수색 방식<select aria-label={t.name+' 수색 방식'} value={t.search_type} onChange={e=>editTeam(i,'search_type',e.target.value)}><option value="sweep">정밀 수색</option><option value="hasty">신속 수색</option><option value="paired">짝 수색</option></select></label><div className="ai-input-row"><label>인원<input aria-label={t.name+' 인원'} type="number" min="1" max="12" required value={t.team_size} onChange={e=>editTeam(i,'team_size',Number(e.target.value))}/></label><label>간격 (m)<input aria-label={t.name+' 대원 간격'} type="number" min="3" max="60" required value={t.spacing_m} onChange={e=>editTeam(i,'spacing_m',Number(e.target.value))}/></label></div><small>밴드 폭 {(t.team_size-1)*t.spacing_m} m, 탐지 노력 {t.team_size}배 (가정)</small></fieldset>)}
      <label className="ai-auto"><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/>새 지도와 시간으로 추천 자동 갱신</label>
      <button className="button primary full" disabled={busy||disabled} type="submit">{busy?<LoaderCircle size={16} className="spin"/>:<Radar size={16}/>} {busy?'경로와 시간 계산 중':'AI 수색 계획 계산'}</button>
    </form>
    {history.length>0&&<section className="ai-history" aria-label="저장한 AI 계획"><div className="section-label"><span>저장한 계획 다시 열기</span><span>확정과 최근 20건 <InfoTip label="저장한 계획 설명"><p>확정한 계획과 최근 추천 20건을 다시 열 수 있습니다. 자동 갱신은 확정 배정을 바꾸지 않습니다.</p></InfoTip></span></div>{history.map(p=><button key={p.id} className="ai-history-item" disabled={busy} onClick={()=>openPlan(p.id)}><strong>{({APPROVED:'확정',SUPERSEDED:'대체됨',PROPOSED:'추천'})[p.status]} v{p.base_version}</strong><span>{p.distribution_mode==='ENDPOINT_REFERENCE'?'발견점 참고':'시간 시나리오'}, {new Date(p.created_at).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})}</span></button>)}</section>}
    {error&&<p role="alert" className="ai-warning">{error}</p>}
    {plan&&<div className="ai-results" data-testid="ai-results"><div className="section-label"><strong>{plan.status==='APPROVED'?'확정한 모의 계획':'추천 경로'}</strong><span>{plan.compute_seconds.toFixed(2)}초 계산 <InfoTip label="추천 경로 설명"><p>시간은 왕복을 포함하며, 경로는 현장 확인 전 제안입니다. 실제 GPS 수색 기록이 아닙니다.</p><p>지도의 초록은 수색 제안, 파랑은 이동, 회색은 복귀 경로입니다.</p>{plan.target_motion&&<p>수색 중 이동 반영: 가중 경로 수는 실제 사건 수가 아닙니다. {plan.target_motion.any_sampled===false?'원래 가중 경로를 전체 사용했지만 실제 가능한 모든 경로라는 뜻은 아닙니다.':'경로를 표집했으므로 표집에 따라 추천 구역이 달라질 수 있습니다.'} 지도는 계획 시작 시각의 입력 분포입니다.</p>}{plan.inputs&&<p>저장된 입력은 {plan.inputs.teams.length}팀, {plan.inputs.minutes}분입니다. 위 입력 폼을 바꾸어도 이 과거 계획은 수정되지 않습니다.</p>}</InfoTip></span></div>
      <p className="ai-time">기준 지도 v{plan.base_version} +{Math.floor(plan.projection_seconds/60)}분 (왕복 포함)</p>
      {plan.target_motion&&<p className="ai-warning" data-testid="ai-motion-caption">수색 중 이동 반영, {plan.target_motion.time_step_seconds}초 간격, 가중 경로 {plan.target_motion.unique_paths.join(' / ')}개, {plan.target_motion.any_sampled===false?'원래 가중 경로 전체 사용':'경로 표집 사용'}</p>}
      {plan.endpoint_model_info&&<p className="ai-time" data-testid="ai-endpoint-source">사용 모델: {plain(plan.endpoint_model_info.label)}{plan.endpoint_model_info.license&&<><br/>{plan.endpoint_model_info.attribution} <a href={plan.endpoint_model_info.license_url} target="_blank" rel="noreferrer">{plan.endpoint_model_info.license}</a></>}</p>}
      {plan.base_version!==state.version?<p className="ai-warning">다른 지도 버전의 과거 계획입니다. 현재 지도에는 겹쳐 표시하지 않습니다. <button className="ai-text-button" onClick={()=>onOpenVersion(plan.base_version)}>기준 지도 v{plan.base_version} 열기</button></p>:plan.display_summary?<p className="ai-warning" data-testid="ai-map-caption">지도: {plan.distribution_mode==='ENDPOINT_REFERENCE'?'학습 발견점 참고 분포 (시간 예측 아님)':'이 계획 계산 시점의 고정 분포'}<br/>지도 밖 잔여 {(plan.display_summary.outside_mixed*100).toFixed(2)}%, 새 계산 전까지 고정</p>:<p className="ai-warning">이전 계획에는 저장된 분포 지도가 없습니다. 지도는 기존 시나리오입니다.</p>}
      {plan.assignments.map((a,i)=><button className="ai-route-card" key={i} onClick={()=>onSelect(a.zone_id)}><strong>{a.team} → {a.zone_label} <ArrowUpRight size={15}/></strong><span>총 {a.total_minutes.toFixed(1)}분, 수색 경로 {a.search_distance_m.toFixed(0)} m</span><small>이동 {a.travel_minutes.toFixed(1)} / 수색 {a.search_minutes.toFixed(1)} / 복귀 {a.return_minutes.toFixed(1)}분</small><small>탐지효과 가정 범위 {(a.assumed_incremental_detection_range[0]*100).toFixed(2)}–{(a.assumed_incremental_detection_range[1]*100).toFixed(2)}%p</small>{a.team_size!==undefined&&<small>{a.team_size}명, 간격 {a.spacing_m} m, 밴드 {a.band_rows}행, 수색 면적 {(a.swept_area_m2/10000).toFixed(1)} ha</small>}</button>)}
      <p className="ai-route-key"><i className="key search"/>수색 제안 <i className="key transit"/>이동 <i className="key back"/>복귀</p>
      {plan.notes.map(n=><p key={n} className="ai-warning">{plain(n)}</p>)}
      <button className="button subtle full" disabled={busy||disabled||expired||plan.base_version!==state.version||['APPROVED','SUPERSEDED'].includes(plan.status)||!plan.assignments.length} onClick={approve}><Check size={15}/>{plan.status==='APPROVED'?'모의 계획 확정됨':plan.status==='SUPERSEDED'?'다른 계획으로 대체됨':expired?'추천 만료, 다시 계산하세요':'현장 검토 후 모의 계획 확정'}</button>
      <p className="ai-warning">{plain(plan.warning)}</p>
    </div>}
  </section>;
}
