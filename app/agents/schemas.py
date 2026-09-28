"""Pydantic contracts passed between agents.

One source of truth: these are the JSON shapes written to the blackboard, so an
agent cannot publish something the next agent can't parse.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The vocabulary for reading the traveler's note. These are enums in the tool
# schemas the model sees, and the allow-list every model-supplied value is
# checked against (see preferences.py).
Category = Literal[
    "landmark", "museum", "viewpoint", "nature", "hike", "beach", "market",
    "food", "nightlife", "neighborhood", "experience", "shopping",
]
BestTime = Literal["sunrise", "morning", "midday", "afternoon", "sunset", "evening", "anytime"]
Pace = Literal["relaxed", "balanced", "packed"]


class TravelBrief(BaseModel):
    destination: str = Field(description="Destination city or region")
    origin: str = Field(default="", description="Departure city")
    start_date: str = Field(default="", description="ISO start date")
    end_date: str = Field(default="", description="ISO end date")
    travelers: int = Field(default=1, ge=1)
    budget_total: float = Field(default=0.0, description="Hard spending ceiling, USD")
    nuance: str = Field(default="", description="Free-text preferences")


class Place(BaseModel):
    name: str
    lat: float = 0.0
    lon: float = 0.0


class Neighborhood(Place):
    vibe: str
    why_now: str = Field(default="", description="Seasonal note")


class PointOfInterest(Place):
    category: str = "landmark"
    best_time: str = Field(
        default="anytime",
        description="sunrise | morning | midday | afternoon | sunset | evening | anytime",
    )
    duration_min: int = 60
    cost_usd: float = Field(default=0.0, description="Per person")
    tip: str = ""


class DestinationShortlist(BaseModel):
    destination: str
    lat: float = 0.0
    lon: float = 0.0
    tz: float = 0.0
    dst: str | None = None
    speed_factor: float = 1.0
    daily_food_usd: float = 50.0
    airport: Place | None = None
    season_summary: str = ""
    neighborhoods: list[Neighborhood] = Field(default_factory=list)
    signature_activities: list[str] = Field(default_factory=list)
    pois: list[PointOfInterest] = Field(default_factory=list)
    # The scout's recommendation for where to sleep, derived from where the
    # things worth seeing actually are. The stay agent consumes this.
    base_area: Place | None = None


class FlightOption(BaseModel):
    carrier: str
    depart: str
    arrive: str
    duration_hours: float = 0.0
    price_usd: float = 0.0
    stops: int = 0


class StayOption(BaseModel):
    name: str
    neighborhood: str = ""
    lat: float = 0.0
    lon: float = 0.0
    nightly_usd: float = 0.0
    rating: float = 0.0
    kind: str = "hotel"
