"""Samples real Gemini latency to give the storage delta context.

The point of this chart is honesty. If Valkey beats Postgres by 5ms while the
model's own p50->p95 spread is 400ms, then the storage choice is invisible to the
user and the plot says so at a glance. Without this overlay a 5ms delta can be
drawn as a dramatic 2x bar and mislead everyone in the room.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.bench.stats import describe
from app.config import settings


async def probe_gemini_latency(samples: int = 30, concurrency: int = 4) -> dict[str, Any]:
    """Time `samples` short generate_content calls against the configured model."""
    if not settings.google_api_key:
        return {"available": False, "reason": "GOOGLE_API_KEY not set"}

    try:
        from google import genai
    except ImportError:
        return {"available": False, "reason": "google-genai not installed"}

    client = genai.Client(api_key=settings.google_api_key)
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    failures = 0

    prompts = [
        f"In one short sentence, name a reason to visit city number {i} in Europe."
        for i in range(samples)
    ]

    async def one(prompt: str) -> None:
        nonlocal failures
        async with sem:
            start = time.perf_counter()
            try:
                await client.aio.models.generate_content(
                    model=settings.gemini_model, contents=prompt
                )
                latencies.append((time.perf_counter() - start) * 1000.0)
            except Exception:  # noqa: BLE001 - a failed probe must not kill the page
                failures += 1

    await asyncio.gather(*(one(p) for p in prompts))

    if not latencies:
        return {"available": False, "reason": f"all {samples} probe calls failed"}

    return {
        "available": True,
        "model": settings.gemini_model,
        "failures": failures,
        **describe(latencies),
    }
