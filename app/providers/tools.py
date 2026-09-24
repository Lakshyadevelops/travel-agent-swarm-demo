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
    StayOption,
)
from app.config import settings
from app.providers.data import resolve_destination, stable_seed

PROVIDER_LATENCY_MS: ContextVar[int] = ContextVar(
    "provider_latency_ms", default=settings.provider_latency_ms
)


async def _simulate_network() -> None:
    ms = PROVIDER_LATENCY_MS.get()
    if ms <= 0:
        return
    jitter = random.uniform(0.85, 1.15)
    await asyncio.sleep(ms * jitter / 1000.0)


async def scout_destination(destination: str, run_id: str = "") -> DestinationShortlist:
    await _simulate_network()
    name, data = resolve_destination(destination)
    return DestinationShortlist(
        destination=name,
        season_summary=data["season_summary"],
        neighborhoods=[Neighborhood(**n) for n in data["neighborhoods"]],
        signature_activities=list(data["activities"]),
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
                arrive=f"{start_date} {12 + i * 4:02d}:30",
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
                nightly_usd=round(nightly * rng.uniform(0.95, 1.05), 2),
                rating=raw["rating"],
                kind=raw["kind"],
            )
        )
    return sorted(options, key=lambda o: o.nightly_usd)
