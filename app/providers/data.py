"""Deterministic mock travel catalogs.

Seeded by run_id so repeated benchmark iterations see identical data -- a
provider that returned different results per iteration would inject variance
into exactly the measurement we are trying to make precise.
"""

from __future__ import annotations

import hashlib
from typing import Any

DESTINATIONS: dict[str, dict[str, Any]] = {
    "lisbon": {
        "season_summary": "Mild and bright in shoulder season; Atlantic breeze keeps evenings cool.",
        "neighborhoods": [
            {"name": "Alfama", "vibe": "Old-world, fado bars, steep tiled lanes",
             "why_now": "Warm evenings without the August crush"},
            {"name": "Principe Real", "vibe": "Leafy, design shops, garden cafes",
             "why_now": "Jacarandas in bloom"},
            {"name": "Belem", "vibe": "Riverside monuments and pastry pilgrimage",
             "why_now": "Clear light for river views"},
        ],
        "activities": ["Tram 28 loop", "Time Out Market crawl", "Sintra day trip",
                       "Fado night in Alfama", "LX Factory browsing"],
        "carriers": ["TAP Air", "Lufthansa", "United"],
        "stays": [
            {"name": "Casa Alfama", "neighborhood": "Alfama", "nightly_usd": 165.0, "rating": 4.6, "kind": "boutique"},
            {"name": "Principe Garden Suites", "neighborhood": "Principe Real", "nightly_usd": 210.0, "rating": 4.8, "kind": "hotel"},
            {"name": "Baixa Budget Rooms", "neighborhood": "Baixa", "nightly_usd": 95.0, "rating": 4.1, "kind": "hostel"},
            {"name": "Riverside Apartment", "neighborhood": "Belem", "nightly_usd": 140.0, "rating": 4.4, "kind": "rental"},
        ],
    },
    "kyoto": {
        "season_summary": "Temple gardens peak in autumn; mornings crisp, afternoons golden.",
        "neighborhoods": [
            {"name": "Gion", "vibe": "Machiya townhouses, lantern-lit lanes", "why_now": "Maple colour along the canal"},
            {"name": "Arashiyama", "vibe": "Bamboo groves and riverside temples", "why_now": "Fewer crowds at dawn"},
            {"name": "Downtown Nakagyo", "vibe": "Covered markets, coffee, easy transit", "why_now": "Central base for day trips"},
        ],
        "activities": ["Fushimi Inari at sunrise", "Nishiki Market tasting", "Philosopher's Path walk",
                       "Arashiyama bamboo grove", "Kaiseki dinner"],
        "carriers": ["ANA", "JAL", "Singapore Airlines"],
        "stays": [
            {"name": "Gion Machiya Inn", "neighborhood": "Gion", "nightly_usd": 245.0, "rating": 4.9, "kind": "ryokan"},
            {"name": "Nakagyo Business Hotel", "neighborhood": "Nakagyo", "nightly_usd": 120.0, "rating": 4.3, "kind": "hotel"},
            {"name": "Arashiyama Riverside", "neighborhood": "Arashiyama", "nightly_usd": 190.0, "rating": 4.7, "kind": "ryokan"},
            {"name": "Kyoto Station Pods", "neighborhood": "Shimogyo", "nightly_usd": 70.0, "rating": 4.0, "kind": "capsule"},
        ],
    },
    "mexico city": {
        "season_summary": "Dry season clarity; warm days, cool nights, jacaranda colour in spring.",
        "neighborhoods": [
            {"name": "Roma Norte", "vibe": "Art deco, cafes, galleries", "why_now": "Patio weather"},
            {"name": "Condesa", "vibe": "Park loops and bistros", "why_now": "Jacarandas over Amsterdam Ave"},
            {"name": "Centro Historico", "vibe": "Grand plazas and museums", "why_now": "Cool mornings for walking"},
        ],
        "activities": ["Teotihuacan sunrise", "Frida Kahlo Museum", "Xochimilco trajineras",
                       "Mercado de Coyoacan", "Lucha libre night"],
        "carriers": ["Aeromexico", "Delta", "Volaris"],
        "stays": [
            {"name": "Roma Norte Loft", "neighborhood": "Roma Norte", "nightly_usd": 130.0, "rating": 4.7, "kind": "rental"},
            {"name": "Condesa Garden Hotel", "neighborhood": "Condesa", "nightly_usd": 175.0, "rating": 4.6, "kind": "hotel"},
            {"name": "Centro Hostel", "neighborhood": "Centro", "nightly_usd": 55.0, "rating": 4.2, "kind": "hostel"},
            {"name": "Polanco Grand", "neighborhood": "Polanco", "nightly_usd": 320.0, "rating": 4.9, "kind": "luxury"},
        ],
    },
    "reykjavik": {
        "season_summary": "Long twilight, aurora odds climb after September; pack layers.",
        "neighborhoods": [
            {"name": "Midborg", "vibe": "Walkable centre, wool shops, bakeries", "why_now": "Aurora visible from harbour"},
            {"name": "Laugardalur", "vibe": "Thermal pools and parkland", "why_now": "Quiet soaking season"},
            {"name": "Grandi", "vibe": "Old harbour, galleries, seafood", "why_now": "Whale watching still running"},
        ],
        "activities": ["Golden Circle loop", "Blue Lagoon soak", "Aurora chase",
                       "Harpa concert hall", "Whale watching"],
        "carriers": ["Icelandair", "Delta", "PLAY"],
        "stays": [
            {"name": "Midborg Guesthouse", "neighborhood": "Midborg", "nightly_usd": 185.0, "rating": 4.5, "kind": "guesthouse"},
            {"name": "Harbour View Hotel", "neighborhood": "Grandi", "nightly_usd": 240.0, "rating": 4.7, "kind": "hotel"},
            {"name": "Laugardalur Apartments", "neighborhood": "Laugardalur", "nightly_usd": 150.0, "rating": 4.4, "kind": "rental"},
            {"name": "Capital Hostel", "neighborhood": "Midborg", "nightly_usd": 85.0, "rating": 4.0, "kind": "hostel"},
        ],
    },
}

_DEFAULT_KEY = "lisbon"


def resolve_destination(name: str) -> tuple[str, dict[str, Any]]:
    """Fuzzy-match a destination, falling back to a sensible default."""
    key = (name or "").strip().lower()
    if key in DESTINATIONS:
        return key.title(), DESTINATIONS[key]
    for k, v in DESTINATIONS.items():
        if k in key or key in k:
            return k.title(), v
    return name.title() if name else _DEFAULT_KEY.title(), DESTINATIONS[_DEFAULT_KEY]


def stable_seed(*parts: str) -> int:
    """Deterministic seed from string parts."""
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:4], "big")
