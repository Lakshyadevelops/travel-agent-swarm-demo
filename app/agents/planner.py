"""Day-by-day itinerary planner.

Pure and deterministic. Used by two agents for two different purposes:

* the **budget guardrail** calls it to price the ground portion of a candidate
  plan (entry fees + local transport + food), so its verdict reflects what the
  traveler will actually spend rather than a flat per-day guess;
* the **itinerary agent** calls it to render the final timeline.

Because both call the same function with the same blackboard inputs, the total
the budget agent approved is exactly the total the itinerary shows.

What goes into each day:
  0. Apply the traveler's note (preferences.py): leave out what they asked to
     skip, and rank the rest -- places the scout added for a wish first, then
     places that match their interests, then everything else.
  1. Assign places to days, one priority tier at a time, so that when days are
     short it is what the traveler asked for that gets in. Within a tier,
     sunrise and sunset places are the scarce slots -- only one of each per
     day -- so they are placed first. Everything else is filled greedily by
     proximity to what the day already contains. That keeps each day
     geographically tight instead of zig-zagging across the city.
  2. Order each day by natural light (sunrise -> morning -> ... -> evening).
  3. Walk the day with a clock: travel leg from the previous place, wait for the
     place's best window if early, insert lunch when the day crosses midday.

A flight that lands on a later date (SFO -> Bali) turns the days before landing
into travel days. On a researched destination every leg uses the modes that
exist there (no metro in Bali, trains for long legs in England), and a day trip
is judged by the journey from the hotel rather than distance from a centre.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from app.agents.preferences import (
    CATEGORY_LABEL,
    MAX_NOTE_CHARS,
    PACE_LABEL,
    Tailoring,
    clean_text,
    normalize_prefs,
    parse_preferences,
)
from app.providers.data import utc_offset
from app.providers.geo import (
    from_minutes,
    haversine_km,
    solar_events,
    time_rank,
    to_minutes,
    travel_leg,
    window_for,
)

__all__ = ["parse_preferences", "plan_days"]

_DAY_END = 23 * 60  # nothing starts after 23:00
_DEFAULT_START = 8 * 60 + 30
_LATE_START = 9 * 60 + 30  # "no early starts": out of the hotel no earlier than this
_LUNCH_START, _LUNCH_LATEST = 12 * 60 + 15, 14 * 60 + 30
_LUNCH_MIN = 60
_ARRIVAL_BUFFER_MIN = 45  # deplane, immigration, bags
_CHECKIN_MIN = 30
_FOOD_CATEGORIES = {"market", "food"}
# Places further than this from the city centre are day trips: they get a day
# to themselves rather than being wedged between two city stops.
_DAY_TRIP_KM = 12.0
# A researched destination (a region such as Bali has no single centre) judges
# a day trip by the journey from the hotel instead, one way, in minutes.
_DAY_TRIP_MIN = 45
_ALL_BANDS = {"sunrise", "morning", "midday", "anytime", "afternoon", "sunset", "evening"}
_SCARCE = ("sunrise", "sunset", "evening")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return date.today()


def _arrival_minutes(flight: dict | None) -> int | None:
    if not flight:
        return None
    parts = str(flight.get("arrive", "")).split()
    return to_minutes(parts[-1]) if parts else None


def arrival_offset(flight: dict | None, brief: dict[str, Any]) -> int:
    """Days between leaving and landing, by the destination's calendar.

    0 when the flight lands the day it leaves -- always, on the scripted model.
    Days before landing are travel days, and hotel nights start the night you
    land. Capped so the trip keeps at least one day on the ground.
    """
    if not flight or flight.get("no_flight"):
        return 0
    try:
        landed = date.fromisoformat(str(flight.get("arrive", ""))[:10])
    except ValueError:
        return 0
    start = _parse_date(brief.get("start_date", ""))
    n_days = max(1, int(brief.get("nights", 3)))
    return max(0, min(n_days - 1, (landed - start).days))


def _flight_item(flight: dict[str, Any], shortlist: dict[str, Any], d: int) -> dict[str, Any]:
    """A travel day's entry: the flight itself, or still being on the way."""
    arrive = str(flight.get("arrive", ""))
    landed = _parse_date(arrive)
    lands = f"lands {landed:%a %b} {landed.day} at {arrive[-5:]} local time"
    if d > 0:
        return {"kind": "flight", "start": "", "end": "", "name": "Still on the way",
                "note": lands[0].upper() + lands[1:]}
    stops = int(flight.get("stops", 0) or 0)
    route = "nonstop" if not stops else (
        f"{stops} stop{'s' if stops != 1 else ''}"
        + (f" via {flight['via']}" if flight.get("via") else ""))
    depart = str(flight.get("depart", ""))[-5:]
    to = (shortlist.get("airport") or {}).get("name") or shortlist.get("destination", "")
    return {"kind": "flight", "start": depart, "end": "", "name": f"Fly to {to}",
            "note": f"{flight.get('carrier', '')}, {route}; leaves {depart} local time, {lands}"}


def _near_km(a: dict, b: dict) -> bool:
    """Catalog cities: a day trip's companion stop is within the day-trip radius."""
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) <= _DAY_TRIP_KM


def _assign(
    pois: list[dict],
    n_days: int,
    capacity: list[int],
    hotel: dict,
    tier: Callable[[dict], int],
    allowed_bands: list[set[str]],
    is_trip: Callable[[dict], bool],
    first_day: int = 0,
    near: Callable[[dict, dict], bool] | None = None,
    fit_local: bool = False,
) -> tuple[list[list[dict]], list[dict]]:
    days: list[list[dict]] = [[] for _ in range(n_days)]
    trip_day = [False] * n_days
    unscheduled: list[dict] = []
    near = near or _near_km

    def room(i: int) -> bool:
        return len(days[i]) < capacity[i]

    def load(i: int) -> float:
        return len(days[i]) / max(1, capacity[i])

    def hosts(i: int, p: dict) -> bool:
        return capacity[i] > 0 and p["best_time"] in allowed_bands[i]

    def ok(i: int, p: dict) -> bool:
        if not room(i) or p["best_time"] not in allowed_bands[i]:
            return False
        # A day-trip day holds the trip, one companion stop, and optionally a
        # sunset/evening slot. The companion must either come before leaving
        # town or be near the trip -- never a long drive back for a city stop.
        if trip_day[i] and p["best_time"] not in ("sunset", "evening"):
            daytime = [s for s in days[i] if s["best_time"] not in ("sunset", "evening")]
            if len(daytime) >= 2:
                return False
            trip = daytime[0] if daytime else None
            if trip is not None:
                before = time_rank(p["best_time"]) < time_rank(trip["best_time"])
                if not (before or near(trip, p)):
                    return False
        return True

    # 1. Day trips claim whole days, latest first (never the landing day, nor a
    #    travel day before it). A trip the traveler wished for gets first pick.
    #    But a trip never takes the last day on which a local place of the same
    #    or higher priority could still be seen: on a short trip, several
    #    sights beat one long drive.
    trips = sorted((p for p in pois if is_trip(p)), key=tier)
    local = [p for p in pois if not is_trip(p)]
    ground = range(first_day, n_days)
    for p in trips:
        slots = [i for i in range(n_days - 1, first_day, -1) if not days[i] and not trip_day[i]]
        if not slots or p["best_time"] not in allowed_bands[slots[0]] | {"sunrise"}:
            unscheduled.append(p)
            continue
        i = slots[0]
        others = [j for j in ground if j != i and not trip_day[j]]
        if any(tier(q) <= tier(p) and hosts(i, q) and not any(hosts(j, q) for j in others)
               for q in local):
            unscheduled.append(p)
            continue
        if fit_local:
            # Nor may it leave too few stops for the local places ranked as
            # high: a second day trip must not crowd out the city's own
            # must-sees. A trip day still fits one of them, back in town.
            needed = sum(1 for q in local
                         if tier(q) <= tier(p) and any(hosts(j, q) for j in ground))
            if needed > sum(capacity[j] for j in others) + sum(trip_day) + 1:
                unscheduled.append(p)
                continue
        days[i].append(p)
        trip_day[i] = True

    def centroid(i: int) -> tuple[float, float]:
        pts = days[i] or [hotel]
        return (
            sum(p["lat"] for p in pts) / len(pts),
            sum(p["lon"] for p in pts) / len(pts),
        )

    # Steps 2 and 3 run once per priority tier -- wishes, then interests, then
    # the rest -- so a place the traveler asked for is never crowded out by one
    # they didn't.
    for level in sorted({tier(p) for p in local}):
        group = [p for p in local if tier(p) == level]

        # 2. Scarce light-bound slots: at most one of each per day, least-loaded first.
        for kind in _SCARCE:
            for p in [q for q in group if q["best_time"] == kind]:
                slots = [i for i in range(n_days)
                         if ok(i, p) and not any(s["best_time"] == kind for s in days[i])]
                if slots:
                    days[min(slots, key=lambda i: (load(i), i))].append(p)
                else:
                    unscheduled.append(p)

        # 3. Fill: the least-loaded open day takes its closest remaining place,
        #    keeping each day geographically tight.
        rest = [p for p in group if p["best_time"] not in _SCARCE]
        closed: set[int] = set()
        while rest:
            open_days = [i for i in range(n_days) if i not in closed and room(i)]
            if not open_days:
                break
            i = min(open_days, key=lambda j: (load(j), j))
            candidates = [p for p in rest if ok(i, p)]
            if not candidates:
                closed.add(i)
                continue
            clat, clon = centroid(i)
            best = min(candidates, key=lambda p: haversine_km(clat, clon, p["lat"], p["lon"]))
            rest.remove(best)
            days[i].append(best)
        unscheduled.extend(rest)

    return days, unscheduled


def _order(stops: list[dict], hotel: dict) -> list[dict]:
    """Chronological by light, then nearest-first within the same band."""
    return sorted(
        stops,
        key=lambda p: (
            time_rank(p["best_time"]),
            haversine_km(hotel["lat"], hotel["lon"], p["lat"], p["lon"]),
        ),
    )


def plan_days(
    shortlist: dict[str, Any],
    stay: dict[str, Any] | None,
    flight: dict[str, Any] | None,
    brief: dict[str, Any],
    prefs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the timed day plan and price its ground costs.

    The traveler's preferences come from the scout's shortlist, which carries
    the supervisor's reading of their note, so the budget guardrail and the
    itinerary agent plan with the same reading. `prefs` overrides it.
    """
    prefs = normalize_prefs(
        prefs or shortlist.get("prefs") or parse_preferences(brief.get("nuance", ""))
    )
    tailor = Tailoring(prefs)
    travelers = max(1, int(brief.get("travelers", 1)))
    n_days = max(1, int(brief.get("nights", 3)))
    start = _parse_date(brief.get("start_date", ""))
    speed = float(shortlist.get("speed_factor", 1.0))
    food_pp = float(shortlist.get("daily_food_usd", 50.0))
    pace = prefs["pace"]
    # A researched destination says which modes exist there and what they cost
    # (geo._profile_leg). The catalog has no profile: distance bands apply.
    transport = shortlist.get("transport")

    def leg_between(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        return travel_leg(a, b, travelers=travelers, speed_factor=speed, transport=transport)

    base = shortlist.get("base_area") or {
        "name": shortlist.get("destination", "centre"),
        "lat": shortlist.get("lat", 0.0), "lon": shortlist.get("lon", 0.0),
    }
    hotel = (
        {"name": stay["name"], "lat": stay["lat"], "lon": stay["lon"]}
        if stay and stay.get("lat") else dict(base)
    )

    # ---- what the note rules out never competes for a slot
    pois: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for p in shortlist.get("pois", []):
        why_not = tailor.skip_reason(p)
        if why_not:
            skipped.append({"name": p["name"], "reason": why_not})
        else:
            pois.append(dict(p))

    # ---- the first day on the ground depends on when the plane lands
    if flight and flight.get("no_flight"):
        flight = None  # an overland trip: no landing, no airport transfer
    land = arrival_offset(flight, brief)  # days before it are spent travelling
    arrival = _arrival_minutes(flight)
    transfer = None
    day1_start = _DEFAULT_START
    if arrival is not None and shortlist.get("airport"):
        transfer = leg_between(shortlist["airport"], hotel)
        day1_start = arrival + _ARRIVAL_BUFFER_MIN + transfer["minutes"] + _CHECKIN_MIN

    suns = []
    for d in range(n_days):
        day = start + timedelta(days=d)
        suns.append(
            solar_events(
                float(shortlist.get("lat", 0.0)), float(shortlist.get("lon", 0.0)),
                day, utc_offset(shortlist, day),
            )
        )

    capacity = [pace] * n_days
    hours_left_d1 = max(0, _DAY_END - day1_start) / 60.0
    # Landing day is jet-lagged: lighter than a full day, one stop per ~3h left.
    capacity[land] = max(0, min(pace - 1, int(hours_left_d1 / 3.0)))

    # Which light-bands each day can still use.
    base_bands = set(_ALL_BANDS)
    if not prefs["early_ok"]:
        base_bands.discard("sunrise")
    allowed_bands = [set(base_bands) for _ in range(n_days)]
    allowed_bands[land].discard("sunrise")  # you are on a plane
    if day1_start > 11 * 60:
        allowed_bands[land] -= {"morning", "midday"}
    if day1_start > 16 * 60:
        allowed_bands[land] -= {"afternoon", "anytime"}
    golden_d1 = suns[land].get("_golden_pm_min")
    if golden_d1 is not None and day1_start > golden_d1 - 20:
        allowed_bands[land].discard("sunset")
    for d in range(land):  # still in the air
        capacity[d] = 0
        allowed_bands[d] = set()

    near = None  # catalog: within the day-trip radius (_near_km)
    if transport is None:
        centre = (float(shortlist.get("lat", hotel["lat"])),
                  float(shortlist.get("lon", hotel["lon"])))

        def is_trip(p: dict) -> bool:
            return haversine_km(centre[0], centre[1], p["lat"], p["lon"]) > _DAY_TRIP_KM
    else:
        def is_trip(p: dict) -> bool:
            return leg_between(hotel, p)["minutes"] > _DAY_TRIP_MIN

        def near(a: dict, b: dict) -> bool:
            return leg_between(a, b)["minutes"] <= _DAY_TRIP_MIN

    assigned, unscheduled = _assign(
        pois, n_days, capacity, hotel, tailor.tier, allowed_bands, is_trip,
        first_day=land, near=near, fit_local=transport is not None,
    )

    days_out: list[dict[str, Any]] = []
    ground_total = 0.0

    for d in range(n_days):
        day = start + timedelta(days=d)
        # On a travel day the destination's sunrise and sunset don't apply.
        sun = suns[d] if d >= land else {}
        stops = _order(assigned[d], hotel)

        items: list[dict[str, Any]] = []
        transport_usd = 0.0
        activities_usd = 0.0
        distance_km = 0.0
        travel_min = 0
        walk_km = 0.0

        if d < land and flight:
            items.append(_flight_item(flight, shortlist, d))
        if d == land and arrival is not None:
            items.append({
                "kind": "arrival",
                "start": from_minutes(arrival),
                "end": from_minutes(day1_start),
                "name": f"Land at {shortlist['airport']['name']}"
                        if shortlist.get("airport") else "Arrive",
                "note": f"{flight.get('carrier', '')} arrives {from_minutes(arrival)}; "
                        f"check in at {hotel['name']}",
                "leg": transfer,
            })
            if transfer:
                transport_usd += transfer["cost_usd"]
                distance_km += transfer["distance_km"]
                travel_min += transfer["minutes"]

        # First stop of the day: leave the hotel in time for its window.
        cursor = day1_start if d == land else _DEFAULT_START
        if stops and d > land:
            first_leg = leg_between(hotel, stops[0])
            pref, _ = window_for(stops[0]["best_time"], sun)
            cursor = int(min(_DEFAULT_START, max(4 * 60, pref - first_leg["minutes"])))
        if not prefs["early_ok"]:
            cursor = max(cursor, _LATE_START)

        prev = hotel
        lunch_done = False

        for stop in stops:
            leg = leg_between(prev, stop)
            arrive = cursor + leg["minutes"]

            pref, reason = window_for(stop["best_time"], sun)

            # Lunch goes in first if we'd otherwise run through midday without eating.
            if (not lunch_done and stop["category"] not in _FOOD_CATEGORIES
                    and cursor <= _LUNCH_LATEST
                    and max(arrive, pref) >= _LUNCH_START + _LUNCH_MIN):
                lunch_at = max(cursor, _LUNCH_START)
                items.append({
                    "kind": "lunch",
                    "start": from_minutes(lunch_at),
                    "end": from_minutes(lunch_at + _LUNCH_MIN),
                    "name": f"Lunch near {prev['name']}",
                    "note": "Budgeted in the daily food allowance",
                })
                cursor = lunch_at + _LUNCH_MIN
                arrive = cursor + leg["minutes"]
                lunch_done = True

            begin = max(arrive, pref)
            if begin > _DAY_END:
                unscheduled.append(stop)
                continue
            wait = int(max(0, begin - arrive))
            end = begin + int(stop.get("duration_min", 60))

            note = reason
            if stop["best_time"] == "sunset" and sun.get("_sunset_min") is not None \
                    and arrive > sun["_sunset_min"]:
                note = f"arrives after sunset ({sun['sunset']}); still worth it for city lights"

            cost = round(float(stop.get("cost_usd", 0.0)) * travelers, 2)
            item = {
                "kind": "stop",
                "start": from_minutes(begin),
                "end": from_minutes(end),
                "name": stop["name"],
                "category": stop["category"],
                "best_time": stop["best_time"],
                "why_this_time": note,
                "tip": stop.get("tip", ""),
                "cost_usd": cost,
                "leg": leg,
                "wait_min": wait,
            }
            for_you = tailor.reason(stop)
            if for_you:
                item["for_you"] = for_you
            if stop.get("added_by"):
                item["added_by"] = stop["added_by"]
            items.append(item)

            if stop["category"] in _FOOD_CATEGORIES and stop["best_time"] in ("midday", "morning"):
                lunch_done = True
            activities_usd += cost
            transport_usd += leg["cost_usd"]
            distance_km += leg["distance_km"]
            travel_min += leg["minutes"]
            if leg["mode"] == "walk":
                walk_km += leg["distance_km"]
            cursor = end
            prev = stop

        if (not lunch_done and prev is not hotel
                and _LUNCH_START - 45 <= cursor <= _LUNCH_LATEST):
            lunch_at = max(cursor, _LUNCH_START)
            items.append({
                "kind": "lunch",
                "start": from_minutes(lunch_at),
                "end": from_minutes(lunch_at + _LUNCH_MIN),
                "name": f"Lunch near {prev['name']}",
                "note": "Budgeted in the daily food allowance",
            })

        back = None
        if prev is not hotel:
            back = leg_between(prev, hotel)
            transport_usd += back["cost_usd"]
            distance_km += back["distance_km"]
            travel_min += back["minutes"]
            if back["mode"] == "walk":
                walk_km += back["distance_km"]

        # Nothing is spent on the ground while flying; the landing day is short.
        food_share = 0.0 if d < land else 0.6 if d == land else 1.0
        food_usd = round(food_pp * travelers * food_share, 2)
        day_total = round(activities_usd + transport_usd + food_usd, 2)
        ground_total += day_total

        named = [i["name"] for i in items if i["kind"] == "stop"]

        def bucket(lo: int, hi: int) -> str:
            hits = [i["name"] for i in items
                    if i["kind"] == "stop" and lo <= to_minutes(i["start"]) < hi]
            return ", ".join(hits) or "Free time"

        if named:
            title = " · ".join(named[:2])
        else:
            title = "Travel day" if d < land else "Arrival and settle in"
        days_out.append({
            "day": d + 1,
            "date": day.isoformat(),
            "weekday": day.strftime("%A"),
            "title": title,
            "sunrise": sun.get("sunrise"),
            "sunset": sun.get("sunset"),
            "golden_hour": sun.get("golden_evening_start"),
            "daylight": sun.get("daylight"),
            "items": items,
            "return_leg": back,
            "totals": {
                "distance_km": round(distance_km, 1),
                "travel_min": travel_min,
                "walk_km": round(walk_km, 1),
                "activities_usd": round(activities_usd, 2),
                "transport_usd": round(transport_usd, 2),
                "food_usd": food_usd,
            },
            "est_cost_usd": day_total,
            # Coarse summary kept for compact displays and older clients.
            "morning": bucket(0, 12 * 60),
            "afternoon": bucket(12 * 60, 17 * 60),
            "evening": bucket(17 * 60, 24 * 60),
        })

    scheduled = [i for day in days_out for i in day["items"] if i["kind"] == "stop"]
    tailored = {
        "note": clean_text(brief.get("nuance", ""), MAX_NOTE_CHARS),
        "understood_by": prefs["understood_by"],
        "pace": pace,
        "pace_label": PACE_LABEL.get(pace, "balanced"),
        "early_ok": prefs["early_ok"],
        "interests": [CATEGORY_LABEL.get(c, c) for c in prefs["interests"]],
        "avoid": [CATEGORY_LABEL.get(c, c) for c in prefs["avoid"]],
        "wishes": prefs["wishes"],
        "for_you": [
            {"name": i["name"], "day": day["day"], "reason": i["for_you"],
             "added": i.get("added_by") == "scout"}
            for day in days_out for i in day["items"]
            if i["kind"] == "stop" and i.get("for_you")
        ],
        "skipped": skipped,
        "unmet": tailor.unmet(scheduled, pois, str(shortlist.get("destination") or "the city")),
    }

    return {
        "days": days_out,
        "ground_cost_usd": round(ground_total, 2),
        "hotel": hotel,
        "arrival_transfer": transfer,
        "unscheduled": [p["name"] for p in unscheduled],
        "prefs": prefs,
        "tailored": tailored,
    }
