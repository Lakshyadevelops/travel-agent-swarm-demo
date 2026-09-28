"""What the traveler's free-text note asks for, and how each place answers it.

The note ("I love hills and would like to go on a trek") is read once, at
intake. The supervisor model translates it into structured arguments for
`intake_tool`; a keyword reader fills any field the model leaves out, and is the
whole reading in benchmark mode, where the model is a stub.

The reading then travels on the blackboard:

    trip_constraints        supervisor's reading of the note
      -> scout              on the live model, researches places for each wish
    destination_shortlist   carries the reading as "prefs"
      -> budget, itinerary  both plan with it, so their totals agree

Everything a model supplies is validated here before it reaches the blackboard:
categories against a fixed vocabulary, text lengths and counts, and proposed
coordinates against the destination.
"""

from __future__ import annotations

import math
import re
from typing import Any, get_args

from pydantic import BaseModel

from app.agents.schemas import BestTime, Category, Pace
from app.providers.geo import haversine_km

CATEGORIES: tuple[str, ...] = get_args(Category)
BEST_TIMES: tuple[str, ...] = get_args(BestTime)
PACES: dict[str, int] = {"relaxed": 3, "balanced": 4, "packed": 5}
PACE_LABEL: dict[int, str] = {v: k for k, v in PACES.items()}
DEFAULT_PACE = PACES["balanced"]
assert set(PACES) == set(get_args(Pace))

# How each category reads in a sentence: "into museums, beaches".
CATEGORY_LABEL: dict[str, str] = {
    "landmark": "landmarks", "museum": "museums", "viewpoint": "viewpoints",
    "nature": "nature", "hike": "hiking", "beach": "beaches", "market": "markets",
    "food": "food", "nightlife": "nightlife", "neighborhood": "neighborhoods",
    "experience": "experiences", "shopping": "shopping",
}

# An interest in one of these is served by the other: someone who loves hills is
# well served by a big park, a food lover by a market. Skips stay exact, so "no
# hiking" does not cost the traveler the parks.
_RELATED: dict[str, frozenset[str]] = {
    "nature": frozenset({"nature", "hike"}),
    "hike": frozenset({"hike", "nature"}),
    "market": frozenset({"market", "food"}),
    "food": frozenset({"food", "market"}),
}


def related(category: str) -> frozenset[str]:
    return _RELATED.get(category, frozenset({category}))


# ------------------------------------------------------------ keyword reader
def _words(*words: str, plural: bool = True) -> re.Pattern[str]:
    """Whole words or phrases; with `plural`, "museum" also matches "museums"."""
    alts = sorted({re.escape(w) for w in words}, key=len, reverse=True)
    suffix = r"(?:s|es)?" if plural else ""
    return re.compile(r"\b(?:" + "|".join(alts) + r")" + suffix + r"\b")


# Whole words only, so "art" does not fire on "start" or "sea" on "season".
INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "landmark": ("landmark", "sight", "sightseeing", "iconic", "monument", "temple",
                 "castle", "palace", "cathedral", "church", "architecture"),
    "museum": ("museum", "art", "history", "gallery", "galleries", "culture", "exhibition"),
    "viewpoint": ("view", "photo", "photography", "sunset", "sunrise", "skyline",
                  "panorama", "lookout", "rooftop", "miradouro"),
    "nature": ("nature", "park", "outdoor", "outdoors", "garden", "forest", "lake",
               "countryside", "wildlife", "green space"),
    "hike": ("hike", "hiking", "trek", "trekking", "trail", "hill", "hilly", "mountain",
             "climb", "climbing", "hillwalking"),
    "beach": ("beach", "sea", "seaside", "coast", "coastal", "ocean", "surf", "swim", "swimming"),
    "market": ("market", "street food", "food hall", "bazaar"),
    "food": ("food", "foodie", "eat", "eating", "dinner", "culinary", "cuisine",
             "restaurant", "pastry", "pastries", "bakery", "bakeries", "tapas", "sushi"),
    "nightlife": ("nightlife", "night out", "bar", "pub", "club", "clubbing", "music",
                  "show", "fado", "concert", "theatre", "theater", "jazz", "cocktail"),
    "neighborhood": ("neighborhood", "neighbourhood", "walkable", "stroll", "wander",
                     "street art", "local life"),
    "shopping": ("shop", "shopping", "boutique", "vintage", "souvenir"),
    "experience": ("tour", "cruise", "boat", "cooking class", "workshop", "tram"),
}
_INTEREST_RES = {cat: _words(*words) for cat, words in INTEREST_KEYWORDS.items()}

_LATE_START = _words(
    "no early", "no early start", "no early morning", "not a morning person",
    "not an early riser", "late riser", "late start", "sleep in", "sleeping in",
    "lie in", "lie-in", "sleep late", "no sunrise", "hate mornings", "slow mornings",
    "no alarm",
)
_RELAXED = _words(
    "relax", "relaxed", "relaxing", "slow", "slower", "take it easy", "easygoing",
    "chill", "leisurely", "laid back", "laid-back", "unhurried", "no rush", "no hurry",
    "not rushed", "kid", "children", "toddler", "baby", "elderly", "grandparent",
    "downtime",
)
_PACKED = _words(
    "packed", "jam-packed", "action-packed", "busy", "intense", "max", "maximum",
    "as much as possible", "see everything", "do everything", "see it all", "whirlwind",
)
_NEGATION = _words(
    "no", "not", "never", "avoid", "avoids", "avoiding", "skip", "skips", "skipping",
    "hate", "hates", "dislike", "dislikes", "without", "except", "don't", "dont",
    "do not", "doesn't", "won't", "isn't", "aren't", "nothing", "none", "neither",
    "nor", "rather not", plural=False,
)
_POSITIVE = _words(
    "love", "loves", "like", "likes", "want", "wants", "enjoy", "enjoys", "into",
    "keen", "prefer", "lots of", "plenty of", "fan of", "interested in", "must",
    "would like", "hoping", "looking for", "adore", plural=False,
)
# "museums are not my thing": the dismissal comes after what it dismisses.
_DISMISSIVE = _words(
    "not my thing", "not our thing", "not for me", "not for us", "not my cup of tea",
    "isn't my thing", "aren't my thing", "isn't for us", "aren't for us", plural=False,
)
# Words that may sit between a negation and a positive verb without breaking
# it: "don't like", "not a big fan of", "not really into".
_FILLERS = frozenset({
    "a", "an", "the", "really", "too", "very", "big", "huge", "great", "much", "so",
    "that", "at", "all", "any", "to", "i", "we", "do", "particularly", "especially",
    "overly", "super",
})
_CLAUSES = re.compile(r"[.,;:!?\n()/]+|\b(?:but|however|although|though|whereas)\b")


def _negation_marks(clause: str) -> list[tuple[int, bool]]:
    """Positions in a clause where negation switches on (True) or off (False)."""
    cues = sorted(
        [(m.start(), m.end(), True) for m in _NEGATION.finditer(clause)]
        + [(m.start(), m.end(), False) for m in _POSITIVE.finditer(clause)]
    )
    marks: list[tuple[int, bool]] = []
    last_negation_end: int | None = None
    for start, end, is_negation in cues:
        if is_negation:
            marks.append((start, True))
            last_negation_end = end
            continue
        if last_negation_end is not None:
            between = clause[last_negation_end:start].split()
            if len(between) <= 3 and all(w in _FILLERS for w in between):
                continue  # "don't like": still negated
        marks.append((start, False))
        last_negation_end = None
    return marks


def _negated_at(marks: list[tuple[int, bool]], pos: int) -> bool:
    state = False
    for at, negated in marks:
        if at > pos:
            break
        state = negated
    return state


def parse_preferences(nuance: str) -> dict[str, Any]:
    """Keyword reading of the note: pace, interests, skips and early starts.

    Negation is per clause ("love food, no museums"; "no bars and lots of
    museums"), so a skip is never mistaken for an interest. It cannot find
    wishes -- specific requests like "hill trek" need the model.
    """
    text = " ".join((nuance or "").lower().replace("\u2019", "'").split())

    early_ok = not _LATE_START.search(text)
    # Remove before reading interests: "no sunrise starts" is not "no viewpoints".
    text = _LATE_START.sub(" ", text)
    relaxed = bool(_RELAXED.search(text))
    text = _RELAXED.sub(" ", text)  # "no rush" must not negate what follows

    liked: set[str] = set()
    avoided: set[str] = set()
    packed = not_packed = False
    for clause in _CLAUSES.split(text):
        if not clause.strip():
            continue
        marks = _negation_marks(clause)
        dismissed = bool(_DISMISSIVE.search(clause))
        for cat, rx in _INTEREST_RES.items():
            for m in rx.finditer(clause):
                if dismissed or _negated_at(marks, m.start()):
                    avoided.add(cat)
                else:
                    liked.add(cat)
        for m in _PACKED.finditer(clause):
            if dismissed or _negated_at(marks, m.start()):
                not_packed = True  # "nothing too packed"
            else:
                packed = True

    both = liked & avoided  # contradictory; a keyword reader can't tell, so neither
    if relaxed or not_packed:
        pace = PACES["relaxed"]
    elif packed:
        pace = PACES["packed"]
    else:
        pace = DEFAULT_PACE
    return {
        "pace": pace,
        "interests": sorted(liked - both),
        "avoid": sorted(avoided - both),
        "early_ok": early_ok,
        "wishes": [],
        "understood_by": "keywords",
    }


# ------------------------------------------------------------ coercion
MAX_WISHES = 4
MAX_WISH_CHARS = 60
MAX_NOTE_CHARS = 1000

_CATEGORY_ALIASES = {
    "hiking": "hike", "trek": "hike", "trekking": "hike", "hill": "hike", "hills": "hike",
    "park": "nature", "parks": "nature", "outdoors": "nature", "outdoor": "nature",
    "garden": "nature", "gardens": "nature", "view": "viewpoint", "views": "viewpoint",
    "art": "museum", "history": "museum", "bar": "nightlife", "bars": "nightlife",
    "restaurant": "food", "restaurants": "food", "shop": "shopping", "shops": "shopping",
    "neighbourhood": "neighborhood", "neighbourhoods": "neighborhood",
    "sea": "beach", "coast": "beach", "seaside": "beach",
}
_PACE_ALIASES = {
    "slow": "relaxed", "easy": "relaxed", "leisurely": "relaxed", "moderate": "balanced",
    "normal": "balanced", "medium": "balanced", "fast": "packed", "busy": "packed",
    "intense": "packed", "full": "packed",
}


def clean_text(value: Any, limit: int) -> str:
    """Printable, single-spaced, at most `limit` characters."""
    raw = "" if value is None else str(value)
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split())
    return text[:limit].rstrip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return re.split(r"[,;]", value)
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def to_category(value: Any) -> str | None:
    key = clean_text(value, 40).lower()
    key = _CATEGORY_ALIASES.get(key, key)
    if key in CATEGORIES:
        return key
    for suffix in ("es", "s"):
        if key.endswith(suffix) and key[: -len(suffix)] in CATEGORIES:
            return key[: -len(suffix)]
    return None


def to_categories(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        cat = to_category(item)
        if cat and cat not in out:
            out.append(cat)
    return out


def to_pace(value: Any) -> int | None:
    key = clean_text(value, 20).lower()
    return PACES.get(_PACE_ALIASES.get(key, key))


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    key = clean_text(value, 10).lower()
    if key in ("true", "yes", "1"):
        return True
    if key in ("false", "no", "0"):
        return False
    return None


def clean_wishes(value: Any) -> list[str]:
    """At most MAX_WISHES short, distinct phrases that contain a letter."""
    out: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        text = clean_text(item, MAX_WISH_CHARS)
        if not re.search(r"[^\W\d_]", text) or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) == MAX_WISHES:
            break
    return out


def normalize_prefs(prefs: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce a stored reading (from the blackboard) into the planner's shape."""
    p = prefs if isinstance(prefs, dict) else {}
    try:
        pace = min(PACES["packed"], max(PACES["relaxed"], int(p.get("pace", DEFAULT_PACE))))
    except (TypeError, ValueError):
        pace = to_pace(p.get("pace")) or DEFAULT_PACE
    avoid = to_categories(p.get("avoid"))
    return {
        "pace": pace,
        "interests": [c for c in to_categories(p.get("interests")) if c not in avoid],
        "avoid": avoid,
        "early_ok": to_bool(p.get("early_ok")) is not False,
        "wishes": clean_wishes(p.get("wishes")),
        "understood_by": "supervisor" if p.get("understood_by") == "supervisor" else "keywords",
    }


def merge_reading(
    note: str,
    *,
    interests: Any = None,
    avoid: Any = None,
    pace: Any = None,
    early_starts_ok: Any = None,
    wishes: Any = None,
) -> dict[str, Any]:
    """The supervisor model's reading of the note, per field, over the keywords.

    A field the model supplies (and that survives validation) wins; any other
    field keeps the keyword reading. With no note there is nothing to read, so
    whatever the model supplied is ignored rather than trusted.
    """
    reading = parse_preferences(note)
    if not clean_text(note, MAX_NOTE_CHARS):
        return reading
    from_model = False
    for key, value in (("interests", interests), ("avoid", avoid)):
        if value is None:
            continue
        cats = to_categories(value)
        if cats or not _as_list(value):  # an explicit [] means "none"
            reading[key] = cats
            from_model = True
    if (p := to_pace(pace)) is not None:
        reading["pace"] = p
        from_model = True
    if (e := to_bool(early_starts_ok)) is not None:
        reading["early_ok"] = e
        from_model = True
    if wishes is not None:
        reading["wishes"] = clean_wishes(wishes)
        from_model = True
    reading["interests"] = [c for c in reading["interests"] if c not in reading["avoid"]]
    reading["understood_by"] = "supervisor" if from_model else "keywords"
    return reading


def describe(prefs: dict[str, Any]) -> str:
    """One line for the progress view: "relaxed pace, up to 3 stops a day · …"."""
    parts = [f"{PACE_LABEL.get(prefs['pace'], 'balanced')} pace, "
             f"up to {prefs['pace']} stops a day"]
    labels = [CATEGORY_LABEL.get(c, c) for c in prefs["interests"]]
    parts.append("into " + ", ".join(labels) if labels else "general sightseeing")
    if prefs["wishes"]:
        parts.append("wishes: " + ", ".join(prefs["wishes"]))
    if prefs["avoid"]:
        parts.append("skipping " + ", ".join(CATEGORY_LABEL.get(c, c) for c in prefs["avoid"]))
    if not prefs["early_ok"]:
        parts.append("no early starts")
    return " · ".join(parts)


# ------------------------------------------------------------ per place
def wish_profile(wish: str) -> tuple[frozenset[str], frozenset[str] | None]:
    """Categories (and times of day) that satisfy a wish without a special trip."""
    text = wish.lower()
    cats = frozenset(parse_preferences(wish)["interests"])
    if re.search(r"\b(?:sunrise|dawn)\b", text):
        times: frozenset[str] | None = frozenset({"sunrise"})
    elif re.search(r"\b(?:sunset|dusk|golden hour)\b", text):
        times = frozenset({"sunset"})
    elif re.search(r"\b(?:night|evening|after dark)\b", text):
        times = frozenset({"sunset", "evening"})
    else:
        times = None
    return cats, times


class Tailoring:
    """How one traveler's reading applies to places: skip, priority, and why."""

    def __init__(self, prefs: dict[str, Any]) -> None:
        self.interests: list[str] = list(prefs.get("interests", []))
        self.avoid: set[str] = set(prefs.get("avoid", []))
        self.wishes: list[str] = list(prefs.get("wishes", []))
        self.early_ok: bool = prefs.get("early_ok", True) is not False
        self.wanted: set[str] = set().union(*(related(c) for c in self.interests))
        self._profiles = [(w, *wish_profile(w)) for w in self.wishes]

    def reason(self, place: dict[str, Any]) -> str | None:
        """Why this place is in the plan for this traveler; None if not specifically."""
        if place.get("added_by") == "scout" and place.get("for_you"):
            return str(place["for_you"])
        cat, when = place.get("category"), place.get("best_time")
        for wish, cats, times in self._profiles:
            if cat in cats and (times is None or when in times):
                return wish
        if cat in self.wanted:
            return CATEGORY_LABEL.get(cat, cat)
        return None

    def tier(self, place: dict[str, Any]) -> int:
        """0: added for a wish, 1: matches the note, 2: everything else."""
        if place.get("added_by") == "scout":
            return 0
        return 1 if self.reason(place) else 2

    def skip_reason(self, place: dict[str, Any]) -> str | None:
        if not self.early_ok and place.get("best_time") == "sunrise":
            return "sunrise spot, and you'd rather not start early"
        cat = place.get("category")
        # A place added for an explicit wish outranks a blanket skip.
        if cat in self.avoid and place.get("added_by") != "scout":
            return f"you asked to skip {CATEGORY_LABEL.get(cat, cat)}"
        return None

    def unmet(
        self, scheduled: list[dict[str, Any]], candidates: list[dict[str, Any]], city: str
    ) -> list[dict[str, str]]:
        """Wishes and interests the plan does not answer, and why."""

        def why(possible: bool) -> str:
            return ("didn't fit in the days available" if possible
                    else f"no good match near {city} this time")

        out: list[dict[str, str]] = []
        tagged = {s.get("for_you") for s in scheduled}
        have = {s.get("category") for s in scheduled}
        spoken_for: set[str] = set()  # interests a wish already speaks to
        for wish, cats, _times in self._profiles:
            spoken_for |= cats
            if wish not in tagged:
                possible = any(self.reason(p) == wish for p in candidates)
                out.append({"what": wish, "reason": why(possible)})
        for cat in self.interests:
            if related(cat) & have or cat in spoken_for:
                continue
            possible = any(p.get("category") in related(cat) for p in candidates)
            out.append({"what": CATEGORY_LABEL.get(cat, cat), "reason": why(possible)})
        return out


# ------------------------------------------------------------ researched places
MAX_PLACES = 16
PLACE_MAX_KM = 150.0  # "in or near the destination": a long day trip at most
_SAME_PLACE_KM = 0.15
_DURATION_RANGE = (20.0, 480.0)
_COST_RANGE = (0.0, 300.0)
_WEAK_WORDS = frozenset({
    "day", "days", "trip", "the", "and", "for", "see", "visit", "some", "good", "best",
    "great", "local", "tour", "with", "near", "time",
})


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[^\W\d_]{3,}", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in words if w not in _WEAK_WORDS}


def match_wish(value: Any, wishes: list[str]) -> str | None:
    """The traveler's wish a model-written label refers to, if any."""
    text = clean_text(value, MAX_WISH_CHARS).lower()
    if not text or not wishes:
        return None
    for wish in wishes:
        if wish.lower() == text:
            return wish
    if len(text) >= 3:
        for wish in wishes:
            if text in wish.lower() or wish.lower() in text:
                return wish
    tokens = _tokens(text)
    for wish in wishes:
        if tokens & _tokens(wish):
            return wish
    return None


def _number(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, f)) if math.isfinite(f) else default


def _coord(value: Any, limit: float) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and -limit <= f <= limit else None


def _same_place(name: str, lat: float, lon: float, other: dict[str, Any]) -> bool:
    """The same name (or one inside the other), or the same spot under a similar name.

    Nearness alone is not enough: distinct sights can sit across the street
    from each other (Ubud Palace and Ubud Art Market).
    """
    a, b = name.casefold(), str(other.get("name", "")).casefold()
    if a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)):
        return True
    try:
        km = haversine_km(lat, lon, float(other["lat"]), float(other["lon"]))
    except (KeyError, TypeError, ValueError):
        return False
    return km < _SAME_PLACE_KM and len(_tokens(a) & _tokens(b)) >= 2


def clean_places(
    raw: Any,
    *,
    centre: tuple[float, float],
    prefs: dict[str, Any],
    known: list[dict[str, Any]] | None = None,
    limit: int = MAX_PLACES,
    max_km: float = PLACE_MAX_KM,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Validate places proposed by a model (the scout's research).

    Returns (accepted, rejected). Accepted places are catalog-shaped points of
    interest. One that serves a traveler's wish is also tagged
    `added_by="scout"` and `for_you=<the wish>`, so the planner ranks it first
    and a blanket skip ("no museums") does not drop it. Rejected ones carry a
    short reason, so a developer reading the blackboard can see why.
    """
    tailor = Tailoring(prefs)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    items = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    for item in items:
        d = item.model_dump() if isinstance(item, BaseModel) else item
        name = clean_text(d.get("name"), 80) if isinstance(d, dict) else ""

        def reject(reason: str, name: str = name) -> None:
            rejected.append({"name": name or "(unnamed)", "reason": reason})

        if not name:
            reject("not a named place")
            continue
        if len(accepted) >= limit:
            reject(f"only {limit} places are kept")
            continue
        lat, lon = _coord(d.get("lat"), 90.0), _coord(d.get("lon"), 180.0)
        if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
            reject("invalid coordinates")
            continue
        km = haversine_km(centre[0], centre[1], lat, lon)
        if km > max_km:
            reject(f"{km:.0f} km from the destination, too far")
            continue
        if any(_same_place(name, lat, lon, p) for p in [*(known or []), *accepted]):
            reject("already on the list")
            continue
        category = to_category(d.get("category")) or "experience"
        best_time = clean_text(d.get("best_time"), 20).lower()
        best_time = best_time if best_time in BEST_TIMES else "anytime"
        wish = match_wish(d.get("for_wish"), tailor.wishes)
        if wish is None and category in tailor.avoid:
            reject(f"the traveler asked to skip {CATEGORY_LABEL[category]}")
            continue
        place = {
            "name": name,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "category": category,
            "best_time": best_time,
            "duration_min": int(round(_number(d.get("duration_min"), *_DURATION_RANGE, 90.0))),
            "cost_usd": round(_number(d.get("cost_usd"), *_COST_RANGE, 0.0), 2),
            "tip": clean_text(d.get("tip"), 140),
        }
        if wish is not None:
            place["added_by"] = "scout"
            place["for_you"] = wish
        accepted.append(place)
    return accepted, rejected
