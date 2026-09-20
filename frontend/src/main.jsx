import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import maplibregl from 'maplibre-gl';
import { Radar, Map, Route, BookOpen, History, ArrowUpRight, ArrowDownToLine, Plus, X, Crosshair, Layers, ChevronRight, Check, CheckCheck, Upload, FileCheck2, ShieldCheck, Info, LocateFixed, SlidersHorizontal, Navigation, Clock3, CircleHelp, LoaderCircle, ArrowRight, CircleDot, AlertTriangle, PanelLeftClose, PanelLeftOpen, Search, Fingerprint, FileText, Play, MapPin, Undo2, Users, PauseCircle } from 'lucide-react';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import './map-enhancements.css';
import LiveForecastPanel from './LiveForecastPanel.jsx';
import AIPlanPanel from './AIPlanPanel.jsx';
import WalkingLab from './WalkingLab.jsx';
import JoinView from './JoinView.jsx';
import MembersPanel from './MembersPanel.jsx';
import TimeControls from './TimeControls.jsx';
import { timeDefaults, missionPayload, copyTime, elapsedHours } from './time-input.js';
import FacilityPanel, { FacilityControls } from './FacilityPanel.jsx';
import { plain } from './text.js';
import { nearestRegion, autoMissionName } from './regions.js';
import InfoTip from './InfoTip.jsx';
import { loadDetailedStyle, localAnalysisStyle } from './map-style.js';
import { SATELLITE, satelliteSource, displaySatellite } from './satellite.js';

const API = '/api';
const formatPct = n => (n * 100).toFixed(n * 100 < 1 ? 2 : 1) + '%';
const when = text => new Date(text).toLocaleString('ko-KR', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
async function api(path, options={}) {
  const response = await fetch(API + path, options);
  if (!response.ok) {
    let error; try { error=await response.json(); } catch { error={detail:'요청을 처리하지 못했습니다.'}; }
    throw new Error(typeof error.detail==='string' ? error.detail : Array.isArray(error.detail)?error.detail.map(item=>item.msg).join(' / '):'입력값을 확인해 주세요.');
  }
  return response.json();
}
const post = (path,body={}) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const EMPTY={type:'FeatureCollection',features:[]};
function IconButton({label,children,...props}) { return <button className="icon-button" aria-label={label} title={label} {...props}>{children}</button>; }
function Modal({title,onClose,children,wide=false}) {
  const ref=useRef();
  useEffect(()=>{ref.current.showModal();return()=>ref.current?.close();},[]);
  return <dialog ref={ref} className={wide?'modal wide':'modal'} onCancel={onClose} onClick={e=>{if(e.target===ref.current) onClose();}}><div className="modal-head"><h2>{title}</h2><IconButton label="닫기" onClick={onClose}><X size={20}/></IconButton></div>{children}</dialog>;
}

function MapCanvas({meta,state,aiPlan,layer,phase,selected,onSelect,picking,onPick,previewTrack,showGrid,opacity,showAnalysis,onBasemapChange,facilityData,showFacilities,basemap,satelliteStatus,onSatelliteStatus,satelliteAttempt,showSatelliteLabels}) {
  const container=useRef(), mapRef=useRef(), callbacks=useRef(), markerRef=useRef(), baseLayersRef=useRef([]), basemapRef=useRef(''), missionRef=useRef('');
  const [loaded,setLoaded]=useState(false),[styleEpoch,setStyleEpoch]=useState(0),[mapError,setMapError]=useState(''),[retry,setRetry]=useState(0);
  callbacks.current={onSelect,onPick,picking,state,showAnalysis,onBasemapChange};
  useEffect(()=>{
    if(!meta||!container.current)return;
    let map,disposed=false,usingVector=true;
    const abort=new AbortController();
    const timeout=setTimeout(()=>abort.abort(),8000);
    onBasemapChange('loading');
    setMapError('');
    const fallbackStyle=()=>{const s=callbacks.current.state;return localAnalysisStyle({corners:s.window.corners,basemap_url:'/api/missions/'+s.id+'/basemap.png'});};
    async function start() {
      let style;
      try { style=await loadDetailedStyle(abort.signal); }
      catch { style=fallbackStyle();usingVector=false; }
      finally { clearTimeout(timeout); }
      if(disposed)return;
      try {
        const initial=callbacks.current.state;
        map=new maplibregl.Map({
          container:container.current,style,
          center:[initial.params.lon,initial.params.lat],zoom:13.5,maxZoom:20,minZoom:10.5,
          canvasContextAttributes:{antialias:true},attributionControl:false,
        });
        mapRef.current=map;
        map.addControl(new maplibregl.NavigationControl({showCompass:false}),'bottom-right');
        map.addControl(new maplibregl.ScaleControl({maxWidth:90,unit:'metric'}),'bottom-left');
        map.on('style.load',()=>{
          if(disposed)return;
          setLoaded(false);
          const current=callbacks.current.state;
          const layers=map.getStyle().layers;
          baseLayersRef.current=structuredClone(layers);
          // Roads/buildings and their labels stay ABOVE the probability tint.
          const detailAnchor=layers.find(l=>['transportation','building'].includes(l['source-layer']))?.id;
          const labelAnchor=layers.find(l=>l.type==='symbol')?.id;
          map.addSource('heat',{type:'image',url:'/api/missions/'+current.id+'/layers/combined.png?version='+current.version,coordinates:current.window.corners});
          map.addLayer({id:'heat',type:'raster',source:'heat',paint:{'raster-opacity':.42,'raster-fade-duration':0}},detailAnchor);
          map.addSource('shadow',{type:'image',url:'/api/missions/'+current.id+'/layers/shadow.png?version='+current.version,coordinates:current.window.corners});
          map.addLayer({id:'shadow',type:'raster',source:'shadow',paint:{'raster-opacity':.8,'raster-fade-duration':0}},detailAnchor);
          map.addSource('zones',{type:'geojson',data:EMPTY});
          map.addLayer({id:'zones-fill',type:'fill',source:'zones',paint:{'fill-color':['case',['==',['get','access'],'AGENCY_ONLY'],'#ba684f','#158877'],'fill-opacity':['case',['<=',['get','rank'],3],.08,0.005]}},detailAnchor);
          map.addLayer({id:'zones-line',type:'line',source:'zones',paint:{'line-color':['case',['<=',['get','rank'],3],'#1e8271','#517872'],'line-width':['case',['<=',['get','rank'],3],1.8,.55],'line-opacity':.32}},labelAnchor);
          map.addSource('facilities',{type:'geojson',data:EMPTY});
          map.addLayer({id:'facility-dots',type:'circle',source:'facilities',minzoom:12,paint:{'circle-radius':['interpolate',['linear'],['zoom'],12,2.8,17,6],'circle-color':['case',['==',['get','category'],'supplies'],'#886341','#746694'],'circle-stroke-color':'#fff','circle-stroke-width':1.5,'circle-opacity':.9}});
          map.addSource('selected',{type:'geojson',data:EMPTY});
          map.addLayer({id:'selected-fill',type:'fill',source:'selected',paint:{'fill-color':'#1f8a74','fill-opacity':.08}},detailAnchor);
          map.addLayer({id:'selected-line',type:'line',source:'selected',paint:{'line-color':'#146d5e','line-width':2.5}},labelAnchor);
          map.addSource('tracks',{type:'geojson',data:EMPTY});
          map.addLayer({id:'tracks',type:'line',source:'tracks',paint:{'line-color':['case',['==',['get','status'],'APPLIED'],'#087c70','#306bb4'],'line-width':2.7}},labelAnchor);
          map.addSource('ai-routes',{type:'geojson',data:EMPTY});
          map.addLayer({id:'ai-routes',type:'line',source:'ai-routes',paint:{'line-color':['match',['get','kind'],'SEARCH','#06836d','TRANSIT','#3572b3','#87918b'],'line-width':['case',['==',['get','kind'],'SEARCH'],4,2],'line-opacity':.9}});
          if(!markerRef.current) {
            const el=document.createElement('div');el.className='ipp-marker';el.innerHTML='<i></i><span>마지막 확인 위치</span>';
            markerRef.current=new maplibregl.Marker({element:el}).setLngLat([current.params.lon,current.params.lat]).addTo(map);
          }
          if(!usingVector)callbacks.current.onBasemapChange('fallback');
          setStyleEpoch(v=>v+1);setLoaded(true);
        });
        map.on('idle',()=>{if(usingVector&&!disposed)callbacks.current.onBasemapChange('vector');});
        map.on('click',e=>{
          if(callbacks.current.picking) {callbacks.current.onPick(e.lngLat);return;}
          if(map.getLayer('facility-dots')) {
            const poi=map.queryRenderedFeatures(e.point,{layers:['facility-dots']})[0];
            if(poi) {
              const content=document.createElement('div');content.className='facility-popup';
              const title=document.createElement('strong');title.textContent=poi.properties.name;
              const detail=document.createElement('div');detail.textContent=plain(poi.properties.category_label)+' (영업, 접근 미확인)';
              content.append(title,detail);new maplibregl.Popup().setLngLat(poi.geometry.coordinates).setDOMContent(content).addTo(map);return;
            }
          }
          if(!callbacks.current.showAnalysis||!map.getLayer('zones-fill'))return;
          const f=map.queryRenderedFeatures(e.point,{layers:['zones-fill']})[0];
          if(f)callbacks.current.onSelect(Number(f.properties.id));
        });
        map.on('error',e=>{
          console.warn('Map:',e.error?.message);
          if(usingVector&&(e.sourceId==='openmaptiles'||e.error?.message?.includes('tiles.openfreemap.org'))) {
            usingVector=false;
            setLoaded(false);
            callbacks.current.onBasemapChange('fallback');
            map.setStyle(fallbackStyle());
          }
        });
      } catch {
        setMapError('지도를 시작할 수 없습니다. 브라우저의 그래픽 가속 설정을 확인해 주세요.');
        callbacks.current.onBasemapChange('error');
      }
    }
    start();
    return()=>{disposed=true;clearTimeout(timeout);abort.abort();setLoaded(false);map?.remove();mapRef.current=null;markerRef.current=null;};
  },[meta,retry]);
  useEffect(()=>{
    const m=mapRef.current;if(!loaded||!m?.getSource('heat'))return;
    const heatUrl=aiPlan?.display_map_url||(state.forecast&&layer!=='shadow'?'/api/missions/'+state.id+'/forecast/'+layer+'.png?version='+state.version+'&seconds='+state.forecast.projection_seconds:'/api/missions/'+state.id+'/layers/'+layer+'.png?version='+state.version+'&phase='+phase);
    m.getSource('heat').updateImage({url:heatUrl,coordinates:state.window.corners});
    const url=previewTrack ? '/api/missions/'+state.id+'/tracks/'+previewTrack+'/shadow.png' : '/api/missions/'+state.id+'/layers/shadow.png?version='+state.version;
    m.getSource('shadow').updateImage({url,coordinates:state.window.corners});
    const basemapUrl='/api/missions/'+state.id+'/basemap.png';
    if(m.getSource('basemap')&&basemapRef.current!==basemapUrl){basemapRef.current=basemapUrl;m.getSource('basemap').updateImage({url:basemapUrl,coordinates:state.window.corners});}
    m.setPaintProperty('heat','raster-opacity',!showAnalysis||(!aiPlan?.display_map_url&&layer==='shadow')?0:['interpolate',['linear'],['zoom'],13,opacity,17,opacity*.45]);
    m.setPaintProperty('shadow','raster-opacity',!showAnalysis||phase==='prior'?0:(previewTrack||layer==='shadow'?.95:.65));
    const zones={...state.zones,features:state.zones.features.filter(f=>f.properties.rank<=100 || f.properties.has_track)};
    m.getSource('zones').setData(zones);
    m.setLayoutProperty('zones-line','visibility',showAnalysis&&showGrid?'visible':'none');
    m.setLayoutProperty('zones-fill','visibility',showAnalysis&&showGrid?'visible':'none');
    for(const id of ['selected-fill','selected-line','tracks'])m.setLayoutProperty(id,'visibility',showAnalysis?'visible':'none');
    const trackFeatures=state.tracks.filter(t=> (t.status==='APPLIED'&&t.applied_version<=state.version)||t.id===previewTrack).map(t=>({type:'Feature',geometry:t.summary.geometry,properties:{status:t.status}}));
    m.getSource('tracks').setData({type:'FeatureCollection',features:phase==='prior'?[]:trackFeatures});
    m.getSource('ai-routes')?.setData(aiPlan?.routes||EMPTY);
    m.setLayoutProperty('ai-routes','visibility',showAnalysis?'visible':'none');
    markerRef.current?.setLngLat([state.params.lon,state.params.lat]);
  },[loaded,styleEpoch,state,aiPlan,layer,phase,previewTrack,showGrid,opacity,showAnalysis]);
  useEffect(()=>{
    // A mission elsewhere on the island: move the camera to its last-seen point.
    const m=mapRef.current;if(!loaded||!m)return;
    if(missionRef.current&&missionRef.current!==state.id)m.jumpTo({center:[state.params.lon,state.params.lat],zoom:13.5});
    missionRef.current=state.id;
  },[loaded,state.id]);
  useEffect(()=>{
    const m=mapRef.current;if(!loaded||!m?.getSource('selected'))return;
    const f=state.zones.features.find(f=>f.properties.id===selected);
    m.getSource('selected').setData(f?{type:'FeatureCollection',features:[f]}:EMPTY);
    if(f) {const a=f.geometry.coordinates[0];m.easeTo({center:[(a[0][0]+a[2][0])/2,(a[0][1]+a[2][1])/2],zoom:Math.max(m.getZoom(),15),duration:550});}
  },[selected,loaded,styleEpoch,state.id]);
  useEffect(()=>{
    const m=mapRef.current;if(!loaded||!m?.getSource('facilities'))return;
    m.getSource('facilities').setData(facilityData||EMPTY);
    m.setLayoutProperty('facility-dots','visibility',showFacilities?'visible':'none');
  },[loaded,styleEpoch,facilityData,showFacilities]);
  useEffect(()=>{
    const m=mapRef.current;if(!loaded||!m?.getLayer('heat'))return;
    if(basemap!=='satellite'){
      displaySatellite(m,baseLayersRef.current,false);onSatelliteStatus('idle');return;
    }
    // Lazily request only visible tiles, after the user selects satellite mode.
    // Recreate just this source on retry; camera and all analysis sources survive.
    if(m.getLayer(SATELLITE.id))m.removeLayer(SATELLITE.id);
    if(m.getSource(SATELLITE.id))m.removeSource(SATELLITE.id);
    let failed=false,disposed=false,timer;
    const fail=()=>{
      if(disposed||failed)return;failed=true;clearTimeout(timer);
      displaySatellite(m,baseLayersRef.current,false);onSatelliteStatus('error');
    };
    const ready=e=>{
      if(disposed||failed||e.sourceId!==SATELLITE.id)return;
      if(m.isSourceLoaded(SATELLITE.id)){
        clearTimeout(timer);onSatelliteStatus('ready');
      }
    };
    const error=e=>{if(e.sourceId===SATELLITE.id||e.error?.message?.includes('tiles.maps.eox.at'))fail();};
    onSatelliteStatus('loading');
    m.on('sourcedata',ready);m.on('error',error);
    timer=setTimeout(fail,15000);
    try {
      m.addSource(SATELLITE.id,satelliteSource());
      m.addLayer({id:SATELLITE.id,type:'raster',source:SATELLITE.id,
        paint:{'raster-fade-duration':0,'raster-resampling':'linear'}},baseLayersRef.current[0]?.id);
      displaySatellite(m,baseLayersRef.current,true,showSatelliteLabels);
    } catch {fail();}
    return()=>{disposed=true;clearTimeout(timer);m.off('sourcedata',ready);m.off('error',error);};
  },[loaded,styleEpoch,basemap,satelliteAttempt]);
  useEffect(()=>{
    const m=mapRef.current;if(!loaded||!m?.getLayer(SATELLITE.id))return;
    displaySatellite(m,baseLayersRef.current,basemap==='satellite'&&satelliteStatus!=='error',showSatelliteLabels);
  },[loaded,styleEpoch,basemap,satelliteStatus,showSatelliteLabels]);
  useEffect(()=>{if(mapRef.current)mapRef.current.getCanvas().style.cursor=picking?'crosshair':'';},[picking]);
  return <>
    <div ref={container} className="map-canvas"/>
    {mapError&&<div className="map-error">{mapError}<button onClick={()=>setRetry(n=>n+1)}>다시 시도</button></div>}
    <button className="map-detail-zoom" aria-label="골목과 건물까지 확대" onClick={()=>mapRef.current?.easeTo({zoom:17.5,duration:650})}><Search size={14}/> 상세 확대</button>
    <button className="map-home" title="마지막 확인 위치로" onClick={()=>mapRef.current?.easeTo({center:[state.params.lon,state.params.lat],zoom:13.5,duration:650})}><LocateFixed size={19}/></button>
  </>;
}
function App() {
  const [meta,setMeta]=useState(null),[savedState,setState]=useState(null),[missions,setMissions]=useState([]),[active,setActive]=useState('');
  const [tab,setTab]=useState('ai'),[layer,setLayer]=useState('combined'),[phase,setPhase]=useState('post');
  const [aiPlan,setAIPlan]=useState(null);
  const [selected,setSelected]=useState(null),[previewTrack,setPreviewTrack]=useState(null),[modal,setModal]=useState(null);
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[showGrid,setShowGrid]=useState(true),[opacity,setOpacity]=useState(.42);
  const [forecast,setForecast]=useState(null),[timelineMode,setTimelineMode]=useState('saved');
  const projection=forecast?.mission===savedState?.id&&forecast?.base_version===savedState?.version&&timelineMode!=='saved'?forecast:null;
  const savedZones=new globalThis.Map(savedState?.zones.features.map(f=>[f.properties.id,f.properties])||[]);
  const timeState=projection?{...savedState,forecast:projection,outside_mixed:projection.outside_mixed,outside:projection.outside,inside_mass:projection.inside_mass,zones:{...projection.zones,features:projection.zones.features.map(f=>({...f,properties:{...f.properties,pod_low:savedZones.get(f.properties.id)?.pod_low||0,pod_high:savedZones.get(f.properties.id)?.pod_high||0,has_track:savedZones.get(f.properties.id)?.has_track||false,decision:savedZones.get(f.properties.id)?.decision||'PROPOSED',previous_rank:savedZones.get(f.properties.id)?.rank}}))}}:savedState;
  const activePlan=tab==='ai'&&aiPlan&&savedState&&aiPlan.mission===savedState.id&&aiPlan.base_version===savedState.version?aiPlan:null;
  const planMap=activePlan?.display_summary;
  const state=planMap?{...timeState,outside_mixed:planMap.outside_mixed,outside:planMap.outside,inside_mass:planMap.inside_mass,zones:planMap.zones}:timeState;
  useEffect(()=>{setForecast(null);setTimelineMode('saved');},[savedState?.id,savedState?.version]);
  const [facilityData,setFacilityData]=useState(null),[showFacilities,setShowFacilities]=useState(true);
  const [basemap,setBasemap]=useState('vector'),[satelliteStatus,setSatelliteStatus]=useState('idle'),[satelliteAttempt,setSatelliteAttempt]=useState(0),[showSatelliteLabels,setShowSatelliteLabels]=useState(true);
  const [showAnalysis,setShowAnalysis]=useState(true),[basemapStatus,setBasemapStatus]=useState('loading');
  const [picking,setPicking]=useState(false),[sidebar,setSidebar]=useState(true),[search,setSearch]=useState('');
  const [form,setForm]=useState(()=>({name:autoMissionName(126.268,33.397),autoName:true,lon:126.268,lat:33.397,hours:3,sigma:50,seed:42,facility_influence:true,facility_strength:.6,distance_model:'learned_lognormal',row_share:.1,subject_brief:'',...timeDefaults()}));
  // Keep the auto-generated name in step with the chosen point until the user edits the name.
  const place=(f,lon,lat)=>({...f,lon,lat,name:f.autoName?autoMissionName(lon,lat):f.name});
  const [team,setTeam]=useState({team_size:1,spacing_m:15});
  useEffect(()=>{
    let cancelled=false;
    const digest=savedState?.params.facility_snapshot||meta?.facilities?.snapshot;
    if(!digest||!savedState?.id){setFacilityData(null);return;}
    setFacilityData(null);
    // Only the POIs around this mission's analysis window are loaded.
    api('/facilities?snapshot='+digest+'&mission='+savedState.id).then(data=>{if(!cancelled)setFacilityData(data);}).catch(e=>{if(!cancelled)setError(e.message);});
    return()=>{cancelled=true;};
  },[savedState?.params.facility_snapshot,meta?.facilities?.snapshot,savedState?.id]);
  const [note,setNote]=useState(''),[noteSource,setNoteSource]=useState('조정자'),[noteTime,setNoteTime]=useState('');
  const [verification,setVerification]=useState(null);
  const uploadRef=useRef(),verifyRef=useRef(),requestSeq=useRef(0);
  async function refresh(mid=active,version) {
    const seq=++requestSeq.current;
    const result=await api('/missions/'+mid+(version===undefined?'':'?version='+version));
    if(seq===requestSeq.current)setState(result);
    return result;
  }
  useEffect(()=>{(async()=>{
    try {const [base,list]=await Promise.all([api('/base'),api('/missions')]);setMeta(base);setMissions(list);setActive(list[0].id);await refresh(list[0].id);}
    catch(e){setError(e.message);}
  })();},[]);
  useEffect(()=>{if(notice){const t=setTimeout(()=>setNotice(''),6000);return()=>clearTimeout(t);}},[notice]);
  async function run(label,fn) {if(busy)return;setBusy(label);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
  const zone=state?.zones.features.find(f=>f.properties.id===selected)?.properties;
  const top=state?.zones.features.filter(f=>f.properties.access!=='AGENCY_ONLY')||[];
  const waiting=state?.tracks.filter(t=>t.status==='UPLOADED').length||0;
  const isLatest=state && state.version===state.current_version;
  async function upload(file) {
    if(!file)return;
    await run('수색 기록 분석 중',async()=>{
      const body=new FormData();body.append('file',file);body.append('team_size',String(team.team_size));body.append('spacing_m',String(team.spacing_m));
      const tr=await api('/missions/'+active+'/tracks',{method:'POST',body});
      await refresh();setTab('tracks');setPreviewTrack(tr.id);setLayer('shadow');setNotice('트랙을 분석했습니다. 확률지도는 아직 변경되지 않았습니다.');
    });
  }
  async function addDemo(){
    await run('훈련 트랙 분석 중',async()=>{const tr=await post('/missions/'+active+'/demo-track',team);await refresh();setPreviewTrack(tr.id);setLayer('shadow');setTab('tracks');setNotice('합성 훈련 트랙이 준비되었습니다. 수색 결과를 확정해 보세요.');});
  }
  async function approveTrack(track) {
    await run('수색 결과 반영 중',async()=>{
      await post('/missions/'+active+'/tracks/'+track.id+'/apply',{outcome:'COMPLETED_NO_FIND',expected_version:state.version});
      await refresh();setPreviewTrack(null);setPhase('post');setModal(null);setNotice('미발견 결과를 반영하고 새 지도 버전과 재현 영수증을 저장했습니다.');
    });
  }
  if(!state||!meta)return <div className="boot"><img src="/logo-mark.png" alt="천라지망"/><h1>천라지망</h1><p>{error||'수색 워크스페이스를 준비하고 있습니다.'}</p>{error?<button onClick={()=>location.reload()}>다시 연결</button>:<LoaderCircle className="spin"/>}</div>;

  return <div className="app">
    <aside className="rail"><a className="brand-mark" href="/" title="천라지망"><img src="/logo-mark.png" alt="천라지망 로고"/></a><div className="rail-links">
      {[['ai',Radar,'AI 수색 계획'],['zones',Map,'수색 지도'],['tracks',Route,'수색 기록'],['members',Users,'참여자'],['ledger',BookOpen,'근거 장부'],['history',History,'버전 기록']].map(([key,Icon,label])=><button key={key} aria-label={label} title={label} className={tab===key?'rail-item active':'rail-item'} onClick={()=>{setTab(key);setSidebar(true);}}><Icon size={22}/>{key==='tracks'&&waiting>0&&<span className="badge-dot"/>}</button>)}
    </div><div className="rail-bottom"><button className="rail-item" title="데이터와 가정" aria-label="데이터와 가정" onClick={()=>setModal({type:'sources'})}><CircleHelp size={22}/></button></div></aside>
    <div className="shell">
      <header className="header"><div className="wordmark"><img src="/logo-mark.png" alt=""/>천라지망<span>수색 의사결정 장부</span></div><div className="header-right">{meta.walking_lab_enabled!==false&&<a className="walking-lab-link" href="/?view=walking-lab">보행 학습 결과 ↗</a>}<span className="training-chip"><Flask/>모의 수색</span></div></header>
      <section className="mission-header"><div><div className="breadcrumb">워크스페이스 <ChevronRight size={12}/> 제주특별자치도 <ChevronRight size={12}/> {nearestRegion(state.params.lon,state.params.lat)}</div><div className="title-row"><h1>{state.name}</h1><span className="version-pill">v{state.version}</span>{state.status?.status==='PAUSED'&&<span className="status-pill paused"><PauseCircle size={13}/> 일괄 중지</span>}{state.status?.status==='HANDED_OVER'&&<span className="status-pill handed"><ShieldCheck size={13}/> 기관 인계 완료</span>}</div><p><MapPin size={13}/> 마지막 확인 {state.params.lat.toFixed(5)}, {state.params.lon.toFixed(5)} <span className="divider">|</span><Clock3 size={13}/> 기준까지 {Number(state.params.hours.toFixed(2))}시간</p></div>
      <div className="mission-actions"><button className="button subtle" onClick={()=>setModal({type:'new'})}><Plus size={17}/> 새 모의 수색</button><button className="button primary" onClick={()=>setModal({type:'export'})}><ArrowDownToLine size={17}/> 인계 패키지</button></div></section>
      <div className="workspace">
        {sidebar&&<aside className="side-panel">
          <div className="side-heading"><div><span className="eyebrow">MISSION CONTROL</span><h2>{({ai:'AI와 수색을 계획하세요',zones:'다음 수색을 계획하세요',tracks:'수색의 흔적을 확인하세요',members:'참여자를 관리하세요',ledger:'판단의 근거를 남기세요',history:'결정의 변화를 살펴보세요'})[tab]}</h2></div><IconButton label="패널 접기" onClick={()=>setSidebar(false)}><PanelLeftClose size={18}/></IconButton></div>
          <div className="side-tabs">{[['ai','AI 계획'],['zones','우선 구역'],['tracks','수색 기록'],['members','참여자'],['ledger','근거 장부']].map(([k,l])=><button className={tab===k?'selected':''} key={k} onClick={()=>setTab(k)}>{l}{k==='tracks'&&waiting>0&&<span>{waiting}</span>}</button>)}</div>
          <div className="side-content">
          {tab==='ai'&&<AIPlanPanel state={savedState} bounds={meta.bounds} projection={projection} mode={timelineMode} plan={aiPlan} onPlan={setAIPlan} onSelect={setSelected} onRefresh={()=>refresh()} onOpenVersion={v=>refresh(active,v)}/>}
          {tab==='zones'&&<>
            <FacilityPanel params={state.params} metadata={facilityData?.metadata} features={facilityData?.features} onCompare={()=>{setForm({name:state.name+' (시설 비교)',autoName:false,lon:state.params.lon,lat:state.params.lat,hours:state.params.hours,sigma:state.params.sigma,seed:state.params.seed,facility_influence:!state.params.facility_influence,facility_strength:.6,distance_model:state.params.distance_model||'learned_lognormal',row_share:state.params.row_share??.1,subject_brief:state.params.subject_brief||'',...copyTime(state.params)});setModal({type:'new'});}}/>
            <div className="section-label"><span><Navigation size={14}/> 검토할 우선 구역</span><span>상위 {Math.min(8,top.length)}곳 <InfoTip label="우선 구역 설명"><p>거리, 지형, 행동 세 시나리오에서 공통으로 우선하는 후보입니다. 순위는 세 시나리오 확률의 기하평균이며, 합의도는 최소값과 최대값의 비율입니다.</p><p>일반팀 접근 전 현장 확인이 필요합니다. 수역과 급경사 구역은 일반팀 추천에서 제외됩니다.</p></InfoTip></span></div>
            <label className="search-box"><Search size={15}/><input aria-label="구역 검색" placeholder="구역 번호 검색" value={search} onChange={e=>setSearch(e.target.value)}/></label>
            <div className="zone-list">{top.filter(f=>f.properties.label.includes(search)).slice(0,8).map((f,i)=>{
              const p=f.properties;return <button className={'zone-card '+(selected===p.id?'chosen':'')} key={p.id} onClick={()=>setSelected(p.id)}>
                <div className="zone-card-top"><span className={'rank rank-'+i}>{p.rank.toString().padStart(2,'0')}</span><strong>구역 {p.label}</strong>{p.decision==='APPROVED'?<CheckCheck size={16} className="teal"/>:<ArrowUpRight size={16}/>}</div>
                {p.previous_rank&&p.previous_rank!==p.rank&&<div className="rank-delta">{projection?'기준 v'+state.version:'v'+(state.version-1)} 대비 {p.previous_rank}위 → {p.rank}위</div>}<div className="zone-meta"><span>{p.has_track?'수색 기록 있음':'수색 기록 없음'}</span>{p.agreement!==undefined&&<span>합의도 {Math.round(p.agreement*100)}%</span>}</div>
                <div className="scenario-bars">{p.scenario.map((v,j)=><span key={j} style={{width:(15+Math.min(v/Math.max(...p.scenario),1)*85)+'%'}}/>)}</div>
                <div className="zone-card-bottom"><span>시나리오별 가능성</span><strong>{formatPct(Math.min(...p.scenario))} – {formatPct(Math.max(...p.scenario))}</strong></div>
              </button>;
            })}</div>
          </>}
          {tab==='tracks'&&<>
            <input ref={uploadRef} type="file" accept=".gpx" hidden onChange={e=>{upload(e.target.files[0]);e.target.value='';}}/>
            <div className="section-label"><span><Users size={14}/> 팀 구성</span><span>밴드 폭 {(team.team_size-1)*team.spacing_m} m <InfoTip label="팀 구성 설명"><p>기록한 한 사람 옆에 {team.team_size-1}명이 {team.spacing_m} m 간격으로 나란히 걸었다고 가정합니다. 밴드 폭은 (인원−1)×간격이며, 가정이지 실측이 아닙니다.</p><p>업로드만으로 지도는 바뀌지 않습니다. 미발견을 확정한 기록만 확률을 갱신합니다.</p></InfoTip></span></div>
            <div className="team-inputs"><label>팀 인원<input aria-label="팀 인원" type="number" min="1" max={meta.team_limits?.max_team_size||12} value={team.team_size} onChange={e=>setTeam({...team,team_size:Number(e.target.value)})}/></label><label>대원 간격 (m)<input aria-label="대원 간격" type="number" min={meta.team_limits?.min_spacing_m||3} max={meta.team_limits?.max_spacing_m||60} value={team.spacing_m} onChange={e=>setTeam({...team,spacing_m:Number(e.target.value)})}/></label></div>
            <button className="upload-box" disabled={!!busy||state.status?.status==='HANDED_OVER'} onClick={()=>uploadRef.current.click()}><div className="upload-symbol"><Upload size={23}/></div><strong>GPX 수색 기록 올리기</strong><span>시간 정보가 있는 .gpx, 최대 2 MB</span></button>
            <button className="button demo-button" disabled={!!busy||state.status?.status==='HANDED_OVER'} onClick={addDemo}><Play size={15}/> 합성 훈련 트랙으로 체험</button>
            <div className="section-label"><span>등록된 수색 기록</span><span>{state.tracks.length}건</span></div>
            {!state.tracks.length&&<div className="empty-state"><Route size={30}/><strong>아직 수색 기록이 없습니다</strong><p>기록을 올리면 탐지 흔적과 빈틈을<br/>지도에서 확인할 수 있습니다.</p></div>}
            {state.tracks.map(t=><div className={'track-card '+(previewTrack===t.id?'chosen':'')} key={t.id}>
              <div className="track-title"><Route size={18}/><strong>{t.name}</strong></div><span className={'status-chip '+t.status}>{({UPLOADED:'결과 확정 대기',APPLIED:'미발견 반영 완료',REJECTED:'반영 제외'})[t.status]}</span>
              <div className="track-stats"><div><span>유효 이동 거리</span><strong>{(t.summary.distance_m/1000).toFixed(2)}<small> km</small></strong></div><div><span>위치 공백</span><strong>{t.summary.gaps}<small>구간</small></strong></div></div>
              <p className="muted small-text meta-line"><span>비정상 이동 제외 {t.summary.excluded_segments}구간</span>{t.summary.team&&<span>팀 {t.summary.team.team_size}명, 간격 {t.summary.team.spacing_m} m</span>}{t.summary.submitted_by&&<span>봉사자 제출</span>}</p><div className="track-buttons"><button className="text-button" onClick={()=>{setPreviewTrack(t.id);setLayer('shadow');setPhase('post');}}>탐지 흔적 보기 <ArrowUpRight size={13}/></button></div>
              {t.status==='UPLOADED'&&<button className="button primary full" disabled={!!busy||!isLatest||timelineMode!=='saved'||state.status?.status==='HANDED_OVER'} onClick={()=>setModal({type:'approve',track:t})}><Check size={16}/> 미발견 확정</button>}
              {t.status==='UPLOADED'&&<button className="text-button muted centered" disabled={!!busy} onClick={()=>run('기록 제외 중',async()=>{await post('/missions/'+active+'/tracks/'+t.id+'/reject');await refresh();setPreviewTrack(null);})}>이 기록 반영 제외</button>}
            </div>)}
          </>}
          {tab==='members'&&<MembersPanel state={savedState} onRefresh={()=>refresh()} onNotice={setNotice} onError={setError} busy={!!busy}/>}
          {tab==='ledger'&&<>
            <div className="section-label"><span><BookOpen size={14}/> 근거 기록</span><span><InfoTip label="근거 장부 설명"><p>단서와 판단의 출처를 남깁니다. 기록한 내용은 확률지도에 자동 반영되지 않습니다.</p></InfoTip></span></div>
            <form className="ledger-form" onSubmit={e=>{e.preventDefault();run('근거 저장 중',async()=>{await post('/missions/'+active+'/ledger',{note,source:noteSource,observed_at:noteTime?new Date(noteTime).toISOString():''});setNote('');await refresh();setNotice('근거를 장부에 저장했습니다.');});}}>
              <label>출처<input required value={noteSource} maxLength={100} onChange={e=>setNoteSource(e.target.value)} placeholder="예: 훈련 조정자"/></label><label>관찰 시각 (선택)<input type="datetime-local" value={noteTime} onChange={e=>setNoteTime(e.target.value)}/></label><label>관찰 내용<textarea required minLength={2} maxLength={1000} value={note} onChange={e=>setNote(e.target.value)} placeholder="직접 확인한 사실과 미확인 정보를 구분해 적어 주세요." rows={4}/></label><button className="button primary full" disabled={!!busy}><Plus size={16}/> 근거 기록</button>
            </form>
            <div className="section-label"><span>기록된 근거</span><span>{state.ledger.length}건</span></div>
            {state.ledger.map(l=><article className="ledger-card" key={l.id}><div><span className="tiny-tag">{l.kind==='DECISION'?'결정 기록':'단서 기록'}</span><time>{when(l.created_at)}</time></div><p>{l.note}</p><small>출처 {l.source}, 관찰 {when(l.observed_at)}</small></article>)}
            {!state.ledger.length&&<div className="empty-state"><BookOpen size={28}/><p>등록된 근거가 없습니다.</p></div>}
          </>}
          {tab==='history'&&<>
            <div className="section-label"><span><History size={14}/> 지도 버전</span><span>{state.versions.length}개 <InfoTip label="버전 기록 설명"><p>지도는 선택한 과거 버전이며, 근거 장부와 후보 승인 상태는 최신 상태입니다. 각 지도는 입력, 가정, 승인된 수색 기록과 연결되어 있습니다.</p></InfoTip></span></div>
            <div className="history-list">{state.versions.map(v=><button key={v.number} className={'history-item '+(v.number===state.version?'chosen':'')} onClick={()=>run('지도 불러오는 중',async()=>{await refresh(active,v.number);setPreviewTrack(null);})}><div className="history-icon">{v.number===0?<Layers size={18}/>:<FileCheck2 size={18}/>}</div><div><strong>v{v.number}<em>{v.number===0?'이동 시나리오 계산':'미발견 결과 반영'}</em></strong><time>{when(v.created_at)}</time><span className="hash-mini">{v.hash.slice(0,20)}…</span></div></button>)}</div>
            <button className="button subtle full" onClick={()=>setModal({type:'receipt'})}><Fingerprint size={17}/> 현재 버전 영수증</button>
          </>}
          </div>
          <div className="side-foot"><span className="data-ready"><span/> 실제 지형 데이터</span><button onClick={()=>setModal({type:'sources'})}>모델 가정 확인 <ArrowUpRight size={12}/></button></div>
        </aside>}
        <main className="map-workspace">
          <div className="map-toolbar"><div className="layer-tabs">{!sidebar&&<IconButton label="패널 펼치기" onClick={()=>setSidebar(true)}><PanelLeftOpen size={18}/></IconButton>}<span className="map-toolbar-label"><Layers size={15}/> 시나리오</span>{[['combined','종합'],['a','거리'],['b','지형'],['b2','행동'],['consensus','비교'],['shadow','수색 흔적']].map(([k,n])=><button key={k} className={layer===k?'selected':''} onClick={()=>{setLayer(k);setShowAnalysis(true);}}>{n}</button>)}</div><div className="map-view-actions"><div className="basemap-switch" role="group" aria-label="배경지도 종류"><button type="button" aria-pressed={basemap==='vector'} onClick={()=>setBasemap('vector')}>일반 지도</button>{meta.satellite_enabled!==false&&<button type="button" aria-pressed={basemap==='satellite'} onClick={()=>{setBasemap('satellite');setSatelliteAttempt(n=>n+1);}}>위성사진</button>}</div><button className={'map-only-toggle '+(!showAnalysis?'active':'')} onClick={()=>setShowAnalysis(v=>!v)} aria-pressed={!showAnalysis}>{showAnalysis?'지도만 보기':'수색 레이어 켜기'}</button><IconButton label="지도 표시 설정" onClick={()=>setModal({type:'layers'})}><SlidersHorizontal size={17}/></IconButton></div></div>
          <LiveForecastPanel daylight={state.daylight} mission={state.id} version={state.version} mode={timelineMode} forecast={projection} onForecastChange={setForecast} onModeChange={mode=>{setTimelineMode(mode);setForecast(null);setPhase('post');setShowAnalysis(true);}}/>
          {basemap==='satellite'&&<div className="satellite-notice" role="status">
            {satelliteStatus==='error'?<span>위성사진 연결에 실패해 {basemapStatus==='fallback'?'분석':'일반'} 지도로 돌아왔습니다. <button onClick={()=>setSatelliteAttempt(n=>n+1)}>다시 시도</button></span>:<span>{satelliteStatus==='loading'?'위성사진 불러오는 중. ':''}2025년 합성영상, 원본 약 10 m, 실시간 촬영 아님</span>}
            <label><input type="checkbox" checked={showSatelliteLabels} onChange={e=>setShowSatelliteLabels(e.target.checked)}/> 도로와 지명</label>
          </div>}
          <div className={'map-area '+(basemap==='satellite'&&satelliteStatus!=='error'?'satellite-active':'')}>
            <MapCanvas meta={meta} state={state} aiPlan={tab==='ai'&&aiPlan?.mission===state.id&&aiPlan.base_version===state.version?aiPlan:null} layer={layer} phase={phase} selected={selected} onSelect={setSelected} picking={picking} onPick={({lng,lat})=>{setForm(f=>place(f,Number(lng.toFixed(6)),Number(lat.toFixed(6))));setPicking(false);setModal({type:'new'});}} previewTrack={previewTrack} showGrid={showGrid} opacity={opacity} showAnalysis={showAnalysis} onBasemapChange={setBasemapStatus} facilityData={facilityData} showFacilities={showFacilities} basemap={basemap} satelliteStatus={satelliteStatus} onSatelliteStatus={setSatelliteStatus} satelliteAttempt={satelliteAttempt} showSatelliteLabels={showSatelliteLabels}/>
            {basemapStatus==='fallback'&&!(basemap==='satellite'&&satelliteStatus==='ready')&&<div role="status" className="basemap-warning">온라인 지도를 불러오지 못해 저해상도 분석 지도를 표시합니다. 연결 후 새로고침해 주세요.</div>}<div className="map-topline"><span className="map-region"><MapPin size={14}/> 제주 {nearestRegion(state.params.lon,state.params.lat)} <span data-testid="basemap-status">{basemap==='satellite'&&satelliteStatus==='ready'?'위성사진 (Sentinel-2)':basemap==='satellite'&&satelliteStatus==='loading'?'위성사진 연결 중':basemapStatus==='vector'?'고해상도 벡터':basemapStatus==='loading'?'상세 지도 연결 중':'오프라인 분석 지도'}</span></span><span className="map-mode">{planMap?'AI 계획 기준 분포 (고정)':projection?'시간 이동 추정 / 저장 전':'TRAINING / 실제 사건 아님'}</span></div>
            {planMap&&showAnalysis&&<div className="ai-map-legend"><strong>{activePlan.distribution_mode==='ENDPOINT_REFERENCE'?'학습 발견점 분포 (시간 예측 아님)':'계획 시점의 이동 시나리오'}</strong><span className="gradient"/><small>낮음 → 높음 (상대 농도)</small><small>계획 기준 {new Date(activePlan.estimated_at).toLocaleString('ko-KR')}, 고정 지도</small><button onClick={()=>setAIPlan(null)}>계획 지도 닫기</button></div>}
            {planMap&&zone&&<div className="zone-detail"><div className="detail-title"><h3>구역 {zone.label}</h3><span className="detail-tools"><InfoTip label="구역 설명"><p>현장 검토 전 제안입니다. 배정은 왼쪽의 계획 확정에서 처리합니다.</p></InfoTip><IconButton label="구역 상세 닫기" onClick={()=>setSelected(null)}><X size={18}/></IconButton></span></div><p>{activePlan.distribution_mode==='ENDPOINT_REFERENCE'?'발견점 참고 질량':'계획 기준 존재 가능성'} {formatPct(zone.poa)}</p></div>}
            {picking&&<div className="picking-banner"><Crosshair size={17}/> 지도를 눌러 마지막 확인 위치를 선택하세요<button onClick={()=>setPicking(false)}>취소</button></div>}
            {previewTrack&&<div className="preview-banner"><Route size={16}/>{state.tracks.some(t=>t.id===previewTrack&&t.status==='APPLIED'&&t.applied_version<=state.version)?'승인된 수색 흔적 (지도에 반영됨)':'수색 흔적 미리보기 (지도 미반영)'}<IconButton label="미리보기 닫기" onClick={()=>setPreviewTrack(null)}><X size={15}/></IconButton></div>}
            {zone&&!planMap&&<div className="zone-detail"><div className="detail-title"><div><span className="eyebrow">SEARCH SECTOR</span><h3>구역 {zone.label}</h3></div><span className="detail-tools"><InfoTip label="구역 설명"><p>{zone.access==='AGENCY_ONLY'?'수역이나 급경사가 포함된 구역입니다. 전문기관 검토가 필요합니다.':'일반팀 후보입니다. 실제 접근 가능 여부는 현장에서 확인해야 합니다.'}</p><p>탐지확률 범위는 가정 변화에 따른 범위이며 신뢰구간이 아닙니다. 낮은 확률은 부재의 증거가 아닙니다.</p></InfoTip><IconButton label="구역 상세 닫기" onClick={()=>setSelected(null)}><X size={18}/></IconButton></span></div><div className="detail-pair"><span>{projection?'시간 추정 존재 가능성':'종합 존재 가능성'}</span><strong>{formatPct(zone.poa)}</strong></div>{zone.speed_kmh!==undefined&&<div className="detail-pair"><span>이 구역 도보 모형 속도</span><strong>{zone.speed_kmh.toFixed(2)} km/h</strong></div>}<div className="detail-pair"><span>탐지확률 가정 범위</span><strong>{formatPct(zone.pod_low)}–{formatPct(zone.pod_high)}</strong></div><div className="mini-models">{zone.scenario.map((p,i)=><div key={i}><span>{['거리','지형','행동'][i]}</span><i style={{height:(8+42*p/Math.max(...zone.scenario,1e-12))+'px'}}/><strong>{formatPct(p)}</strong></div>)}</div>{zone.access==='AGENCY_ONLY'&&<p className="detail-note">수역이나 급경사 포함, 전문기관 검토 필요</p>}<button className="button primary full" disabled={busy||timelineMode!=='saved'||zone.access==='AGENCY_ONLY'||zone.decision==='APPROVED'||!isLatest} onClick={()=>run('구역 승인 중',async()=>{await post('/missions/'+active+'/zones/'+zone.id+'/approve');await refresh();setNotice('후보 구역 승인을 근거 장부에 남겼습니다.');})}>{zone.decision==='APPROVED'?<><CheckCheck size={16}/> 후보 승인됨</>:<>수색 후보 승인 <ArrowRight size={16}/></>}</button></div>}
            <div className={'map-legend '+(!showAnalysis||planMap?'analysis-hidden':'')}>{layer==='consensus'?<div><span className="legend-heading">각 시나리오 상위 50% 질량 영역</span><div className="consensus-keys"><span>● 세 시나리오 공통</span><span>● 가설 의존</span></div></div>:<div><span className="legend-heading">{layer==='shadow'?'탐지 흔적':layer==='consensus'?'시나리오 비교':'상대적 존재 가능성'}</span><span className={'gradient '+(layer==='shadow'?'green':'')}/><div className="legend-range"><span>{layer==='shadow'?'낮음':'낮음'}</span><span>높음</span></div></div>}<div className="legend-key"><i className="teal-key"/> 수색 흔적<i className="outline-key"/> 우선 구역</div></div>
            <div className={'compare-switch '+(!showAnalysis||planMap?'analysis-hidden':'')}><button className={phase==='prior'?'active':''} onClick={()=>{setTimelineMode('saved');setForecast(null);setPhase('prior');}}>수색 전</button><button className={phase==='post'?'active':''} onClick={()=>{setTimelineMode('saved');setForecast(null);setPhase('post');}}>현재 v{state.version}</button></div>
            <div className="attribution">{basemap==='satellite'&&satelliteStatus!=='error'&&<span className="satellite-credit"><a href="https://cloudless.eox.at/" target="_blank" rel="noreferrer">EOxCloudless</a> by <a href="https://eox.at/" target="_blank" rel="noreferrer">EOX IT Services GmbH</a> (Contains modified Copernicus Sentinel data 2025), <a href="https://maps.eox.at/" target="_blank" rel="noreferrer">EOX::Maps</a>, <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/" target="_blank" rel="noreferrer">CC BY-NC-SA 4.0</a><br/></span>}<a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a>, <a href="https://openmaptiles.org/" target="_blank" rel="noreferrer">© OpenMapTiles</a>, <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a></div>
          </div>
          <div className="summary-strip"><div><span><CircleDot size={15}/> 지도 밖 잔여확률</span><strong>{formatPct(state.outside_mixed)}<small>{planMap?(activePlan.distribution_mode==='ENDPOINT_REFERENCE'?'학습 발견점 참고':'계획 기준 시나리오'):'ROW '+Math.round((state.params.row_share??0)*100)+'% 포함, 미발견마다 증가'}</small></strong></div><div><span><Route size={15}/> 수색 기록 영역</span><strong>{state.coverage_km2.toFixed(3)} <small>km² (가정)</small></strong></div><div><span><FileCheck2 size={15}/> 확정된 수색 기록</span><strong>{state.tracks.filter(t=>t.status==='APPLIED'&&t.applied_version<=state.version).length}<small>건 / 등록 {state.tracks.length}건</small></strong></div><button onClick={()=>setModal({type:'receipt'})}><Fingerprint size={19}/><span>지도 버전마다<br/><b>근거가 남습니다</b></span><ArrowUpRight size={17}/></button></div>
          <div className="safety-footer"><Info size={13}/><span>판단 보조용 훈련 도구입니다. 낮은 확률은 부재의 증거가 아닙니다.</span><span className="engine-version">연구용 모델</span></div>
        </main>
      </div>
    </div>
    {busy&&<div className="toast busy"><LoaderCircle size={18} className="spin"/>{busy}</div>}
    {notice&&!busy&&<div role="status" className="toast"><Check size={18}/>{notice}</div>}
    {error&&<div role="alert" className="toast error"><AlertTriangle size={18}/><span>{error}</span><IconButton label="오류 닫기" onClick={()=>setError('')}><X size={16}/></IconButton></div>}
    {modal?.type==='new'&&<Modal title="새 모의 수색 시작" onClose={()=>setModal(null)}><form onSubmit={e=>{e.preventDefault();run('세 시나리오 계산 중',async()=>{const result=await post('/missions',missionPayload(form));setActive(result.id);await refresh(result.id);setMissions(await api('/missions'));setSelected(null);setPreviewTrack(null);setLayer('combined');setTab('zones');setModal(null);setNotice('세 이동 시나리오를 실제 계산해 새 임무를 만들었습니다.');});}} className="mission-form"><label>임무 이름<input required maxLength={80} value={form.name} onChange={e=>setForm({...form,name:e.target.value,autoName:false})}/></label><div className="form-grid"><label>위도<input type="number" step="0.000001" min={meta.bounds?.lat?.[0]??33.0} max={meta.bounds?.lat?.[1]??33.67} required value={form.lat} onChange={e=>setForm(f=>place(f,f.lon,Number(e.target.value)))}/></label><label>경도<input type="number" step="0.000001" min={meta.bounds?.lon?.[0]??126.02} max={meta.bounds?.lon?.[1]??127.08} required value={form.lon} onChange={e=>setForm(f=>place(f,Number(e.target.value),f.lat))}/></label></div><button type="button" className="button subtle full" onClick={()=>{setModal(null);setPicking(true);}}><Crosshair size={16}/> 지도에서 위치 선택</button><div className="form-grid"><label>{form.daylight_enabled?'시각 입력으로 경과시간 계산':'경과 시간 (시간)'}<input disabled={!!form.daylight_enabled} type="number" min=".25" max="8" step=".25" required value={form.daylight_enabled?elapsedHours(form):form.hours} onChange={e=>setForm({...form,hours:Number(e.target.value)})}/></label><label>위치 불확실성 (m)<input type="number" min="5" max="500" required value={form.sigma} onChange={e=>setForm({...form,sigma:Number(e.target.value)})}/></label></div><div className="tip-host"><label>거리 시나리오(A)의 근거<select aria-label="거리 시나리오 모델" value={form.distance_model} onChange={e=>setForm({...form,distance_model:e.target.value})}><option value="learned_lognormal">학습 모델 (실제 실종 사건 65건, 기본)</option><option value="assumed_sqrt_time">팀 가정 (700√시간 m Gaussian)</option></select></label><InfoTip corner className="field" label="거리 시나리오 설명"><p><strong>학습 모델</strong>: 실제 사건에서 학습한 발견 거리 분포를 경과시간 안에 걸어서 닿을 수 있는 반경(5 km/h 가정)으로 잘라 씁니다. 미국 하이커 표본이라 근거리는 지형 시나리오가 보완합니다.</p><p><strong>팀 가정</strong>: 검증되지 않은 확산 가정입니다. 학습 모델과 비교용으로만 권장합니다.</p></InfoTip></div><div className="tip-host"><label>모형 밖 잔여확률 ROW <strong>{Math.round(form.row_share*100)}%</strong><input type="range" min="0" max="0.5" step="0.05" value={form.row_share} onChange={e=>setForm({...form,row_share:Number(e.target.value)})}/></label><InfoTip corner className="field" label="ROW 설명"><p>차량이나 교통 이동, 잘못된 마지막 확인 위치처럼 지도가 표현할 수 없는 가능성을 미리 남겨 둡니다. 수색이 실패할수록 자동으로 커집니다.</p></InfoTip></div><label>봉사자용 인상착의 요약 (선택, 가상 정보만)<input maxLength={200} value={form.subject_brief} onChange={e=>setForm({...form,subject_brief:e.target.value})} placeholder="예: 훈련용 가상 대상, 파란 재킷"/></label><TimeControls form={form} setForm={setForm}/><FacilityControls form={form} setForm={setForm} metadata={meta.facilities}/><div className="info-card"><Info size={16}/><p>훈련용 가정입니다. 실제 실종자 개인정보를 입력하지 마세요.</p></div><button className="button primary full" disabled={!!busy}><Layers size={17}/> 시나리오 계산 및 시작</button></form>{missions.length>1&&<div className="existing-missions"><span>이전 임무 열기</span><select aria-label="이전 임무" value={active} onChange={e=>run('이전 임무 불러오는 중',async()=>{setActive(e.target.value);await refresh(e.target.value);setModal(null);setSelected(null);setPreviewTrack(null);})}>{missions.map(m=><option value={m.id} key={m.id}>{m.name} ({when(m.created_at)})</option>)}</select></div>}</Modal>}
    {modal?.type==='approve'&&<Modal title="수색 결과를 확정할까요?" onClose={()=>setModal(null)}><div className="approval-icon"><FileCheck2 size={28}/></div><h3>{modal.track.name}</h3><p>이 기록의 수색이 완료되었고 대상자를 발견하지 못했음을 확인합니다. 확정하면 v{state.version+1}을 저장합니다.</p><div className="modal-actions"><button className="button subtle" onClick={()=>setModal(null)}>취소</button><button className="button primary" disabled={!!busy} onClick={()=>approveTrack(modal.track)}><Check size={17}/> 미발견 확정</button></div></Modal>}
    {modal?.type==='receipt'&&<Modal title={'재현 영수증 v'+state.version} onClose={()=>setModal(null)}><div className="receipt-status"><ShieldCheck size={20}/> 입력, 지형, 가정, 이전 버전 연결</div><div className="receipt-grid"><span>계산 엔진</span><strong>{state.receipt.engine}</strong><span>실행 seed</span><strong>{state.params.seed}</strong><span>최초 계산 시간</span><strong>{state.params.compute_seconds}초 (실측)</strong><span>입자 수</span><strong>B 20,000 / B′ 20,000</strong><span>지형 해상도</span><strong>30 m, 512 × 512</strong><span>거리 시나리오</span><strong>{state.params.distance_model_used?.used==='learned_lognormal'?'학습 모델 ('+state.params.distance_model_used.cases+'건)':'팀 가정 700√h'}</strong><span>ROW 초기값</span><strong>{Math.round((state.params.row_share??0)*100)}%</strong></div><label className="hash-label">현재 버전 SHA-256<code>{state.receipt.hash}</code></label><label className="hash-label">이전 버전<code>{state.receipt.parent_hash||'첫 번째 버전'}</code></label><p className="muted">해시는 파일 변경 여부를 확인합니다. 관측 사실의 진실성이나 기관 승인을 증명하지 않습니다.</p><button className="button primary full" onClick={()=>setModal({type:'export'})}><ArrowDownToLine size={16}/> 영수증 포함 패키지 받기</button></Modal>}
    {modal?.type==='export'&&<Modal title="수색 기록 인계" onClose={()=>setModal(null)}><p className="muted">최신 버전 v{state.current_version}의 지도, 구역 통계, 근거, 계산 기록을 묶습니다.</p><div className="export-list"><FileText/><div><strong>상황보고서</strong><span>읽기 쉬운 보고서, 브라우저에서 PDF 저장</span></div><button className="button subtle" onClick={()=>window.open(API+'/missions/'+active+'/report','_blank','noopener')}>열기 <ArrowUpRight size={14}/></button></div><div className="export-list"><FileCheck2/><div><strong>재현 패키지</strong><span>GeoJSON, CSV, 계산 배열, 영수증, 장부</span></div><a className="button primary" href={API+'/missions/'+active+'/package.zip'} download><ArrowDownToLine size={16}/> ZIP</a></div><div className="info-card"><ShieldCheck size={17}/><p>원본 GPS 트랙은 기본 패키지에 포함하지 않습니다. 재현 패키지는 내부 무결성을 확인할 수 있습니다.</p></div><input ref={verifyRef} type="file" hidden accept=".zip" onChange={e=>{const file=e.target.files[0];if(file)run('패키지 확인 중',async()=>{const body=new FormData();body.append('file',file);setVerification(await api('/verify',{method:'POST',body}));});e.target.value='';}}/><button className="button subtle full" disabled={!!busy} onClick={()=>verifyRef.current.click()}><Fingerprint size={17}/> 받은 패키지 무결성 확인</button>{verification&&<p role="status" className={verification.valid?'verification good':'verification bad'}>{verification.valid?'✓ '+verification.files+'개 파일의 해시가 일치합니다.':'변경된 파일: '+verification.failed.join(', ')}</p>}</Modal>}
    {modal?.type==='sources'&&<Modal title="데이터와 모델의 근거" wide onClose={()=>setModal(null)}><div className="source-section"><span className="source-badge">실제 공개 데이터</span><h3>선명한 지도와 별도의 분석 격자</h3><p>표시용 지도는 OpenFreeMap 벡터 타일로 도로, 건물, 지명을 그립니다. 확대해도 선명하며 인터넷 연결이 필요합니다. 현재 화면 범위의 타일이 외부 지도 제공자에게 요청됩니다. 데이터에 등록되지 않은 건물이나 도로는 표시할 수 없습니다. 아래의 수색 계산 데이터는 별도의 30 m 격자입니다. 지도 표시 해상도가 계산 정확도를 높이는 것은 아닙니다.</p><p>위성사진은 EOX의 2025년 Sentinel-2 합성영상입니다. 원본 해상도 약 10 m이며 실시간 촬영이나 개별 건물 수준의 항공사진이 아닙니다. 화면에 필요한 타일만 요청하며 이미지 분석이나 AI 학습에는 사용하지 않습니다. 교육 및 비상업 이용 조건과 출처를 표시합니다. 상용 전환 시 별도 이용 조건 확인이 필요합니다. 연결 실패 시 기존 지도로 돌아갑니다. <a href="https://cloudless.eox.at/documentation/license" target="_blank" rel="noreferrer">영상 이용 조건</a></p><p>Copernicus GLO-30 표고와 ESA WorldCover 2021 토지피복을 제주도 전체({meta.nx}×{meta.ny}셀, 30 m)로 변환했고, OpenStreetMap 도로와 탐방로 {meta.road_count.toLocaleString()}개를 반영했습니다. 임무마다 마지막 확인 위치 주변 {(meta.window?.size_m/1000||15.36).toFixed(2)} km × {(meta.window?.size_m/1000||15.36).toFixed(2)} km 창(512×512셀)을 잘라 계산하며, 창 밖은 지도 밖 잔여확률로 셉니다.</p><p className="muted">수집: {when(meta.prepared_at)}. DSM에는 식생과 건물 높이가 포함됩니다.</p>{meta.sources.map(s=><a key={s.name} href={s.url} target="_blank" rel="noreferrer">{s.name==='dem'?'Copernicus DEM 원본':s.name==='roads'?'OpenStreetMap 출처':'WorldCover 원본'} <ArrowUpRight size={13}/></a>)}</div><div className="source-section"><span className="source-badge amber">검증 전 팀 가정</span><h3>이동 가설과 탐지확률</h3><p>거리 시나리오는 반경 분포, 지형 시나리오는 표고와 토지피복 이동비용을 사용합니다. 행동 시나리오는 경로 추종과 내리막 선호를 더한 변형입니다. 시설 가정을 켠 임무에서는 실제 OSM 시설까지의 지형 이동비용을 사용해 방향 선호를 추가합니다. 시설은 편의점, 식당, 음수대, 정류장, 숙박, 의료, 공공 도움 지점입니다. 영향 강도는 미검증 팀 가정이며 영업 여부, 출입, 탑승, 체류는 계산하지 않습니다. 해당 시설 데이터 버전을 영수증에 고정합니다.</p><ul><li>실시간 추정은 저장된 사후분포를 지형 이동규칙으로 전파합니다. 15초 간격이며 실제 위치, 속도, 교통 관측이 아닙니다. 과거 수색 강도를 다시 적용하지 않습니다.</li><li>거리 시나리오 A는 기본적으로 실제 실종 사건 65건에서 학습한 발견 거리 분포(도달 가능 반경으로 절단)를 씁니다. 표본이 1 km 미만 사건을 대부분 제외해 근거리는 지형 시나리오가 보완합니다.</li><li>지도 밖 잔여확률에는 임무 생성 시 정한 ROW 몫이 포함되며, 미발견 결과가 반영될수록 커집니다. 0에 가깝다는 것이 바깥에 없다는 증명은 아닙니다.</li><li>A : B : B′ 가중치 = ½ : ¼ : ¼</li><li>탐지폭 8–22 m, GPS 오차 가정 10 m</li><li>POD 범위는 가정 변화에 따른 범위이며 신뢰구간이 아닙니다.</li><li>순위는 세 시나리오 구역 확률의 기하평균(로그 선형 합의)입니다. 한 시나리오라도 0이면 후보에서 빠지며, 카드의 합의도는 최소/최대 비율입니다. 가정 100회 스트레스 검증은 아직 구현하지 않았습니다.</li><li>팀 인원과 간격 입력은 n명이 (n−1)×간격 폭으로 나란히 걷는다는 가정으로 탐지 노력을 n배로 늘리고 밴드로 분산합니다.</li><li>수색대 GPS 속도 모델은 별도 AI 계획에서 학습과 평가 결과를 확인할 수 있습니다. 실종자 시간별 행동, 표식 탐지율, 실제 수색 성능은 검증 전입니다.</li></ul></div><div className="source-section"><span className="source-badge">기록 원칙</span><p>승인된 미발견 기록만 반영합니다. 위치 공백과 비정상 속도는 탐지강도 계산에서 제외합니다. 단서는 장부에만 기록합니다.</p></div><p className="muted small-text">{plain(meta.attribution)}</p></Modal>}
    {modal?.type==='layers'&&<Modal title="지도 표시" onClose={()=>setModal(null)}><label className="toggle-row">주변 시설 위치<input type="checkbox" checked={showFacilities} onChange={e=>setShowFacilities(e.target.checked)}/></label><label className="toggle-row">수색 구역 경계<input type="checkbox" checked={showGrid} onChange={e=>setShowGrid(e.target.checked)}/></label><label className="range-label">확률지도 불투명도 <strong>{Math.round(opacity*100)}%</strong><input type="range" min=".1" max="1" step=".05" value={opacity} onChange={e=>setOpacity(Number(e.target.value))}/></label><p className="muted">색 농도는 상대적 확률입니다. 수색 전후에는 동일한 색 척도를 사용합니다.</p></Modal>}
  </div>;
}
function Flask(){return <svg width="13" height="15" viewBox="0 0 16 18" fill="none"><path d="M5 1h6M6 1v5l-4 8a2 2 0 002 3h8a2 2 0 002-3l-4-8V1M4 11h8" stroke="currentColor" strokeWidth="1.4"/></svg>}
const view=new URLSearchParams(window.location.search).get('view');
createRoot(document.getElementById('root')).render(view==='walking-lab'?<WalkingLab/>:view==='join'?<JoinView/>:<App/>);
