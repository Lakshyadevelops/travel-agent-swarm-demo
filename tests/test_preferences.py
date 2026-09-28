"""The traveler's free-text note must visibly and honestly shape the plan."""

from __future__ import annotations

import json

import pytest
from google.adk.tools import FunctionTool

from app.agents import prompts
from app.agents.orchestrator import run_swarm
from app.agents.planner import plan_days
from app.agents.preferences import (
    CATEGORIES,
    INTEREST_KEYWORDS,
    clean_places,
    merge_reading,
    parse_preferences,
)
from app.agents.scratchpad_tools import intake_tool, itinerary_tool, scout_tool
from app.providers.data import DESTINATIONS
from app.providers.tools import scout_destination
from tests.conftest import BRIEF
from tests.helpers import bind as _bind

LONDON = DESTINATIONS["london"]
CENTRE = (LONDON["lat"], LONDON["lon"])
HILLS = "I love hills and would like to go on a trek"
ARRIVAL = {"arrive": "2026-10-10 13:30", "carrier": "BA"}
HEATH = {
    "name": "Hampstead Heath & Parliament Hill", "lat": 51.5608, "lon": -0.1636,
    "category": "hike", "best_time": "morning", "duration_min": 150, "cost_usd": 0,
    "tip": "Climb Parliament Hill for the skyline", "for_wish": "hill trek",
}
BRIGHTON = {
    "name": "Brighton beach & Palace Pier", "lat": 50.8194, "lon": -0.1363,
    "category": "beach", "best_time": "afternoon", "duration_min": 240, "cost_usd": 0,
    "tip": "Fast trains from Victoria", "for_wish": "beach day",
}


# ------------------------------------------------------------ keyword reader
@pytest.mark.parametrize("note, liked, avoided", [
    (HILLS, {"hike"}, set()),
    ("I love museums and beach", {"museum", "beach"}, set()),
    ("love food, no museums", {"food"}, {"museum"}),
    ("no nightlife and lots of museums", {"museum"}, {"nightlife"}),
    ("not really into museums", set(), {"museum"}),
    ("don't like bars", set(), {"nightlife"}),
    ("museums are not my thing, love parks", {"nature"}, {"museum"}),
    ("love everything except museums", set(), {"museum"}),
])
def test_keyword_reader_handles_synonyms_and_negation(note, liked, avoided):
    p = parse_preferences(note)
    assert set(p["interests"]) == liked
    assert set(p["avoid"]) == avoided


def test_keyword_reader_matches_whole_words_only():
    p = parse_preferences("start early this season, walkable neighborhoods, nonstop flight")
    # Not "art" in start, "sea" in season, "walk" in walkable, or a packed pace.
    assert p["interests"] == ["neighborhood"]
    assert p["avoid"] == [] and p["pace"] == 4 and p["early_ok"] is True


@pytest.mark.parametrize("note", [
    "no early starts please", "not a morning person", "we like a lie-in",
    "late starts, food markets",
])
def test_keyword_reader_hears_late_starts(note):
    assert parse_preferences(note)["early_ok"] is False


def test_a_late_start_is_not_a_skip():
    p = parse_preferences("no early starts and love food markets; no sunrise alarms")
    assert p["early_ok"] is False
    assert p["avoid"] == []
    assert {"food", "market"} <= set(p["interests"])


@pytest.mark.parametrize("note, pace", [
    ("a relaxed trip with the kids", 3),
    ("packed schedule, see it all", 5),
    ("nothing too packed", 3),
    ("", 4),
])
def test_keyword_reader_pace(note, pace):
    assert parse_preferences(note)["pace"] == pace


def test_keyword_vocabulary_is_the_tool_vocabulary():
    assert set(INTEREST_KEYWORDS) <= set(CATEGORIES)


# ------------------------------------------------------------ model reading
def test_model_reading_wins_and_is_validated():
    p = merge_reading(
        HILLS, interests=["hike", "nature", "volcano", "hike"], avoid=[], pace="relaxed",
        early_starts_ok=False,
        wishes=["hill trek", "Hill Trek", "   ", "x" * 100, "sunrise run", "pub quiz", "extra"],
    )
    assert p["interests"] == ["hike", "nature"]
    assert p["avoid"] == []
    assert p["pace"] == 3 and p["early_ok"] is False
    assert p["wishes"] == ["hill trek", "x" * 60, "sunrise run", "pub quiz"]
    assert p["understood_by"] == "supervisor"


def test_missing_or_invalid_model_fields_fall_back_to_keywords():
    p = merge_reading("love museums, no nightlife", interests=["volcano"])
    assert p["interests"] == ["museum"] and p["avoid"] == ["nightlife"]
    assert p["understood_by"] == "keywords"


def test_model_cannot_invent_preferences_without_a_note():
    p = merge_reading("", interests=["museum"], wishes=["pub crawl"], pace="packed")
    assert p["interests"] == [] and p["wishes"] == [] and p["pace"] == 4


def test_a_skip_in_the_note_outlasts_a_model_slip():
    # The model listed museums as an interest and omitted `avoid`; the note said no.
    p = merge_reading("not into museums", interests=["museum"])
    assert p["interests"] == [] and p["avoid"] == ["museum"]


# ------------------------------------------------------------ researched places
def _hill_prefs():
    return merge_reading(HILLS, interests=["hike"], avoid=[], wishes=["hill trek"])


def test_researched_places_are_validated_and_tagged_for_wishes():
    raw = [
        HEATH,
        {"name": "Primrose Hill", "lat": 51.539, "lon": -0.1606, "for_wish": "hill trek"},
        {"name": "Eiffel Tower", "lat": 48.8584, "lon": 2.2945, "for_wish": "hill trek"},
        {"name": "Box Hill", "lat": 51.253, "lon": -0.31, "category": "volcano",
         "best_time": "noon", "duration_min": 9999, "cost_usd": -5, "tip": "t" * 500,
         "for_wish": "hills"},
        {"name": "Oxford Street", "lat": 51.5152, "lon": -0.1418, "category": "shopping",
         "for_wish": ""},
        {"name": "", "lat": 51.5, "lon": -0.1},
        {"name": "Leith Hill", "lat": float("nan"), "lon": -0.37},
        {"name": "Richmond Park", "lat": 51.4425, "lon": -0.275, "category": "nature",
         "for_wish": "«hill trek»"},  # the prompt quotes wishes; the model may copy the marks
        "not a place",
    ]
    accepted, rejected = clean_places(
        raw, centre=CENTRE, prefs=_hill_prefs(), known=[dict(p) for p in LONDON["pois"]])

    assert [p["name"] for p in accepted] == [
        HEATH["name"], "Box Hill", "Oxford Street", "Richmond Park"]
    assert {p["name"]: p.get("for_you") for p in accepted} == {
        HEATH["name"]: "hill trek", "Box Hill": "hill trek", "Oxford Street": None,
        "Richmond Park": "hill trek"}
    assert all(p.get("added_by") == ("scout" if p.get("for_you") else None) for p in accepted)
    box = accepted[1]
    assert box["category"] == "experience" and box["best_time"] == "anytime"
    assert box["duration_min"] == 480 and box["cost_usd"] == 0 and len(box["tip"]) == 140

    reasons = {r["name"]: r["reason"] for r in rejected}
    assert reasons["Primrose Hill"] == "already on the list"
    assert "too far" in reasons["Eiffel Tower"]
    assert reasons["Leith Hill"] == "invalid coordinates"
    assert reasons["(unnamed)"] == "not a named place"
    assert len(rejected) == 5


def test_researched_places_need_no_preferences_and_are_capped():
    places = [dict(HEATH, name=f"Place {i}", lat=51.50 + i * 0.01) for i in range(5)]
    accepted, rejected = clean_places(places, centre=CENTRE, prefs=merge_reading(""), limit=3)
    assert [p["name"] for p in accepted] == ["Place 0", "Place 1", "Place 2"]
    assert not any("for_you" in p or "added_by" in p for p in accepted)
    assert [r["reason"] for r in rejected] == ["only 3 places are kept"] * 2


def test_researched_places_respect_skips_unless_wished_for():
    prefs = merge_reading("live jazz, no nightlife", interests=[], avoid=["nightlife"],
                          wishes=["live jazz"])
    accepted, rejected = clean_places(
        [{"name": "Ronnie Scott's", "lat": 51.5134, "lon": -0.1318, "category": "nightlife",
          "for_wish": "live jazz"},
         {"name": "Soho pub crawl", "lat": 51.5136, "lon": -0.1365, "category": "nightlife"}],
        centre=CENTRE, prefs=prefs)
    assert [(p["name"], p["for_you"]) for p in accepted] == [("Ronnie Scott's", "live jazz")]
    assert "skip nightlife" in rejected[0]["reason"]


def test_neighbouring_sights_are_not_duplicates():
    ubud = (-8.5069, 115.2625)
    accepted, rejected = clean_places([
        {"name": "Ubud Palace", "lat": -8.5066, "lon": 115.2625},
        {"name": "Ubud Art Market", "lat": -8.5074, "lon": 115.2631},  # across the street
        {"name": "Sacred Monkey Forest Sanctuary", "lat": -8.5188, "lon": 115.2585},
        {"name": "Ubud Monkey Forest", "lat": -8.5180, "lon": 115.2590},  # the same, renamed
    ], centre=ubud, prefs=merge_reading(""))
    assert [p["name"] for p in accepted] == [
        "Ubud Palace", "Ubud Art Market", "Sacred Monkey Forest Sanctuary"]
    assert rejected == [{"name": "Ubud Monkey Forest", "reason": "already on the list"}]


# ------------------------------------------------------------ tools
async def test_intake_and_scout_carry_the_reading_onto_the_blackboard():
    pad = _bind({"destination": "London", "nuance": HILLS, "nights": 4, "travelers": 2})
    constraints = await intake_tool(interests=["hike", "nature"], avoid=[], wishes=["hill trek"])
    assert constraints["understood_by"] == "supervisor"
    assert constraints["wishes"] == ["hill trek"]
    assert "place" not in constraints  # the scripted model: catalog, no research

    out = await scout_tool()
    shortlist = await pad.read("t", "destination_shortlist")
    assert shortlist["prefs"]["wishes"] == ["hill trek"]
    assert shortlist["inputs_from"] == ["supervisor: trip_constraints"]
    # The catalog adds nothing for wishes; live research does (test_research.py).
    assert shortlist["added_for_you"] == [] and out["added_for_traveler"] == []
    assert "source" not in shortlist and "grounding" not in shortlist

    await itinerary_tool()
    itinerary = await pad.read("t", "itinerary")
    assert itinerary["tailored"]["wishes"] == ["hill trek"]


def test_tool_declarations_offer_the_vocabulary():
    intake = FunctionTool(intake_tool)._get_declaration().parameters_json_schema
    assert set(intake["properties"]) == {"interests", "avoid", "pace", "early_starts_ok", "wishes"}
    assert "hike" in json.dumps(intake["properties"]["interests"])
    # The scout takes nothing from the model: it reads the reading off the blackboard.
    scout = FunctionTool(scout_tool)._get_declaration().parameters_json_schema
    assert not (scout or {}).get("properties")


def test_instructions_are_static_text():
    """ADK fills {placeholders} in string instructions from session state."""
    for name in ("SUPERVISOR_INTAKE", "SCOUT", "TRANSIT", "STAY", "BUDGET", "ITINERARY",
                 "SUPERVISOR_FINAL"):
        text = getattr(prompts, name)
        assert isinstance(text, str) and "{" not in text and "}" not in text, name


# ------------------------------------------------------------ planner
async def _shortlist(prefs: dict, extras=()) -> dict:
    s = (await scout_destination("London", "seed")).model_dump()
    accepted, _ = clean_places(list(extras), centre=(s["lat"], s["lon"]), prefs=prefs,
                               known=s["pois"])
    s["pois"] = accepted + s["pois"]
    s["prefs"] = prefs
    return s


def _brief(note: str, nights: int = 4) -> dict:
    return {"destination": "London", "start_date": "2026-10-10", "nights": nights,
            "travelers": 2, "nuance": note}


def _stops(plan: dict) -> list[tuple[int, dict]]:
    return [(d["day"], i) for d in plan["days"] for i in d["items"] if i["kind"] == "stop"]


async def test_planner_leaves_out_what_the_note_skips():
    note = "love museums, no nightlife"
    plan = plan_days(await _shortlist(merge_reading(note)), None, ARRIVAL, _brief(note))
    names = [i["name"] for _, i in _stops(plan)]
    assert "West End show" not in names and "West End show" not in plan["unscheduled"]
    assert plan["tailored"]["skipped"] == [
        {"name": "West End show", "reason": "you asked to skip nightlife"}]
    tags = {i["name"]: i.get("for_you") for _, i in _stops(plan)}
    assert tags["British Museum"] == "museums" and tags["Tate Modern"] == "museums"


async def test_planner_schedules_wish_places_and_day_trips():
    note = "I love hills and the seaside"
    prefs = merge_reading(note, interests=["hike", "beach"], avoid=[],
                          wishes=["hill trek", "beach day"])
    plan = plan_days(await _shortlist(prefs, [HEATH, BRIGHTON]), None, ARRIVAL, _brief(note))
    where = {i["name"]: (day, i) for day, i in _stops(plan)}

    _, heath = where[HEATH["name"]]
    assert heath["for_you"] == "hill trek" and heath["added_by"] == "scout"
    trip_day, brighton = where[BRIGHTON["name"]]
    assert trip_day != 1 and brighton["leg"]["mode"] == "rail"
    daytime = [i for d, i in _stops(plan)
               if d == trip_day and i["best_time"] not in ("sunset", "evening")]
    assert len(daytime) <= 2  # the trip is the day's main event

    t = plan["tailored"]
    assert {x["name"] for x in t["for_you"] if x["added"]} == {HEATH["name"], BRIGHTON["name"]}
    assert t["unmet"] == []


async def test_planner_is_honest_about_what_it_could_not_include():
    note = "I love museums and beach"  # keyword reading, no scout extras: benchmark mode
    plan = plan_days(await _shortlist(merge_reading(note)), None, ARRIVAL, _brief(note))
    assert plan["tailored"]["unmet"] == [
        {"what": "beaches", "reason": "no good match near London this time"}]


async def test_interests_decide_what_gets_in_when_days_are_short():
    plain = plan_days(await _shortlist(merge_reading("relaxed")), None, ARRIVAL,
                      _brief("relaxed", nights=2))
    assert "Borough Market" not in {i["name"] for _, i in _stops(plain)}

    note = "relaxed, love food markets"
    plan = plan_days(await _shortlist(merge_reading(note)), None, ARRIVAL, _brief(note, nights=2))
    stops = {i["name"]: i for _, i in _stops(plan)}
    assert stops["Borough Market"]["for_you"] == "markets"


async def test_no_early_starts():
    note = "no early starts"
    plan = plan_days(await _shortlist(merge_reading(note)), None, ARRIVAL, _brief(note))
    assert all(i["start"] >= "09:30" for _, i in _stops(plan))
    assert any(s["name"] == "Primrose Hill" for s in plan["tailored"]["skipped"])


# ------------------------------------------------------------ whole swarm
async def test_fake_run_publishes_the_tailoring():
    brief = dict(BRIEF, nuance="love food markets, no nightlife")
    r = await run_swarm(brief, "valkey", llm_mode="fake", provider_latency_ms=0)
    board = r["scratchpad"]
    assert board["destination_shortlist"]["prefs"]["avoid"] == ["nightlife"]
    t = board["itinerary"]["tailored"]
    assert t["skipped"] == [{"name": "Fado night in Alfama", "reason": "you asked to skip nightlife"}]
    assert any(x["name"] == "Time Out Market" for x in t["for_you"])
    # Budget and itinerary planned with the same reading, so they priced the same plan.
    assert board["itinerary"]["total_estimate_usd"] == board["budget_verdict"]["total_estimate_usd"]


async def test_the_note_does_not_change_store_traffic():
    """Benchmarks compare stores, so reading the note must not add or drop ops."""
    runs = []
    for note in ("", "love food markets, no nightlife, no early starts"):
        runs.append(await run_swarm(dict(BRIEF, nuance=note), "postgres",
                                    llm_mode="fake", provider_latency_ms=0))
    assert runs[0]["budget_rounds"] == runs[1]["budget_rounds"]
    counts = [{op: v["n"] for op, v in r["ops_by_type"].items()} for r in runs]
    assert counts[0] == counts[1]
