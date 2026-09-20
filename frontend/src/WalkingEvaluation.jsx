import React,{useEffect,useState} from 'react';
import './walking-evaluation.css';

export default function WalkingEvaluation({modelId}){
  const [result,setResult]=useState(null),[error,setError]=useState('');
  useEffect(()=>{
    const c=new AbortController();setResult(null);setError('');
    async function get(){
      try{
        const r=await fetch('/api/ai/walking/horizon-evaluation?model_id='+encodeURIComponent(modelId),{signal:c.signal});
        const v=await r.json();if(!r.ok)throw new Error(typeof v.detail==='string'?v.detail:'평가 조회 실패');
        if(v.model_id!==modelId)throw new Error('평가와 모델 버전이 다릅니다.');
        if(!c.signal.aborted)setResult(v);
      }catch(e){if(!c.signal.aborted)setError(e.message);}
    }
    get();return()=>c.abort();
  },[modelId]);
  const five=result?.horizons.find(h=>h.seconds===300);
  return <section className="walk-method walk-evaluation" data-testid="walking-horizon-evaluation" aria-label="시간 누적 예측 평가">
    <div className="walk-eyebrow">CLOSED-LOOP CHECK / 실제 도착점 비교</div>
    <h2>시간이 지나도 위치 분포가 맞을까요?</h2>
    {error?<p role="alert">{error}. 검증된 결과가 있는 것으로 간주하지 마세요.</p>:!result?<p role="status">평가 기록 확인 중…</p>:<>
      <p>출발 위치만 알고, 미래의 실제 속도와 방향을 알려 주지 않은 상태로 생성했습니다. 기존 평가자 <b>{result.people}명, 연속 5분 기록 {result.windows}개</b>를 사용한 후속 점검이며 새 외부 검증은 아닙니다.</p>
      <table><caption>모형이 예측한 80% 반경과 실제 도착점 포함률</caption><thead><tr><th scope="col">시간</th><th scope="col">모형 80% 반경</th><th scope="col">실제 포함률*</th></tr></thead><tbody>{result.horizons.map(h=><tr key={h.seconds}><th scope="row">{h.seconds<60?h.seconds+'초':h.seconds/60+'분'}</th><td>{h.radius_80_m.toFixed(0)} m</td><td>{(h.coverage_80_macro*100).toFixed(1)}%</td></tr>)}</tbody></table>
      <p className="walk-note">* 사람별 포함률을 먼저 계산한 뒤 같은 가중치로 평균했습니다. 전체 구간을 합친 비율과 다를 수 있습니다. 포함률은 발견률이나 POD가 아닙니다.</p>
      {five&&<p className="walk-evaluation-conclusion"><b>5분 뒤에는 과신 위험이 남습니다.</b> 모형의 80% 반경 안에 실제 도착점이 든 비율은 {(five.coverage_80_macro*100).toFixed(1)}%였습니다. 분포 점수는 학습 모델 {five.model_energy_m.toFixed(1)}, 일정 속도와 방향 기준 {five.ballistic_energy_m.toFixed(1)}로 평균 개선됐지만, 개선 구간({five.gain_vs_ballistic_95ci_m.map(x=>x.toFixed(2)).join(' ~ ')})에 0이 포함됩니다.</p>}
      <p>{result.metric_note} 경로 재학습이나 채택 기준 완화 없이 실패와 불확실성까지 표시했습니다. 실제 수색 지도와 추천에는 자동 적용하지 않습니다.</p>
    </>}
  </section>;
}
