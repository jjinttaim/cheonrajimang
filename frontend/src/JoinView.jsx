import React,{useEffect,useRef,useState} from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {ShieldCheck,Upload,Check,LoaderCircle,AlertTriangle,MapPin,Clock3,PauseCircle,Send} from 'lucide-react';
import {loadDetailedStyle,plainStyle} from './map-style.js';
import {plain} from './text.js';
import './styles.css';
import './join.css';

const KEY='cheonrajimang.joinCode';
async function api(path,options={},code) {
  const headers={...(options.headers||{})};if(code)headers['X-Role-Code']=code;
  const r=await fetch('/api'+path,{...options,headers});
  const data=await r.json().catch(()=>({detail:'요청을 처리하지 못했습니다.'}));
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력값을 확인해 주세요.');
  return data;
}
const post=(path,body,code)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},code);
function readCode(){const h=new URLSearchParams(location.hash.replace(/^#/,''));return h.get('code')||sessionStorage.getItem(KEY)||'';}

function ZoneMap({assignment}) {
  const ref=useRef(),mapRef=useRef();
  useEffect(()=>{
    if(!assignment?.geometry||!ref.current)return;
    let map,disposed=false;const abort=new AbortController();
    (async()=>{
      let style;try{style=await loadDetailedStyle(abort.signal);}catch{style=plainStyle();}
      if(disposed)return;
      const ring=assignment.geometry.coordinates[0];const lon=(ring[0][0]+ring[2][0])/2,lat=(ring[0][1]+ring[2][1])/2;
      map=new maplibregl.Map({container:ref.current,style,center:[lon,lat],zoom:15.2,attributionControl:false,interactive:true});
      mapRef.current=map;
      map.addControl(new maplibregl.ScaleControl({maxWidth:80,unit:'metric'}),'bottom-left');
      map.on('style.load',()=>{
        map.addSource('zone',{type:'geojson',data:{type:'Feature',geometry:assignment.geometry}});
        map.addLayer({id:'zone-fill',type:'fill',source:'zone',paint:{'fill-color':'#2b8a8f','fill-opacity':.18}});
        map.addLayer({id:'zone-line',type:'line',source:'zone',paint:{'line-color':'#13315a','line-width':3}});
      });
    })();
    return()=>{disposed=true;abort.abort();map?.remove();};
  },[assignment?.zone_id]);
  if(!assignment?.geometry)return null;
  return <div className="join-map" ref={ref}/>;
}

export default function JoinView() {
  const [code,setCode]=useState(readCode),[view,setView]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(''),[agree,setAgree]=useState(false),[notice,setNotice]=useState('');
  const [team,setTeam]=useState({team_size:1,spacing_m:15}),[clue,setClue]=useState(''),[now,setNow]=useState(Date.now());
  const fileRef=useRef();
  async function run(label,fn){if(busy)return;setBusy(label);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
  async function join(consent){const v=await post('/join',{code,consent_version:consent?undefined:undefined,...(consent?{consent_version:consent}:{})});sessionStorage.setItem(KEY,code);setView(v);}
  useEffect(()=>{if(code)run('연결 중',()=>join());},[]);
  useEffect(()=>{if(!view)return;const t=setInterval(()=>api('/join/state',{},code).then(setView).catch(e=>setError(e.message)),20000);return()=>clearInterval(t);},[view?.role,code]);
  useEffect(()=>{const t=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(t);},[]);
  useEffect(()=>{if(notice){const t=setTimeout(()=>setNotice(''),5000);return()=>clearTimeout(t);}},[notice]);
  const header=<header className="join-header"><img src="/logo-mark.png" alt=""/><div><strong>천라지망</strong><small>{view?({volunteer:'봉사자 화면',family:'가족 화면'})[view.role]:'참여자 연결'}</small></div></header>;
  if(!view)return <main className="join"> {header}
    <form className="join-card" onSubmit={e=>{e.preventDefault();run('연결 중',()=>join());}}>
      <h1>참여 코드를 입력하세요</h1><p className="muted">조정자에게 받은 8자리 코드입니다. 코드는 이 임무에서만 쓰이며 화면에는 배정 구역과 안전 안내만 표시됩니다.</p>
      <input aria-label="참여 코드" autoFocus value={code} onChange={e=>setCode(e.target.value.toUpperCase())} placeholder="예: K7QW-4M2P" maxLength={12} autoComplete="off"/>
      <button className="button primary full" disabled={!!busy||code.replace(/[^A-Z0-9]/gi,'').length<8}>{busy?<LoaderCircle size={16} className="spin"/>:<Check size={16}/>} 연결</button>
      {error&&<p role="alert" className="join-error"><AlertTriangle size={15}/>{error}</p>}
      <p className="join-foot">판단 보조용 훈련 도구입니다. 실제 실종 사건의 안전 채널이 아닙니다.</p>
    </form></main>;
  const {mission,member}=view;
  const banner=view.handed_over?<div className="join-banner handed"><ShieldCheck size={16}/> 이 임무는 기관에 인계되어 종료되었습니다. 참여 코드는 더 이상 유효하지 않습니다.</div>
    :view.paused?<div className="join-banner paused"><PauseCircle size={16}/> 조정자 지시: 수색을 즉시 중지하고 안전한 곳에서 대기하세요. {mission.status_note&&<small>{plain(mission.status_note)}</small>}</div>:null;
  if(view.role==='volunteer'&&view.needs_consent)return <main className="join">{header}{banner}
    <section className="join-card"><h1>위치정보 이용 동의</h1><p className="consent-text">{plain(view.consent_text)}</p>
      <label className="consent-check"><input type="checkbox" checked={agree} onChange={e=>setAgree(e.target.checked)}/> 위 내용을 읽었고 GPS 기록 제출에 동의합니다.</label>
      <button className="button primary full" disabled={!agree||!!busy} onClick={()=>run('동의 저장 중',()=>join(view.consent_version))}><Check size={16}/> 동의하고 시작</button>
      <p className="muted small-text">동의하지 않아도 배정 구역과 안내는 볼 수 있지만 기록을 제출할 수 없습니다. <button className="text-button" onClick={()=>setView({...view,needs_consent:false})}>동의 없이 보기</button></p>
      {error&&<p role="alert" className="join-error"><AlertTriangle size={15}/>{error}</p>}
    </section></main>;
  const remaining=member.last_checkin_at?Math.max(0,member.checkin_interval*1000-(now-new Date(member.last_checkin_at).getTime())):0;
  const mm=String(Math.floor(remaining/60000)).padStart(2,'0'),ss=String(Math.floor(remaining%60000/1000)).padStart(2,'0');
  return <main className="join">{header}{banner}
    <section className="join-card mission"><span className="eyebrow">{view.role==='volunteer'?'MY ASSIGNMENT':'PROGRESS'}</span><h1>{mission.name}</h1><p className="muted">{member.label}, 지도 버전 v{mission.version}</p></section>
    {view.role==='volunteer'&&<>
      <section className="join-card"><div className="section-label"><span><MapPin size={14}/> 배정 구역</span><span>{view.assignment?.label||'미배정'}</span></div>
        {view.assignment?<><ZoneMap assignment={view.assignment}/><p className="muted small-text">{plain(view.assignment.hazard_note)}</p></>:<p className="muted">조정자가 아직 구역을 배정하지 않았습니다. 이 화면은 20초마다 갱신됩니다.</p>}
        <div className="brief"><strong>찾는 사람</strong><p>{view.subject_brief}</p></div>
        <div className="safety"><ShieldCheck size={15}/><span>{plain(view.safety_text)}</span></div>
      </section>
      <section className="join-card checkin"><div className="section-label"><span><Clock3 size={14}/> 체크인</span><span className={member.overdue?'overdue':''}>{member.last_checkin_at?(member.overdue?'지연됨':'다음까지 '+mm+':'+ss):'아직 없음'}</span></div>
        <p className="muted small-text">{Math.round(member.checkin_interval/60)}분마다 눌러 주세요. 시간이 지나면 조정자 화면에 지연으로 표시됩니다. 응급 상황은 119에 먼저 신고하세요.</p>
        <button className="button primary full" disabled={!!busy||view.handed_over} onClick={()=>run('체크인 중',async()=>{await post('/join/checkin',{},code);setView(await api('/join/state',{},code));setNotice('체크인을 기록했습니다.');})}><Check size={16}/> 지금 체크인</button>
      </section>
      <section className="join-card"><div className="section-label"><span><Upload size={14}/> 수색 기록 제출</span></div>
        {view.can_upload?<>
          <p className="muted small-text">휴대폰 GPS 앱에서 내보낸 GPX 파일을 올립니다. 조정자가 미발견을 확정할 때까지 지도는 바뀌지 않습니다.</p>
          <div className="team-inputs"><label>함께 걸은 인원<input type="number" min="1" max="12" value={team.team_size} onChange={e=>setTeam({...team,team_size:Number(e.target.value)})}/></label><label>대원 간격 (m)<input type="number" min="3" max="60" value={team.spacing_m} onChange={e=>setTeam({...team,spacing_m:Number(e.target.value)})}/></label></div>
          <input ref={fileRef} type="file" accept=".gpx" hidden onChange={e=>{const f=e.target.files[0];e.target.value='';if(!f)return;run('기록 분석 중',async()=>{const body=new FormData();body.append('file',f);body.append('team_size',String(team.team_size));body.append('spacing_m',String(team.spacing_m));await api('/join/tracks',{method:'POST',body},code);setView(await api('/join/state',{},code));setNotice('기록을 제출했습니다. 조정자 확정을 기다립니다.');});}}/>
          <button className="button subtle full" disabled={!!busy} onClick={()=>fileRef.current.click()}><Upload size={16}/> GPX 파일 선택</button>
          {member.tracks&&Object.keys(member.tracks).length>0&&<p className="muted small-text">내 제출: {Object.entries(member.tracks).map(([k,v])=>({UPLOADED:'확정 대기',APPLIED:'반영 완료',REJECTED:'제외'})[k]+' '+v+'건').join(', ')}</p>}
        </>:<p className="muted small-text">{view.handed_over?'종료된 임무에는 제출할 수 없습니다.':'위치정보 이용에 동의한 뒤 제출할 수 있습니다.'}{!view.handed_over&&!member.consented&&<> <button className="text-button" onClick={()=>setView({...view,needs_consent:true})}>동의 화면 열기</button></>}</p>}
      </section>
    </>}
    {view.role==='family'&&<section className="join-card"><div className="section-label"><span>수색 진행</span><span>갱신 {new Date(view.progress.updated_at).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})}</span></div>
      <div className="progress-grid"><div><span>확정된 수색 기록</span><strong>{view.progress.applied_tracks}건</strong></div><div><span>수색한 구역</span><strong>{view.progress.searched_zones}곳</strong></div><div><span>수색 기록 영역</span><strong>{view.progress.coverage_km2.toFixed(2)} km²</strong></div><div><span>접수된 단서</span><strong>{view.progress.clues}건</strong></div></div>
      <p className="muted small-text">{plain(view.family_text)}</p>
    </section>}
    {!view.handed_over&&<section className="join-card"><div className="section-label"><span><Send size={14}/> {view.role==='volunteer'?'단서와 현장 메모':'정보 남기기'}</span></div>
      <form onSubmit={e=>{e.preventDefault();run('저장 중',async()=>{await post('/join/ledger',{note:clue},code);setClue('');setView(await api('/join/state',{},code));setNotice('조정자 장부에 기록했습니다. 지도는 자동으로 바뀌지 않습니다.');});}}>
        <textarea required minLength={2} maxLength={1000} rows={3} value={clue} onChange={e=>setClue(e.target.value)} placeholder="직접 본 것과 들은 것을 구분해 적어 주세요."/>
        <button className="button subtle full" disabled={!!busy||clue.length<2}><Send size={15}/> 조정자에게 전달</button>
      </form></section>}
    {busy&&<div className="toast busy"><LoaderCircle size={18} className="spin"/>{busy}</div>}
    {notice&&!busy&&<div role="status" className="toast"><Check size={18}/>{notice}</div>}
    {error&&<div role="alert" className="toast error"><AlertTriangle size={18}/><span>{error}</span></div>}
    <p className="join-foot">판단 보조용 훈련 도구입니다. 낮은 확률은 부재의 증거가 아니며, 이 화면은 안전 채널이 아닙니다. <button className="text-button" onClick={()=>{sessionStorage.removeItem(KEY);location.hash='';setView(null);setCode('');}}>연결 끊기</button></p>
  </main>;
}
