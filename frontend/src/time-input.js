// All datetime-local fields are explicitly Korean time, regardless of browser zone.
export function koreanLocal(at=Date.now()) {
  return new Date(new Date(at).getTime()+9*3600000).toISOString().slice(0,16);
}
export function timeDefaults() {
  return {daylight_enabled:false,night_factor:.6,
    missing_local:koreanLocal(Date.now()-3*3600000),analysis_local:koreanLocal()};
}
export function elapsedHours(form) {
  const value=(new Date(form.analysis_local+':00+09:00')-new Date(form.missing_local+':00+09:00'))/3600000;
  return Number.isFinite(value)?Number(value.toFixed(2)):'';
}
export function missionPayload(form) {
  const {missing_local,analysis_local,autoName,...rest}=form;
  if(!form.daylight_enabled)return {...rest,missing_at:null,analysis_at:null};
  return {...rest,missing_at:missing_local+':00+09:00',analysis_at:analysis_local+':00+09:00'};
}
export function copyTime(params) {
  return {...timeDefaults(),daylight_enabled:!!params.daylight_enabled,night_factor:params.night_factor??.6,
    ...(params.missing_at?{missing_local:koreanLocal(params.missing_at),analysis_local:koreanLocal(params.analysis_at)}:{})};
}
