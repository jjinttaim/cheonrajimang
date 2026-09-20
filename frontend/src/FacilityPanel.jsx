import React, { useEffect } from 'react';
import { Store } from 'lucide-react';
import InfoTip from './InfoTip.jsx';
import {plain} from './text.js';
import './facilities.css';

export function FacilityControls({form,setForm,metadata}) {
  const available=metadata?.available;
  useEffect(()=>{
    if(!available&&form.facility_influence)setForm(current=>({...current,facility_influence:false}));
  },[available,form.facility_influence,setForm]);
  return <div className="facility-controls">
    <div className="toggle-row"><span>주변 시설 영향 가정 <InfoTip label="시설 가정 설명"><p>{available?`등록 시설 ${metadata.count}곳. 편의점, 식당, 정류장 등으로 향하는 경향을 행동 시나리오 B′에만 더합니다.`:'시설 데이터를 아직 준비하지 못했습니다.'}</p><p>방문 확률을 학습한 값이 아닙니다. 영업 여부와 실제 접근 가능성은 미확인이며, 시설에 머물거나 버스를 탄다고 가정하지 않습니다.</p></InfoTip></span><input type="checkbox" aria-label="주변 시설 영향 가정" disabled={!available} checked={!!form.facility_influence} onChange={e=>setForm({...form,facility_influence:e.target.checked})}/></div>
    {form.facility_influence&&<label className="range-label">가정 강도 (방문 확률 아님) <strong>{form.facility_strength.toFixed(1)}</strong><input aria-label="시설 영향 강도" type="range" min="0" max="1" step=".1" value={form.facility_strength} onChange={e=>setForm({...form,facility_strength:Number(e.target.value)})}/></label>}
  </div>;
}

export default function FacilityPanel({params,metadata,features,onCompare}) {
  const enabled=params.facility_influence&&params.facility_strength>0;
  const windowCount=metadata?.window_count??metadata?.count;
  const counts=features?features.reduce((acc,f)=>{const k=f.properties.category_label;acc[k]=(acc[k]||0)+1;return acc;},{}):(metadata?.categories||{});
  return <section className="facility-panel" aria-label="주변 시설 반영">
    <div className="facility-heading"><Store size={16}/><strong>주변 시설</strong><span>{enabled?'B′ 가정 반영':'계산 반영 꺼짐'}</span>{metadata?.available&&<InfoTip label="주변 시설 설명"><p>이 임무의 분석 창 주변에 OSM 등록 시설 {windowCount}개가 있습니다(제주 전체 {metadata.count}개, 영향 계산 가능 {metadata.eligible_count}개). 수집 {new Date(metadata.retrieved_at).toLocaleDateString('ko-KR')}, 영업, 폐업, 출입 여부는 미확인이며 목록에 없는 시설도 있을 수 있습니다.</p><p>{enabled?'시설 쪽 이동 선호를 넣은 미검증 가정입니다. 거리와 지형 시나리오는 그대로 유지합니다.':'지도 표시와 계산 반영은 별개입니다. 기존 임무의 가정은 바꾸지 않습니다.'}</p></InfoTip>}</div>
    {metadata?.available?<>
      <div className="facility-map-key"><span><i className="supplies"/>편의점, 마트</span><span><i/>기타 시설 (눌러서 확인)</span></div>
      <details><summary>시설 종류 {windowCount}개</summary>
        <div className="facility-counts">{Object.entries(counts).map(([name,count])=><span key={name}>{plain(name)}<b>{count}</b></span>)}</div>
      </details>
      <button className="button subtle full" onClick={onCompare}>{enabled?'시설 가정을 바꿔 새 임무':'시설 가정을 켜서 새 임무'}</button>
    </>:<p>시설 정보를 불러오는 중이거나 데이터가 준비되지 않았습니다.</p>}
  </section>;
}
