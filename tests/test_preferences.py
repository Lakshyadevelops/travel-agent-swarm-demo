"""The traveler's free-text note must visibly and honestly shape the plan."""

from __future__ import annotations

import json

import pytest
from google.adk.tools import FunctionTool

from app.agents.orchestrator import run_swarm
from app.agents.planner import plan_days
from app.agents.preferences import (
    CATEGORIES,
    INTEREST_KEYWORDS,
    clean_extra_places,
    merge_reading,
    parse_preferences,
)
from app.agents.prompts import scout_instruction
from app.agents.runtime import CURRENT_BRIEF, CURRENT_RUN_ID, CURRENT_SCRATCHPAD, CURRENT_SEED
from app.agents.schemas import WishPlace
from app.agents.scratchpad_tools import intake_tool, itinerary_tool, scout_tool
from app.providers.data import DESTINATIONS
from app.providers.tools import PROVIDER_LATENCY_MS, scout_destination
from tests.conftest import BRIEF

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


# ------------------------------------------------------------ scout extras
def _hill_prefs():
    return merge_reading(HILLS, interests=["hike"], avoid=[], wishes=["hill trek"])


def test_scout_extras_are_validated():
    raw = [
        WishPlace(**HEATH),  # as ADK hands it over when the model's JSON validates
        {"name": "Primrose Hill", "lat": 51.539, "lon": -0.1606, "for_wish": "hill trek"},
        {"name": "Eiffel Tower", "lat": 48.8584, "lon": 2.2945, "for_wish": "hill trek"},
        {"name": "Box Hill", "lat": 51.253, "lon": -0.31, "category": "volcano",
         "best_time": "noon", "duration_min": 9999, "cost_usd": -5, "tip": "t" * 500,
         "for_wish": "hills"},
        {"name": "Oxford Street", "lat": 51.5152, "lon": -0.1418, "category": "shopping",
         "for_wish": "shopping"},
        {"name": "", "lat": 51.5, "lon": -0.1},
        {"name": "Leith Hill", "lat": float("nan"), "lon": -0.37},
        {"name": "Richmond Park", "lat": 51.4425, "lon": -0.275, "category": "nature",
         "for_wish": "hill trek"},
        {"name": "Epping Forest", "lat": 51.66, "lon": 0.05, "category": "nature",
         "for_wish": "hill trek"},
        "not a place",
    ]
    accepted, rejected = clean_extra_places(
        raw, catalog=[dict(p) for p in LONDON["pois"]], centre=CENTRE, prefs=_hill_prefs())

    assert [p["name"] for p in accepted] == [HEATH["name"], "Box Hill", "Richmond Park"]
    assert all(p["added_by"] == "scout" and p["for_you"] == "hill trek" for p in accepted)
    box = accepted[1]
    assert box["category"] == "experience" and box["best_time"] == "anytime"
    assert box["duration_min"] == 480 and box["cost_usd"] == 0 and len(box["tip"]) == 140

    reasons = {r["name"]: r["reason"] for r in rejected}
    assert reasons["Primrose Hill"] == "already on the list"
    assert "too far" in reasons["Eiffel Tower"]
    assert "not tied" in reasons["Oxford Street"]
    assert reasons["Leith Hill"] == "invalid coordinates"
    assert "only 3" in reasons["Epping Forest"]
    assert len(rejected) == 7


def test_scout_extras_need_something_to_serve():
    accepted, rejected = clean_extra_places(
        [HEATH], catalog=[], centre=CENTRE, prefs=merge_reading(""))
    assert accepted == []
    assert rejected[0]["reason"] == "the traveler stated no preferences to serve"


def test_scout_extras_respect_skips():
    prefs = merge_reading("museums, no nightlife", interests=["museum"], avoid=["nightlife"])
    accepted, rejected = clean_extra_places(
        [{"name": "Ronnie Scott's", "lat": 51.5134, "lon": -0.1318, "category": "nightlife"}],
        catalog=[], centre=CENTRE, prefs=prefs)
    assert accepted == [] and "skip nightlife" in rejected[0]["reason"]


# ------------------------------------------------------------ tools
class MemPad:
    """Just enough of a ScratchpadStore; values round-trip through JSON like the real ones."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def write(self, run_id, field, value):
        self.rows[(run_id, field)] = json.dumps(value)

    async def read(self, run_id, field):
        raw = self.rows.get((run_id, field))
        return None if raw is None else json.loads(raw)

    async def read_all(self, run_id):
        return {f: json.loads(v) for (r, f), v in self.rows.items() if r == run_id}


def _bind(brief: dict) -> MemPad:
    """Bind the run context. Each async test runs in its own task, so this can't leak."""
    pad = MemPad()
    CURRENT_SCRATCHPAD.set(pad)
    CURRENT_RUN_ID.set("t")
    CURRENT_SEED.set("seed")
    CURRENT_BRIEF.set(brief)
    PROVIDER_LATENCY_MS.set(0)
    return pad


async def test_intake_and_scout_carry_the_reading_onto_the_blackboard():
    pad = _bind({"destination": "London", "nuance": HILLS, "nights": 4, "travelers": 2})
    constraints = await intake_tool(interests=["hike", "nature"], avoid=[], wishes=["hill trek"])
    assert constraints["understood_by"] == "supervisor"
    assert constraints["wishes"] == ["hill trek"]

    out = await scout_tool(extra_places=[
        WishPlace(**HEATH), {"name": "Eiffel Tower", "lat": 48.8584, "lon": 2.2945}])
    shortlist = await pad.read("t", "destination_shortlist")
    assert shortlist["prefs"]["wishes"] == ["hill trek"]
    assert shortlist["pois"][0]["name"] == HEATH["name"]  # wishes first
    assert [p["name"] for p in shortlist["pois"] if p.get("added_by")] == [HEATH["name"]]
    assert out["added_for_traveler"] == [f"{HEATH['name']} (for hill trek)"]
    assert out["rejected_suggestions"][0].startswith("Eiffel Tower:")

    summary = await itinerary_tool()
    itinerary = await pad.read("t", "itinerary")
    # Named once as added -- not a second time as "prioritised".
    assert itinerary["collaboration"]["scout"].count(HEATH["name"]) == 1
    assert itinerary["tailored"]["wishes"] == ["hill trek"]
    assert any("added by the scout" in x
               for x in summary["tailored_to_note"]["picked_for_traveler"])


def test_tool_declarations_offer_the_vocabulary():
    intake = FunctionTool(intake_tool)._get_declaration().parameters_json_schema
    assert set(intake["properties"]) == {"interests", "avoid", "pace", "early_starts_ok", "wishes"}
    assert "hike" in json.dumps(intake["properties"]["interests"])
    scout = FunctionTool(scout_tool)._get_declaration().parameters_json_schema
    assert "WishPlace" in json.dumps(scout)


def test_scout_instruction_lists_the_destination_catalog():
    class Ctx:
        state = {"brief": {"destination": "London"}}

    token = CURRENT_BRIEF.set({})
    try:
        text = scout_instruction(Ctx())
    finally:
        CURRENT_BRIEF.reset(token)
    assert "Destination & Vibe Scout for London" in text
    assert "- Primrose Hill (viewpoint, best at sunrise)" in text
    assert "{city}" not in text and "{catalog}" not in text


# ------------------------------------------------------------ planner
async def _shortlist(prefs: dict, extras=()) -> dict:
    s = (await scout_destination("London", "seed")).model_dump()
    accepted, _ = clean_extra_places(
        list(extras), catalog=s["pois"], centre=(s["lat"], s["lon"]), prefs=prefs)
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
