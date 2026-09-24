"""Geospatial and solar helpers for itinerary detail.

Two things the itinerary needs that the mock catalogs cannot fake convincingly:

1. **How far apart are two stops, and how long does it take?** Computed from real
   coordinates with a haversine distance and a mode chosen by distance band, so
   "Belem Tower -> Alfama" reports a plausible 8.2 km / 27 min by tram rather than
   an invented number.

2. **When is a place actually worth visiting?** A miradouro is a different
   experience at 14:00 and at golden hour. Sunrise/sunset are computed with the
   NOAA solar position algorithm from latitude, longitude and date, so the times
   shift correctly across destinations and seasons -- Reykjavik in June really
   does return a ~03:00 sunrise, and the midnight-sun case is handled explicitly
   rather than producing a NaN.

Everything here is pure and deterministic: no clock reads, no randomness, no I/O.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

# Mean earth radius, km.
_EARTH_KM = 6371.0088

# Sun-centre zenith angles, degrees.
_ZENITH_SUNRISE = 90.833  # includes refraction and solar disc radius
_ZENITH_GOLDEN = 84.0  # sun 6 deg above the horizon


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


# (max_km, mode, effective_kmh, fixed_overhead_min, per_person_usd)
#
# Speeds are door-to-door effective, not vehicle top speed: a metro averaging
# 22 km/h between stations is the honest number once you include walking to the
# platform and waiting. Straight-line distance is also shorter than the real
# route, so these deliberately run slow to compensate.
_MODES: tuple[tuple[float, str, float, float, float], ...] = (
    (1.2, "walk", 4.6, 0.0, 0.0),
    (10.0, "metro", 22.0, 6.0, 2.50),
    (60.0, "taxi", 30.0, 4.0, 18.00),
    (float("inf"), "rail", 75.0, 20.0, 34.00),
)

# Straight-line to street-distance multiplier. Real routes detour around blocks,
# rivers and one-way systems.
_DETOUR = 1.28


def travel_leg(
    origin: dict[str, Any],
    dest: dict[str, Any],
    *,
    travelers: int = 1,
    speed_factor: float = 1.0,
) -> dict[str, Any]:
    """Estimate the hop between two places.

    `speed_factor` scales the effective speed for city-specific traffic: Mexico
    City's surface traffic is not Reykjavik's.
    """
    straight = haversine_km(
        float(origin["lat"]), float(origin["lon"]),
        float(dest["lat"]), float(dest["lon"]),
    )
    distance = straight * _DETOUR

    for max_km, mode, kmh, overhead, fare in _MODES:
        if distance <= max_km:
            break

    effective_kmh = max(1.0, kmh * (speed_factor if mode != "walk" else 1.0))
    minutes = overhead + (distance / effective_kmh) * 60.0

    return {
        "from": origin.get("name", ""),
        "to": dest.get("name", ""),
        "distance_km": round(distance, 2),
        "minutes": int(round(minutes)),
        "mode": mode,
        "cost_usd": round(fare * max(1, travelers), 2),
    }


def _solar_geometry(day: date) -> tuple[float, float]:
    """NOAA fractional-year expansion -> (equation of time mins, declination rad)."""
    n = day.timetuple().tm_yday
    # Fractional year, evaluated at local noon.
    g = 2.0 * math.pi / 365.0 * (n - 1 + 0.5)

    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.001480 * math.sin(3 * g)
    )
    return eqtime, decl


def _hour_angle(lat: float, decl: float, zenith_deg: float) -> float | None:
    """Hour angle in degrees, or None when the sun never reaches that zenith."""
    lat_r = math.radians(lat)
    cos_ha = math.cos(math.radians(zenith_deg)) / (
        math.cos(lat_r) * math.cos(decl)
    ) - math.tan(lat_r) * math.tan(decl)
    if cos_ha > 1.0 or cos_ha < -1.0:
        # > 1: sun stays below the zenith all day (polar night).
        # < -1: sun stays above it all day (midnight sun).
        return None
    return math.degrees(math.acos(cos_ha))


def _clock(minutes_local: float) -> str:
    m = int(round(minutes_local)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def solar_events(
    lat: float, lon: float, day: date, tz_offset_hours: float
) -> dict[str, Any]:
    """Sunrise, sunset and golden-hour bounds as local HH:MM strings.

    Returns `daylight: "midnight_sun" | "polar_night" | "normal"`. Callers must
    handle the first two: Reykjavik in June genuinely has no sunset, and an
    itinerary that schedules a sunset viewpoint there should say so rather than
    print a fabricated time.
    """
    eqtime, decl = _solar_geometry(day)
    tz_min = tz_offset_hours * 60.0

    # Solar noon is the one event that always exists.
    noon_local = 720.0 - 4.0 * lon - eqtime + tz_min

    ha = _hour_angle(lat, decl, _ZENITH_SUNRISE)
    if ha is None:
        # Which pole case? Positive declination with positive latitude = sun up.
        lat_r, decl_deg = math.radians(lat), math.degrees(decl)
        midnight_sun = (lat >= 0 and decl_deg > 0) or (lat < 0 and decl_deg < 0)
        del lat_r
        return {
            "daylight": "midnight_sun" if midnight_sun else "polar_night",
            "sunrise": None,
            "sunset": None,
            "golden_morning_end": None,
            "golden_evening_start": None,
            "solar_noon": _clock(noon_local),
        }

    ha_golden = _hour_angle(lat, decl, _ZENITH_GOLDEN)

    sunrise = noon_local - 4.0 * ha
    sunset = noon_local + 4.0 * ha
    if ha_golden is None:
        # Sun never climbs to 6 deg: the whole day is effectively golden.
        golden_am, golden_pm = sunset, sunrise
    else:
        golden_am = noon_local - 4.0 * ha_golden
        golden_pm = noon_local + 4.0 * ha_golden

    return {
        "daylight": "normal",
        "sunrise": _clock(sunrise),
        "sunset": _clock(sunset),
        "golden_morning_end": _clock(golden_am),
        "golden_evening_start": _clock(golden_pm),
        "solar_noon": _clock(noon_local),
        "_sunrise_min": sunrise,
        "_sunset_min": sunset,
        "_golden_pm_min": golden_pm,
    }


def to_minutes(hhmm: str) -> int:
    """'09:30' -> 570. Tolerates junk by returning 0."""
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 0


def from_minutes(minutes: float) -> str:
    return _clock(minutes)


def window_for(best_time: str, sun: dict[str, Any]) -> tuple[float, str]:
    """Preferred start time (local minutes) and a human reason for a stop.

    Sunrise/sunset stops are anchored to the actual solar times for that date and
    latitude; everything else falls back to conventional clock windows.
    """
    sunrise = sun.get("_sunrise_min")
    sunset = sun.get("_sunset_min")
    golden_pm = sun.get("_golden_pm_min")

    if best_time == "sunrise":
        if sunrise is None:
            return 6 * 60, "no true sunrise at this latitude and date"
        return sunrise - 20, f"sunrise at {sun['sunrise']}"
    if best_time == "sunset":
        if sunset is None or golden_pm is None:
            return 21 * 60, "no true sunset at this latitude and date"
        return golden_pm, f"golden hour from {sun['golden_evening_start']}"

    if best_time == "evening" and sunset is not None:
        return max(19 * 60 + 30, sunset + 45), "comes alive after dark"

    fixed = {
        "morning": (9 * 60, "quiet before the coach tours arrive"),
        "midday": (11 * 60 + 30, "peak opening hours"),
        "afternoon": (14 * 60, "warmest part of the day"),
        "evening": (19 * 60 + 30, "comes alive after dark"),
        "anytime": (10 * 60, "flexible timing"),
    }
    return fixed.get(best_time, fixed["anytime"])


# Ordering key so a day reads chronologically by natural light.
_TIME_RANK = {
    "sunrise": 0,
    "morning": 1,
    "midday": 2,
    "anytime": 3,
    "afternoon": 4,
    "sunset": 5,
    "evening": 6,
}


def time_rank(best_time: str) -> int:
    return _TIME_RANK.get(best_time, 3)
