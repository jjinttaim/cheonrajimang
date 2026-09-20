"""Approximate solar light and an explicitly UNCALIBRATED mobility scenario.

Solar equations: https://gml.noaa.gov/grad/solcalc/solareqns.PDF
No weather, artificial illumination, sleep, visibility or POD is inferred.
Use the mission's fixed reference position over the 15 km training region.
"""
import calendar
from datetime import datetime, timedelta, timezone
import numpy as np

MODEL = "solar-mobility-assumption-v1"
SOURCE = "https://gml.noaa.gov/grad/solcalc/solareqns.PDF"
STEP = 60.0


def aware(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("날짜와 시각에는 시간대가 필요합니다.")
    return dt.astimezone(timezone.utc)


def elevation(at, lat, lon):
    """Geometric solar centre altitude, degrees; UTC input, no refraction."""
    dt = aware(at)
    hour = dt.hour + dt.minute/60 + (dt.second+dt.microsecond/1e6)/3600
    gamma = 2*np.pi/(366 if calendar.isleap(dt.year) else 365)*(dt.timetuple().tm_yday-1+(hour-12)/24)
    eq = 229.18*(.000075+.001868*np.cos(gamma)-.032077*np.sin(gamma)
                 -.014615*np.cos(2*gamma)-.040849*np.sin(2*gamma))
    dec = (.006918-.399912*np.cos(gamma)+.070257*np.sin(gamma)-.006758*np.cos(2*gamma)
           +.000907*np.sin(2*gamma)-.002697*np.cos(3*gamma)+.00148*np.sin(3*gamma))
    angle = np.radians(((hour*60+eq+4*lon) % 1440)/4-180)
    lat = np.radians(lat)
    return float(np.degrees(np.arcsin(np.clip(np.sin(lat)*np.sin(dec)+np.cos(lat)*np.cos(dec)*np.cos(angle),-1,1))))


def phase(altitude):
    return "DAY" if altitude >= 0 else "TWILIGHT" if altitude > -6 else "NIGHT"


class SolarClock:
    """Integrate a light multiplier in real time, including dawn/dusk mid-edge.

    Rates are held constant in fixed one-minute bins, sampled at midpoints.
    The time grid never depends on the requested forecast length, so extending
    a forecast does not change its earlier path. Values are assumptions, not ML.
    """
    def __init__(self, start_at, seconds, lat, lon, night_factor=.6):
        if not np.isfinite(night_factor) or not .3 <= night_factor <= 1:
            raise ValueError("야간 이동계수는 0.3–1.0 범위여야 합니다.")
        self.start = aware(start_at)
        self.lat, self.lon, self.night_factor = lat, lon, night_factor
        self.edges = np.arange(int(np.ceil(seconds/STEP))+2)*STEP
        self.altitudes = np.array([elevation(self.start+timedelta(seconds=float(s)),lat,lon)
                                   for s in self.edges[:-1]+STEP/2])
        self.rates = night_factor+(1-night_factor)*np.clip((self.altitudes+6)/6,0,1)
        self.integral = np.r_[0, np.cumsum(self.rates*STEP)]

    def effective(self, seconds):
        return np.interp(seconds,self.edges,self.integral)

    def factor(self, seconds):
        idx = np.minimum((np.maximum(seconds,0)/STEP).astype(int) if isinstance(seconds,np.ndarray)
                         else int(max(seconds,0)//STEP),len(self.rates)-1)
        return self.rates[idx]

    def travel_seconds(self, elapsed, nominal_seconds):
        target = self.effective(elapsed)+nominal_seconds
        finish = np.interp(target,self.integral,self.edges)
        # Values beyond this forecast's horizon are only used to detect an
        # incomplete step; partial distance is integrated only up to the horizon.
        finish += np.maximum(target-self.integral[-1],0)/self.rates[-1]
        return np.maximum(finish-elapsed,0)

    def summary(self, seconds):
        widths = np.maximum(0,np.minimum(self.edges[1:],seconds)-self.edges[:-1])
        first = elevation(self.start,self.lat,self.lon)
        last = elevation(self.start+timedelta(seconds=float(seconds)),self.lat,self.lon)
        return {"enabled":True,"model":MODEL,"status":"UNCALIBRATED_ASSUMPTION",
                "reference_lat":self.lat,"reference_lon":self.lon,"integration_seconds":STEP,
                "start_at":self.start.isoformat(),"end_at":(self.start+timedelta(seconds=float(seconds))).isoformat(),
                "start_phase":phase(first),"end_phase":phase(last),"solar_elevation_deg":round(last,2),
                "night_factor":self.night_factor,"current_factor":float(self.factor(seconds)),
                "day_minutes":float(widths[self.altitudes>=0].sum()/60),
                "twilight_minutes":float(widths[(self.altitudes<0)&(self.altitudes>-6)].sum()/60),
                "night_minutes":float(widths[self.altitudes<=-6].sum()/60),
                "effective_motion_hours":float(self.effective(seconds)/3600),"source":SOURCE,
                "note":"태양고도 기반 근사와 야간 이동계수는 미검증 가정. 조명, 날씨, 수면, 탐지확률은 미반영."}


def clock_for(params, start_at, seconds):
    if not params.get("daylight_enabled",False):
        return None
    if params.get("daylight_model",MODEL) != MODEL:
        raise ValueError("지원하지 않는 낮밤 가정 버전입니다.")
    return SolarClock(start_at,seconds,params["lat"],params["lon"],params.get("night_factor",.6))


def context(params, start_at=None, seconds=None):
    if not params.get("daylight_enabled",False):
        return {"enabled":False,"note":"이 임무는 낮밤 가정을 사용하지 않습니다. 기존 계산은 유지됩니다."}
    seconds = params["hours"]*3600 if seconds is None else seconds
    return clock_for(params,start_at or params["missing_at"],seconds).summary(seconds)
