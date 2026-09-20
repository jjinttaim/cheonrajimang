import React,{useEffect,useState} from 'react';
import {Users,Plus,Copy,Check,PauseCircle,PlayCircle,ShieldCheck,AlertTriangle,MapPin,Ban} from 'lucide-react';
import InfoTip from './InfoTip.jsx';
import {plain} from './text.js';
import './members.css';

async function call(path,options={}) {
  const r=await fetch('/api'+path,options);
  const data=await r.json().catch(()=>({detail:'요청을 처리하지 못했습니다.'}));
  if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력값을 확인해 주세요.');
  return data;
}
const post=(path,body={})=>call(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const ago=text=>{if(!text)return '기록 없음';const m=Math.round((Date.now()-new Date(text).getTime())/60000);return m<1?'방금':m+'분 전';};

export default function MembersPanel({state,onRefresh,onNotice,onError,busy}) {
  const [data,setData]=useState(null),[role,setRole]=useState('volunteer'),[label,setLabel]=useState(''),[issued,setIssued]=useState(null),[copied,setCopied]=useState(false),[working,setWorking]=useState(false),[confirm,setConfirm]=useState(false);
  const mission=state.id;
  async function load(){try{setData(await call('/missions/'+mission+'/members'));}catch(e){onError(e.message);}}
  useEffect(()=>{setIssued(null);setConfirm(false);load();},[mission,state.version]);
  useEffect(()=>{const t=setInterval(load,20000);return()=>clearInterval(t);},[mission]);
  const status=data?.status?.status||'ACTIVE';
  const handed=status==='HANDED_OVER';
  const zones=state.zones.features.filter(f=>f.properties.access!=='AGENCY_ONLY').slice(0,12);
  async function act(fn,message){if(working)return;setWorking(true);try{await fn();await load();if(message)onNotice(message);}catch(e){onError(e.message);}finally{setWorking(false);}}
  const joinUrl=code=>location.origin+'/?view=join#code='+code;
  return <section className="members-panel" aria-label="참여자 관리">
    <div className={'mission-status-card '+status}>
      <div><strong>{({ACTIVE:'수색 진행 중',PAUSED:'일괄 중지 상태',HANDED_OVER:'기관 인계 완료'})[status]}</strong><small>{plain(data?.status?.note)||(status==='ACTIVE'?'봉사자 화면에 정상 표시':'')}</small></div>
      {!handed&&<div className="status-buttons">
        {status==='ACTIVE'?<button className="button subtle" disabled={working||busy} onClick={()=>act(()=>post('/missions/'+mission+'/transition',{status:'PAUSED',note:'조정자 지시'}),'모든 봉사자 화면에 수색 중지를 표시합니다.')}><PauseCircle size={15}/> 일괄 중지</button>
        :<button className="button subtle" disabled={working||busy} onClick={()=>act(()=>post('/missions/'+mission+'/transition',{status:'ACTIVE'}),'수색을 재개했습니다.')}><PlayCircle size={15}/> 재개</button>}
        <button className="button subtle danger" disabled={working||busy} onClick={()=>setConfirm(true)}><ShieldCheck size={15}/> 기관 인계 완료</button>
      </div>}
    </div>
    {confirm&&!handed&&<div className="handover-confirm"><AlertTriangle size={17}/><div><strong>인계를 완료하면 되돌릴 수 없습니다.</strong><p>원본 GPX {state.tracks.length}건을 서버에서 삭제하고 참여 코드를 모두 해지합니다. 집계된 지도, 구역 통계, 영수증, 해시는 남습니다. 인계 패키지는 먼저 내려받아 두세요.</p><div className="status-buttons"><button className="button subtle" onClick={()=>setConfirm(false)}>취소</button><button className="button primary" disabled={working} onClick={()=>act(async()=>{await post('/missions/'+mission+'/transition',{status:'HANDED_OVER',note:'조정자 확인'});await onRefresh();setConfirm(false);},'원본 GPX를 삭제하고 임무를 인계 완료로 표시했습니다.')}>삭제하고 인계 완료</button></div></div></div>}
    {!handed&&<form className="member-form" onSubmit={e=>{e.preventDefault();act(async()=>{const r=await post('/missions/'+mission+'/members',{role,label});setIssued(r);setCopied(false);setLabel('');},'참여 코드를 발급했습니다. 코드는 지금만 표시됩니다.');}}>
      <div className="section-label"><span><Users size={14}/> 참여 코드 발급</span></div>
      <div className="ai-input-row"><label>역할<select aria-label="참여자 역할" value={role} onChange={e=>setRole(e.target.value)}><option value="volunteer">봉사자</option><option value="family">가족</option></select></label><label>표시 이름<input aria-label="참여자 이름" required maxLength={40} value={label} onChange={e=>setLabel(e.target.value)} placeholder="예: 2조 김OO"/></label></div>
      <button className="button primary full" disabled={working||busy}><Plus size={15}/> 코드 만들기</button>
    </form>}
    {issued&&<div className="issued-code" role="status"><span>{issued.label} ({issued.role==='volunteer'?'봉사자':'가족'}) 코드</span><code>{issued.code.slice(0,4)}-{issued.code.slice(4)}</code><small>휴대폰에서 아래 주소를 열고 코드를 입력합니다. 코드는 저장되지 않으니 지금 전달하세요.</small><button className="text-button" onClick={async()=>{try{await navigator.clipboard.writeText(joinUrl(issued.code));setCopied(true);}catch{onError('클립보드를 사용할 수 없습니다. 주소를 직접 적어 주세요: '+joinUrl(issued.code));}}}>{copied?<><Check size={13}/> 복사됨</>:<><Copy size={13}/> 참여 주소 복사</>}</button><small className="mono">{joinUrl(issued.code)}</small></div>}
    <div className="section-label"><span>참여자</span><span>{data?.members.length||0}명 <InfoTip label="참여자 화면 설명"><p>봉사자 화면에는 배정 구역, 인상착의 요약, 안전 문구만 보이고, 가족 화면에는 진행 상황만 보입니다. 확률 지도와 다른 대원의 위치는 조정자 화면에만 있습니다.</p></InfoTip></span></div>
    {data&&!data.members.length&&<p className="muted intro">아직 참여자가 없습니다. 코드를 발급해 봉사자와 가족 화면을 열어 주세요.</p>}
    {data?.members.map(m=><article key={m.id} className={'member-card '+(m.revoked?'revoked':'')}>
      <div className="member-head"><strong>{m.label}</strong><span className={'status-chip '+(m.revoked?'REJECTED':m.role==='volunteer'&&m.overdue?'OVERDUE':'APPLIED')}>{m.revoked?'해지':m.role==='volunteer'?(m.overdue?'체크인 지연':'체크인 정상'):'가족'}</span></div>
      <p className="muted small-text meta-line"><span>{m.role==='volunteer'?'봉사자':'가족'}</span><span>위치정보 동의 {m.consented?'완료':'전'}</span>{m.role==='volunteer'&&<span>마지막 체크인 {ago(m.last_checkin_at)} (기준 {Math.round(m.checkin_interval/60)}분)</span>}{m.tracks&&Object.keys(m.tracks).length>0&&<span>제출 {Object.entries(m.tracks).map(([k,v])=>({UPLOADED:'대기',APPLIED:'반영',REJECTED:'제외'})[k]+' '+v).join(', ')}</span>}</p>
      {m.role==='volunteer'&&!m.revoked&&!handed&&<label className="assign-row"><MapPin size={13}/> 배정 구역<select aria-label={m.label+' 배정 구역'} value={m.zone??''} disabled={working} onChange={e=>act(()=>post('/missions/'+mission+'/members/'+m.id+'/assign',{zone:e.target.value===''?null:Number(e.target.value)}),'배정을 근거 장부에 기록했습니다.')}><option value="">미배정</option>{zones.map(f=><option key={f.properties.id} value={f.properties.id}>{f.properties.label} ({f.properties.rank}위)</option>)}{m.zone!==null&&!zones.some(f=>f.properties.id===m.zone)&&<option value={m.zone}>구역 #{m.zone}</option>}</select></label>}
      {!m.revoked&&!handed&&<button className="text-button muted" disabled={working} onClick={()=>act(()=>post('/missions/'+mission+'/members/'+m.id+'/revoke'),'참여 코드를 해지했습니다.')}><Ban size={12}/> 코드 해지</button>}
    </article>)}
  </section>;
}
