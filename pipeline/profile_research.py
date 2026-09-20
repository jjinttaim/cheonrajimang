"""Read-only source profiling and explicitly derived ML tables. No model fitting.

Run with the bundled workspace Python (pandas/numpy/openpyxl for XLSX reading).
"""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data/research"
OUT=DATA/"prepared"


def profile(frame):
    numbers=frame.select_dtypes(include="number")
    return {"rows":len(frame),"columns":list(frame.columns),
            "dtypes":{k:str(v) for k,v in frame.dtypes.items()},
            "nulls":{k:int(v) for k,v in frame.isna().sum().items()},
            "unique":{k:int(v) for k,v in frame.nunique(dropna=True).items()},
            "duplicate_rows":int(frame.duplicated().sum()),
            "numeric":json.loads(numbers.describe(percentiles=[.01,.25,.5,.75,.99]).to_json())}


def haversine(lat1,lon1,lat2,lon2):
    a,b,c,d=np.radians([lat1,lon1,lat2,lon2])
    q=np.sin((c-a)/2)**2+np.cos(a)*np.cos(c)*np.sin((d-b)/2)**2
    return 6371008.8*2*np.arcsin(np.sqrt(np.clip(q,0,1)))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    report={"sampling":"FULL_FILES","model_training_performed":False,"tables":{}}
    files=[p for folder in ("lost_hikers_2022","sar_searchers_2026") for p in (DATA/folder).glob("*.csv")]
    for p in files:
        df=pd.read_csv(p)
        report["tables"][str(p.relative_to(DATA))]=profile(df)
    # Real lost-person incident endpoint pairs, not reconstructed trajectories.
    src="lost_hikers_2022/41598_2022_9502_MOESM3_ESM.csv"
    endpoints=pd.read_csv(DATA/src)
    assert endpoints.incident_index.is_unique
    endpoints["straight_line_distance_m"]=haversine(endpoints.IPP_lat,endpoints.IPP_lon,endpoints.find_lat,endpoints.find_lon)
    endpoints["source_file"]=src
    endpoints["row_kind"]="REAL_INCIDENT_ENDPOINTS"
    endpoints.to_csv(OUT/"incident_endpoints.csv",index=False)
    report["incident_endpoints"]={
        "count":len(endpoints),"distance_m":endpoints.straight_line_distance_m.describe().to_dict(),
        "straight_line_under_1km":int((endpoints.straight_line_distance_m<1000).sum()),
        "missing_required_labels":["actual_trajectory","elapsed_time","incident_date","facility_visit","subject_demographics"],
        "split_group":"incident_index",
        "warning":"65 selected US hiker incidents. Simulation outputs and fitted profiles use found locations and are NOT independent labels or predictor inputs."
    }
    # Searcher movement. Retain flagged segments instead of silently deleting them.
    searchers=pd.read_csv(DATA/"sar_searchers_2026/SARsearchertracks.csv")
    searchers["source_row"]=np.arange(2,len(searchers)+2)
    grouped=searchers.groupby("track_id",sort=False)
    previous=grouped.shift()
    dt=searchers.time_sec-previous.time_sec
    distance=haversine(previous.latitude,previous.longitude,searchers.latitude,searchers.longitude)
    with np.errstate(divide="ignore",invalid="ignore"):
        speed=distance/dt
        grade=(searchers.elevation_m-previous.elevation_m)/distance
    first=grouped.cumcount()==0
    segments=searchers.loc[~first,["track_id","search_type","team_id","source_row"]].copy()
    segments["delta_seconds"]=dt[~first];segments["distance_m"]=distance[~first]
    segments["speed_mps"]=speed[~first];segments["grade"]=grade[~first]
    flags=np.select([dt<=0,dt>120,distance>250,speed>3,~np.isfinite(speed)],
                    ["NONINCREASING_TIME","GAP_OVER_120S","JUMP_OVER_250M","SPEED_OVER_3MPS","NONFINITE_SPEED"],default="CANDIDATE")
    segments["quality_flag"]=flags[~first]
    segments.replace([np.inf,-np.inf],np.nan).to_csv(OUT/"searcher_segments.csv",index=False)
    report["searcher_segments"]={"tracks":int(searchers.track_id.nunique()),
        "search_types":searchers.groupby("search_type").track_id.nunique().to_dict(),
        "segments":len(segments),"quality_flags":segments.quality_flag.value_counts().to_dict(),
        "grade_missing_or_nonfinite":int((~np.isfinite(segments.grade)).sum()),
        "speed_mps_quantiles":segments.speed_mps.replace([np.inf,-np.inf],np.nan).quantile([0,.01,.25,.5,.75,.99,1]).to_dict(),
        "relative_time_only":True,"duplicate_track_times":int(searchers.duplicated(["track_id","time_sec"]).sum()),
        "warning":"Not lost-person behavior or POD. Thresholds are QA assumptions, not research findings. No incident ID is provided; track-only splits can leak shared incident/team terrain."}
    # Workbook source stores POINT(latitude longitude), not normal WKT(x=lon,y=lat).
    # info.txt explicitly documents latitude then longitude. Do not swap blindly.
    xlsx=DATA/"cyprus_exercise_2022/traces.xlsx"
    sheets=pd.read_excel(xlsx,sheet_name=None)
    report["exercise_workbook"]={name:profile(df) for name,df in sheets.items()}
    exercise=sheets["Sheet0"].copy()
    xy=exercise.geom.str.extract(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)").astype(float)
    assert xy.notna().all().all()
    normalized=pd.DataFrame({
        "responder_id":exercise.user_team_mission_id,"latitude":xy[0],"longitude":xy[1],
        "local_timestamp":pd.to_datetime(exercise.timestamp).dt.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "timezone_status":"NOT_SPECIFIED_IN_WORKBOOK","source_row":np.arange(2,len(exercise)+2)})
    normalized.to_csv(OUT/"exercise_traces.csv",index=False)
    report["exercise"]={"observations":len(normalized),"responders":int(normalized.responder_id.nunique()),
        "date_min":normalized.local_timestamp.min(),"date_max":normalized.local_timestamp.max(),
        "duplicate_responder_times":int(normalized.duplicated(["responder_id","local_timestamp"]).sum()),
        "source_coordinate_order":"latitude longitude (per publisher info.txt)",
        "warning":"One exercise, not nine independent incidents. No UTC conversion without verified timezone."}
    prepared=[]
    for p in sorted(OUT.glob("*.csv")):
        prepared.append({"path":str(p.relative_to(DATA)),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size})
    report["derived_profiles"]={str(p.relative_to(DATA)):profile(pd.read_csv(p)) for p in sorted(OUT.glob("*.csv"))}
    report["derived_files"]=prepared
    (DATA/"quality_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({"incidents":len(endpoints),"searcher_tracks":int(searchers.track_id.nunique()),
                      "segments":len(segments),"flags":report["searcher_segments"]["quality_flags"],
                      "exercise_rows":len(normalized),"responders":int(normalized.responder_id.nunique())},ensure_ascii=False),flush=True)


if __name__=="__main__":main()
