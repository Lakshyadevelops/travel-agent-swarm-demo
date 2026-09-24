"""Pydantic contracts passed between agents.

One source of truth: these are used as ADK `output_schema`s AND as the JSON
shapes written to the blackboard, so an agent cannot publish something the next
agent can't parse.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TravelBrief(BaseModel):
    destination: str = Field(description="Destination city or region")
    origin: str = Field(default="", description="Departure city")
    start_date: str = Field(default="", description="ISO start date")
    end_date: str = Field(default="", description="ISO end date")
    travelers: int = Field(default=1, ge=1)
    budget_total: float = Field(default=0.0, description="Hard spending ceiling, USD")
    nuance: str = Field(default="", description="Free-text preferences")


class Neighborhood(BaseModel):
    name: str
    vibe: str
    why_now: str = Field(default="", description="Seasonal note")


class DestinationShortlist(BaseModel):
    destination: str
    season_summary: str = ""
    neighborhoods: list[Neighborhood] = Field(default_factory=list)
    signature_activities: list[str] = Field(default_factory=list)


class FlightOption(BaseModel):
    carrier: str
    depart: str
    arrive: str
    duration_hours: float = 0.0
    price_usd: float = 0.0
    stops: int = 0


class TransitPlan(BaseModel):
    options: list[FlightOption] = Field(default_factory=list)
    local_transit_notes: str = ""
    selected_index: int = 0

    @property
    def selected_price(self) -> float:
        if not self.options:
            return 0.0
        idx = min(self.selected_index, len(self.options) - 1)
        return self.options[idx].price_usd


class StayOption(BaseModel):
    name: str
    neighborhood: str = ""
    nightly_usd: float = 0.0
    rating: float = 0.0
    kind: str = "hotel"


class StayPlan(BaseModel):
    options: list[StayOption] = Field(default_factory=list)
    selected_index: int = 0
    nights: int = 1

    @property
    def selected_total(self) -> float:
        if not self.options:
            return 0.0
        idx = min(self.selected_index, len(self.options) - 1)
        return self.options[idx].nightly_usd * self.nights


class BudgetVerdict(BaseModel):
    within_budget: bool
    total_estimate_usd: float = 0.0
    ceiling_usd: float = 0.0
    overage_usd: float = 0.0
    guidance: str = Field(
        default="", description="What the workers must change on the retry"
    )


class DayPlan(BaseModel):
    day: int
    date: str = ""
    morning: str = ""
    afternoon: str = ""
    evening: str = ""
    est_cost_usd: float = 0.0


class Itinerary(BaseModel):
    destination: str
    summary: str = ""
    days: list[DayPlan] = Field(default_factory=list)
    flight: FlightOption | None = None
    stay: StayOption | None = None
    total_estimate_usd: float = 0.0
    within_budget: bool = True
