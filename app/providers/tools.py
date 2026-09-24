"""Mock travel provider tools exposed to the agents.

Latency is simulated and configurable. In benchmark mode it is set to 0: a 40ms
sleep per tool call would dominate end-to-end time and mask the storage signal we
are trying to measure. The demo path keeps it for realism.
"""

from __future__ import annotations

import asyncio
import random
from contextvars import ContextVar

from app.agents.schemas import (
    DestinationShortlist,
    FlightOption,
    Neighborhood,
    Place,
    PointOfInterest,
    StayOption,
)
from app.config import settings
from app.providers.data import resolve_destination, stable_seed
from app.providers.geo import haversine_km

PROVIDER_LATENCY_MS: ContextVar[int] = ContextVar(
    "provider_latency_ms", default=settings.provider_latency_ms
)

# POIs further than this from the city centre are day trips (Sintra-style), and
# must not drag the recommended base area out into the suburbs.
_DAY_TRIP_KM = 12.0


async def _simulate_network() -> None:
    ms = PROVIDER_LATENCY_MS.get()
    if ms <= 0:
        return
    jitter = random.uniform(0.85, 1.15)
    await asyncio.sleep(ms * jitter / 1000.0)


def _base_area(data: dict, pois: list[PointOfInterest]) -> Place:
    """Centroid of the in-town points of interest, snapped to a neighborhood.

    Snapping matters: "stay at 38.7121,-9.1374" is not advice, "stay in Alfama"
    is. The nearest named neighborhood to the centroid is what the scout hands
    to the stay agent.
    """
    in_town = [
        p for p in pois
        if haversine_km(data["lat"], data["lon"], p.lat, p.lon) <= _DAY_TRIP_KM
    ] or pois
    lat = sum(p.lat for p in in_town) / len(in_town)
    lon = sum(p.lon for p in in_town) / len(in_town)

    hood = min(
        data["neighborhoods"],
        key=lambda h: haversine_km(lat, lon, h["lat"], h["lon"]),
    )
    return Place(name=hood["name"], lat=round(lat, 5), lon=round(lon, 5))


async def scout_destination(destination: str, run_id: str = "") -> DestinationShortlist:
    await _simulate_network()
    name, data = resolve_destination(destination)
    pois = [PointOfInterest(**p) for p in data["pois"]]
    return DestinationShortlist(
        destination=name,
        lat=data["lat"],
        lon=data["lon"],
        tz=data["tz"],
        dst=data.get("dst"),
        speed_factor=data.get("speed_factor", 1.0),
        daily_food_usd=data.get("daily_food_usd", 50.0),
        airport=Place(**data["airport"]),
        season_summary=data["season_summary"],
        neighborhoods=[Neighborhood(**n) for n in data["neighborhoods"]],
        signature_activities=[p.name for p in pois],
        pois=pois,
        base_area=_base_area(data, pois),
    )


async def search_flights(
    origin: str, destination: str, start_date: str, travelers: int = 1, run_id: str = ""
) -> list[FlightOption]:
    await _simulate_network()
    name, data = resolve_destination(destination)
    rng = random.Random(stable_seed(run_id, origin, name, start_date))

    options: list[FlightOption] = []
    for i, carrier in enumerate(data["carriers"]):
        base = rng.uniform(320, 980)
        stops = i % 3
        options.append(
            FlightOption(
                carrier=carrier,
                depart=f"{start_date} {6 + i * 4:02d}:00",
                arrive=f"{start_date} {9 + i * 4:02d}:30",
                duration_hours=round(6.5 + stops * 2.25 + rng.uniform(0, 1.5), 2),
                price_usd=round(base * (1 - 0.08 * stops) * max(1, travelers), 2),
                stops=stops,
            )
        )
    return sorted(options, key=lambda o: o.price_usd)


async def search_stays(
    destination: str, nights: int = 1, travelers: int = 1, run_id: str = ""
) -> list[StayOption]:
    await _simulate_network()
    name, data = resolve_destination(destination)
    rng = random.Random(stable_seed(run_id, name, "stays"))

    options: list[StayOption] = []
    for raw in data["stays"]:
        nightly = raw["nightly_usd"] * (1.0 + 0.12 * (travelers - 1))
        options.append(
            StayOption(
                name=raw["name"],
                neighborhood=raw["neighborhood"],
                lat=raw["lat"],
                lon=raw["lon"],
                nightly_usd=round(nightly * rng.uniform(0.95, 1.05), 2),
                rating=raw["rating"],
                kind=raw["kind"],
            )
        )
    return sorted(options, key=lambda o: o.nightly_usd)
