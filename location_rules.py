"""Nepal-grounded potato suitability rules for GET /location/analyze.

Pure functions only — no I/O and no FastAPI (the router + fetch layer live in
location.py). Sources are fixed in the spec
(docs/superpowers/specs/2026-10-02-location-suitability-design.md):
Nepal DofA/DoF agro-ecological altitude zones, NARC/NPRP released-variety
domains, and the USDA texture-class box rules. All English strings returned
here are templates the app runs through its i18n `t()`; placeholders like
{zone} are substituted client-side (mobile/src/utils/locationText.js).
"""

# --- altitude zones (Nepal DofA/DoF classification) --------------------------

ZONES = (
    (300, "Tropical"),
    (1000, "Sub-tropical"),
    (2000, "Warm Temperate"),
    (3000, "Cool Temperate"),
    (4000, "Sub-alpine"),
    (10**9, "Alpine"),
)

# Region words for the recommendation {zone} placeholder — the screenshot's
# "typical of Nepal's mid-hills" is prose, not the belt name (811m → mid-hills).
REGIONS = (
    (300, "Terai"),
    (600, "low hills"),
    (2000, "mid-hills"),
    (3000, "high hills"),
    (10**9, "Himalaya"),
)


def altitude_zone(alt):
    for ceiling, name in ZONES:
        if alt < ceiling:
            return name
    return "Alpine"


def region_of(alt):
    for ceiling, name in REGIONS:
        if alt < ceiling:
            return name
    return "Himalaya"


# --- score helpers -----------------------------------------------------------

def _interp(x, pts):
    if x <= pts[0][0]:
        return float(pts[0][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            if x1 == x0:
                return float(y1)
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return 0.0


def altitude_score(alt):
    return round(_interp(alt, [
        (0, 40), (100, 40), (400, 65), (800, 100),
        (3000, 100), (3500, 75), (4000, 45), (4500, 0),
    ]))


def temperature_score(t):
    if 8 <= t <= 20:
        return 100
    if 5 <= t < 8 or 20 < t <= 23:
        return 80
    if 0 <= t < 5 or 23 < t <= 26:
        return 60
    return 25


def rainfall_score(mm):
    if mm is None:
        return 75
    if 600 <= mm <= 2000:
        return 100
    if 400 <= mm < 600 or 2000 < mm <= 2800:
        return 80
    if 200 <= mm < 400 or 2800 < mm <= 3500:
        return 60
    return 30


def moisture_class(mm):
    if mm is None:
        return None
    if mm < 500:
        return "Arid"
    if mm < 1000:
        return "Semi-arid"
    if mm < 1500:
        return "Sub-humid"
    if mm < 2500:
        return "Humid"
    return "Per-humid"


def climate_zone(coldest_c, alt=None):
    """Temperature-regime zone; altitude heuristic when coldest month unknown."""
    if coldest_c is None:
        if alt is None:
            return "Temperate"
        if alt < 300:
            return "Tropical"
        if alt < 1000:
            return "Subtropical"
        if alt < 2000:
            return "Temperate"
        if alt < 3500:
            return "Cold"
        return "Alpine"
    if coldest_c >= 14.5:
        return "Tropical"
    if coldest_c >= 8:
        return "Subtropical"
    if coldest_c >= 3:
        return "Temperate"
    if coldest_c >= 0:
        return "Cold"
    return "Alpine"


# --- soil --------------------------------------------------------------------

def texture_class(clay, sand, silt):
    """USDA texture class from % clay/sand/silt — display-grade box rules."""
    if clay >= 40 and silt >= 40:
        return "Silty Clay"
    if clay >= 40 and sand >= 45:
        return "Sandy Clay"
    if clay >= 40:
        return "Clay"
    if clay >= 20 and sand >= 45 and silt < 28:
        return "Sandy Clay Loam"
    if clay >= 27 and silt >= 53:
        return "Silty Clay Loam"
    if clay >= 27:
        return "Clay Loam"
    if silt >= 80 and sand <= 20:
        return "Silt"
    if silt >= 50:
        return "Silt Loam"
    if sand >= 85:
        return "Sand"
    if sand >= 70:
        return "Loamy Sand"
    if sand >= 43 and clay < 20:
        return "Sandy Loam"
    return "Loam"


TEXTURE_SCORES = {
    "Loam": 95, "Sandy Loam": 95, "Silt Loam": 95, "Clay Loam": 95,
    "Sandy Clay Loam": 85, "Loamy Sand": 85,
    "Sand": 70, "Silty Clay Loam": 70,
    "Clay": 55, "Silty Clay": 55, "Sandy Clay": 55, "Silt": 55,
}


def ph_score(ph):
    if ph is None:
        return 75
    if 5.5 <= ph <= 6.5:
        return 100
    if 5.0 <= ph < 5.5 or 6.5 < ph <= 7.0:
        return 85
    if 4.5 <= ph < 5.0 or 7.0 < ph <= 7.5:
        return 65
    return 40


def soil_score(texture, ph):
    return round(0.6 * TEXTURE_SCORES.get(texture, 75) + 0.4 * ph_score(ph))


def heuristic_texture(alt):
    if alt < 300:
        return "Alluvial Sandy Loam"
    if alt < 1500:
        return "Loam"
    if alt < 2500:
        return "Clay Loam"
    return "Sandy Loam"


# --- seasons / varieties -----------------------------------------------------

def feasible_seasons(alt):
    out = []
    if alt < 3000:
        out.append("Winter (Oct–Feb)")
    if 500 <= alt <= 3000:
        out.append("Spring (Mar–May)")
    return " & ".join(out) if out else "Off-season"


# NARC/NPRP released + registered varieties: (name, min_alt_m, max_alt_m).
# Bands derive from each variety's official recommended domain — see spec.
VARIETIES = [
    ("Khumal Seto-1", 100, 3000),
    ("Janakdev", 100, 3000),
    ("Khumal Rato-2", 0, 800),
    ("Desiree", 200, 3000),
    ("Kufri Sindhuri", 200, 2500),
    ("Kufri Jyoti", 1000, 3500),
    ("Khumal Laxmi", 0, 2500),
    ("IPY-8", 0, 2500),
    ("Khumal Ujjwal", 1000, 3500),
    ("Khumal Upahar", 0, 2000),
    ("Khumal Bikas", 1000, 3500),
    ("Cardinal", 100, 4000),
    ("Rojita", 1600, 3500),
    ("MS 42.3", 100, 1600),
    ("TPS-1", 0, 2000),
    ("TPS-2", 0, 2000),
]


def recommend_varieties(alt, limit=6):
    fits = []
    for name, lo, hi in VARIETIES:
        if not (lo <= alt <= hi):
            continue
        span = hi - lo
        rel = (alt - lo) / span if span else 0.5
        if 0.25 <= rel <= 0.75:
            fit = 20
        elif rel < 0.1 or rel > 0.9:
            fit = -40
        else:
            fit = 0
        fits.append((fit, name))
    fits.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in fits[:limit]]


# --- condition-filtered text -------------------------------------------------

def challenges(annual_rain, alt, coldest_c, clay_pct, ph, climate_zone_name):
    out = []
    if annual_rain is not None and annual_rain > 1500:
        out.append("Monsoon disease pressure (Jun–Sep)")
    if alt > 2000 or (coldest_c is not None and coldest_c < 3):
        out.append("Frost risk at planting or harvest")
    if clay_pct is not None and clay_pct > 30 and annual_rain and annual_rain > 2000:
        out.append("Waterlogging on heavy soils")
    if annual_rain is not None and annual_rain < 800:
        out.append("Low rainfall — irrigation needed")
    if coldest_c is not None and coldest_c >= 14.5:
        out.append("Heat stress in late crop (Mar–May)")
    if ph is not None and ph < 5.5:
        out.append("Soil too acidic — apply lime before planting")
    return out


def tips(annual_rain, clay_pct, ph, alt):
    out = [
        "Plant in well-prepared ridges or raised beds for optimal drainage",
        "Hill up soil around plants when they reach 20–25cm to prevent greening",
        "Rotate crops — avoid planting potatoes in the same field for 3+ years",
        "Test soil pH (ideal: 5.5–6.5) and adjust with lime or sulfur as needed",
        "In Nepal: plant Khumal varieties for best local adaptation and yield",
    ]
    if clay_pct and clay_pct >= 30:
        out.append("Add compost and open drainage channels — heavy soil compacts easily")
    if annual_rain is not None and annual_rain < 800:
        out.append("Plan drip or furrow irrigation — rainfall alone will not carry the crop")
    if annual_rain is not None and annual_rain > 1500:
        out.append("Watch for late blight in the humid months — remove affected leaves early")
    if alt > 2500:
        out.append("Use well-sprouted seed tubers and mulch to protect against cold nights")
    if ph is not None and ph < 5.5:
        out.append("Apply agricultural lime 2–3 weeks before planting to lift pH")
    return out


# --- overall -----------------------------------------------------------------

WEIGHTS = {
    "altitude": 0.25, "temp": 0.25, "soil": 0.25,
    "rainfall": 0.15, "climate": 0.10,
}


def overall_score(factors):
    return round(sum(factors[k]["score"] * w for k, w in WEIGHTS.items()))


def band_of(score):
    if score >= 80:
        return "Excellent"
    if score >= 65:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"


RECOMMENDATIONS = {
    "Excellent": "Your location is excellent for potato cultivation — typical of Nepal's {zone}. "
                 "Conditions closely match the ideal agronomic requirements. Focus on disease "
                 "prevention and variety selection for maximum yield.",
    "Good": "Your location suits potato well — conditions are close to Nepal's ideal for {zone}. "
            "Manage soil fertility and watch the monsoon for disease pressure.",
    "Fair": "Potato can be grown here, but conditions are only moderately suitable in this "
            "{zone} zone. Choose tolerant varieties, improve drainage or irrigation, and "
            "expect lower yields.",
    "Poor": "This location is poorly suited to potato under natural conditions. Consider "
            "another crop, or invest in irrigation, soil amendment and microclimate "
            "protection before planting.",
}


def build_analysis(*, lat, lon, alt, temp_c, coldest_c, annual_rain, soil, place):
    """Assemble the full GET /location/analyze payload (spec schema)."""
    zone = altitude_zone(alt)
    moisture = moisture_class(annual_rain)
    cz = climate_zone(coldest_c, alt)

    if soil and soil.get("clay") is not None and soil.get("sand") is not None:
        texture = texture_class(soil["clay"], soil["sand"], soil.get("silt") or 0)
        soil_source = "SoilGrids"
    else:
        texture = heuristic_texture(alt)
        soil_source = "zonal estimate"
    ph = soil.get("ph") if soil else None

    factors = {
        "altitude": {
            "key": "altitude", "label": "Altitude", "value": "%dm" % round(alt),
            "score": altitude_score(alt),
            "why": "{alt} m — potato in Nepal spans 100–4000 m; optimum 800–3000 m",
            "vars": {"alt": str(round(alt))},
        },
        "temp": {
            "key": "temp", "label": "Est. Temperature", "value": "~%d°C" % round(temp_c),
            "score": temperature_score(temp_c),
            "why": "~{temp}°C — tuber initiation prefers a cool 8–20°C",
            "vars": {"temp": str(round(temp_c))},
        },
        "rainfall": {
            "key": "rainfall", "label": "Rainfall Zone", "value": zone,
            "score": rainfall_score(annual_rain),
            "why": "{rain} mm typical — Nepal's monsoon delivers most of it Jun–Sep",
            "vars": {"rain": str(round(annual_rain))} if annual_rain is not None else {},
        },
        "soil": {
            "key": "soil", "label": "Est. Soil Type", "value": texture,
            "score": soil_score(texture, ph),
            "why": "{texture}, pH {ph} — loam family drains best for tubers",
            "vars": {
                "texture": texture,
                "ph": ("%.1f" % ph) if ph is not None else "—",
            },
        },
        "climate": {
            "key": "climate", "label": "Climate Zone", "value": cz,
            "score": climate_score(cz),
            "why": "{zone} regime — coldest month near {coldest}°C",
            "vars": {
                "zone": cz,
                "coldest": ("%.1f" % coldest_c) if coldest_c is not None else "—",
            },
        },
    }
    # rainfall why needs the actual mm or a neutral phrasing
    if annual_rain is None:
        factors["rainfall"]["why"] = "Rainfall data unavailable — zone estimated from altitude"

    score = overall_score(factors)
    band = band_of(score)

    summary = {
        "temp_c": int(round(temp_c)),
        "season": feasible_seasons(alt),
        "soil": texture,
    }
    return {
        "score": score,
        "band": band,
        "place": place or "Your location",
        "coords": {"lat": round(lat, 6), "lon": round(lon, 6)},
        "altitude_m": int(round(alt)),
        "recommendation": RECOMMENDATIONS[band],
        "summary": summary,
        "factors": [factors[k] for k in ("altitude", "temp", "rainfall", "soil", "climate")],
        "challenges": challenges(
            annual_rain, alt, coldest_c,
            (soil or {}).get("clay"), ph, cz,
        ),
        "rainfall_zone": zone,
        "region": region_of(alt),
        "varieties": recommend_varieties(alt),
        "tips": tips(annual_rain, (soil or {}).get("clay"), ph, alt),
        # extra (non-schema) keys used by the why templates / debugging:
        "_meta": {"moisture": moisture, "soil_source": soil_source},
    }


def climate_score(zone_name):
    return {
        "Tropical": 90, "Subtropical": 95, "Temperate": 85,
        "Cold": 65, "Alpine": 35,
    }.get(zone_name, 75)
