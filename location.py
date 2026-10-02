"""GET /location/analyze — location suitability: fetch + cache + pure rules.

Stdlib urllib only (no new deps). Hard sources (elevation, temperature) map
failures to 502/504; soft sources (rain, soil, place) degrade to None and
location_rules applies Nepal zonal heuristics. Results are cached in SQLite by
0.01° cell for 30 days. No torch imports (same rationale as auth/history).
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from fastapi import HTTPException, APIRouter

from db import connect
from location_rules import build_analysis

router = APIRouter(prefix="/location", tags=["location"])

TIMEOUT = 7.0
USER_AGENT = "PotatoDoc/1.0 (https://potatodoc.app)"
CACHE_TTL_DAYS = 30


# --- fetchers (exported for tests to patch) ----------------------------------

def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_elevation(lat, lon):
    data = _get("https://api.open-meteo.com/v1/elevation?" + urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon}))
    return float(data["elevation"][0])


def fetch_forecast_temp(lat, lon):
    """Mean of the next 7 days' daily mean temperature (climate-API fallback)."""
    data = _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon,
         "daily": "temperature_2m_mean", "forecast_days": 7}))
    vals = [v for v in (data.get("daily", {}).get("temperature_2m_mean") or [])
            if v is not None]
    if not vals:
        raise ValueError("no forecast temperature in response")
    return sum(vals) / len(vals)


def fetch_climate(lat, lon, today=None):
    """730-day climate normal → annual rain mm, coldest-month mean, current
    month normal. Missing data → None per field (soft)."""
    today = today or date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=730)
    data = _get("https://climate-api.open-meteo.com/v1/climate?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": "precipitation_sum,temperature_2m_mean",
    }))
    daily = data.get("daily") or {}
    rain = [v for v in (daily.get("precipitation_sum") or []) if v is not None]
    annual = (sum(rain) * 365.0 / len(rain)) if rain else None

    by_month: dict[int, list] = {}
    for iso, t in zip(daily.get("time") or [], daily.get("temperature_2m_mean") or []):
        if t is not None and len(iso) >= 7:
            try:
                by_month.setdefault(int(iso[5:7]), []).append(float(t))
            except ValueError:
                continue
    coldest = min((sum(v) / len(v) for v in by_month.values()), default=None)
    now_vals = by_month.get(today.month)
    current = (sum(now_vals) / len(now_vals)) if now_vals else None
    return {"annual_rain": annual, "coldest_c": coldest, "temp_c": current}


def fetch_soil(lat, lon):
    """SoilGrids 0–5 cm means → % / pH; texture null pixel → None (heuristic)."""
    params = [
        ("lon", lon), ("lat", lat),
        ("property", "clay"), ("property", "sand"),
        ("property", "silt"), ("property", "phh2o"),
        ("depth", "0-5cm"), ("value", "mean"),
    ]
    data = _get("https://rest.isric.org/soilgrids/v2.0/properties/query?"
                + urllib.parse.urlencode(params))
    out: dict = {}
    for layer in (data.get("properties", {}).get("layers") or []):
        mean = None
        for depth in layer.get("depths") or []:
            if depth.get("label") == "0-5cm":
                mean = (depth.get("values") or {}).get("mean")
                break
        factor = (layer.get("unit_measure") or {}).get("d_factor") or 10
        out[layer.get("name")] = None if mean is None else round(mean / factor, 1)
    if any(out.get(k) is None for k in ("clay", "sand", "silt")):
        return None
    return {"clay": out["clay"], "sand": out["sand"],
            "silt": out["silt"], "ph": out.get("phh2o")}


def fetch_place(lat, lon):
    data = _get("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "json", "zoom": 10}))
    return data.get("display_name") or None


# --- cache (0.01° cell, 30-day TTL) ------------------------------------------

def _cache_get(lat, lon):
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, created_at FROM location_analysis "
            "WHERE lat_key = ? AND lon_key = ?",
            (round(lat, 2), round(lon, 2)),
        ).fetchone()
    if row is None:
        return None
    try:
        created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if datetime.utcnow() - created > timedelta(days=CACHE_TTL_DAYS):
        return None
    return json.loads(row["payload"])


def _cache_put(lat, lon, payload):
    with connect() as conn:
        conn.execute(
            "INSERT INTO location_analysis (lat_key, lon_key, payload) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(lat_key, lon_key) DO UPDATE SET "
            "payload = excluded.payload, created_at = datetime('now')",
            (round(lat, 2), round(lon, 2), json.dumps(payload)),
        )


# --- endpoint -----------------------------------------------------------------

def _is_timeout(exc):
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return (isinstance(exc, urllib.error.URLError)
            and isinstance(exc.reason, (TimeoutError, socket.timeout)))


def _hard_failure(exc):
    if _is_timeout(exc):
        return HTTPException(504, "Location service timeout")
    return HTTPException(502, "Location service unavailable")


def _build(lat, lon):
    try:
        alt = fetch_elevation(lat, lon)
    except Exception as exc:
        raise _hard_failure(exc)

    climate = {"annual_rain": None, "coldest_c": None, "temp_c": None}
    try:
        got = fetch_climate(lat, lon)
        for key in climate:
            climate[key] = got.get(key)
    except Exception:
        pass  # soft: rain factor neutral, coldest via altitude heuristic

    temp_c = climate["temp_c"]
    if temp_c is None:
        try:
            temp_c = fetch_forecast_temp(lat, lon)
        except Exception as exc:
            raise _hard_failure(exc)

    try:
        soil = fetch_soil(lat, lon)
    except Exception:
        soil = None
    try:
        place = fetch_place(lat, lon)
    except Exception:
        place = None

    return build_analysis(
        lat=lat, lon=lon, alt=alt, temp_c=temp_c,
        coldest_c=climate["coldest_c"], annual_rain=climate["annual_rain"],
        soil=soil, place=place,
    )


@router.get("/analyze")
def analyze(lat: str | None = None, lon: str | None = None):
    """Public suitability analysis; 400 bad coords, 502/504 hard-source errors."""
    try:
        flat, flon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid coordinates")
    if not (-90 <= flat <= 90) or not (-180 <= flon <= 180):
        raise HTTPException(400, "Invalid coordinates")

    cached = _cache_get(flat, flon)
    if cached is not None:
        return cached
    payload = _build(flat, flon)
    _cache_put(flat, flon, payload)
    return payload
