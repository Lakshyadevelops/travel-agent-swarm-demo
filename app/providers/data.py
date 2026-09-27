"""Deterministic mock travel catalogs.

Seeded by the brief so repeated benchmark iterations see identical data -- a
provider that returned different results per iteration would inject variance
into exactly the measurement we are trying to make precise.

Every place carries real coordinates, so the itinerary can report genuine
distances and travel times between stops, and every point of interest carries a
`best_time` (sunrise / morning / midday / afternoon / sunset / evening / anytime)
so the day planner can put viewpoints at golden hour and markets at lunch.

Coordinates are approximate to ~50 m, which is well inside the error of the
straight-line-times-detour distance model that consumes them.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any


def _poi(name, lat, lon, category, best_time, duration_min, cost_usd, tip):
    return {
        "name": name, "lat": lat, "lon": lon, "category": category,
        "best_time": best_time, "duration_min": duration_min,
        "cost_usd": cost_usd, "tip": tip,
    }


def _stay(name, neighborhood, lat, lon, nightly_usd, rating, kind):
    return {
        "name": name, "neighborhood": neighborhood, "lat": lat, "lon": lon,
        "nightly_usd": nightly_usd, "rating": rating, "kind": kind,
    }


def _hood(name, lat, lon, vibe, why_now):
    return {"name": name, "lat": lat, "lon": lon, "vibe": vibe, "why_now": why_now}


# dst: "eu" | "us" | None. Standard-time offsets in `tz`.
DESTINATIONS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------ Europe
    "paris": {
        "display": "Paris", "lat": 48.8566, "lon": 2.3522, "tz": 1, "dst": "eu",
        "speed_factor": 0.8, "daily_food_usd": 70,
        "airport": {"name": "Charles de Gaulle (CDG)", "lat": 49.0097, "lon": 2.5479},
        "season_summary": "Chestnut trees turn in autumn and café terraces stay open under heaters.",
        "carriers": ["Air France", "Delta", "United"],
        "neighborhoods": [
            _hood("Le Marais", 48.8590, 2.3620, "Medieval lanes, galleries, falafel", "Gallery openings on Thursday nights"),
            _hood("Saint-Germain", 48.8540, 2.3330, "Cafés, bookshops, Left Bank chic", "Terrace season with no summer crowds"),
            _hood("Montmartre", 48.8867, 2.3431, "Hilltop village, artists' square", "Harvest festival at the vineyard in October"),
        ],
        "pois": [
            _poi("Sacré-Cœur steps, Montmartre", 48.8867, 2.3431, "viewpoint", "sunrise", 45, 0, "Empty steps and pink light over the rooftops"),
            _poi("Louvre Museum", 48.8606, 2.3376, "museum", "morning", 180, 24, "Book the 9:00 slot and go straight to the Denon wing"),
            _poi("Notre-Dame Cathedral", 48.8530, 2.3499, "landmark", "morning", 45, 0, "Reopened nave; the queue is shortest at opening"),
            _poi("Marché des Enfants Rouges", 48.8629, 2.3620, "market", "midday", 60, 20, "Paris's oldest covered market; Moroccan stall for lunch"),
            _poi("Musée d'Orsay", 48.8600, 2.3266, "museum", "afternoon", 120, 18, "The clock-window café looks straight across the Seine"),
            _poi("Le Marais & Place des Vosges", 48.8556, 2.3655, "neighborhood", "afternoon", 90, 0, "Wander rue des Rosiers, then rest under the arcades"),
            _poi("Trocadéro for the Eiffel Tower", 48.8616, 2.2893, "viewpoint", "sunset", 45, 0, "Stay past dusk: the tower sparkles on the hour"),
            _poi("Seine river cruise from Pont Neuf", 48.8570, 2.3413, "experience", "evening", 60, 17, "Bridges lit up after dark; sit on the open top deck"),
        ],
        "stays": [
            _stay("Hôtel du Marais", "Le Marais", 48.8580, 2.3600, 230.0, 4.6, "hotel"),
            _stay("Saint-Germain Boutique", "Saint-Germain", 48.8535, 2.3340, 285.0, 4.8, "boutique"),
            _stay("Montmartre Studio", "Montmartre", 48.8850, 2.3400, 150.0, 4.4, "rental"),
            _stay("Gare du Nord Hostel", "Gare du Nord", 48.8800, 2.3550, 70.0, 4.0, "hostel"),
        ],
    },
    "london": {
        "display": "London", "lat": 51.5074, "lon": -0.1278, "tz": 0, "dst": "eu",
        "speed_factor": 0.8, "daily_food_usd": 75,
        "airport": {"name": "Heathrow (LHR)", "lat": 51.4700, "lon": -0.4543},
        "season_summary": "Crisp autumn light on the Thames; most big museums are free year-round.",
        "carriers": ["British Airways", "Virgin Atlantic", "United"],
        "neighborhoods": [
            _hood("Covent Garden", 51.5117, -0.1240, "Theatres, piazza buskers, walkable to everything", "West End season opens"),
            _hood("South Bank", 51.5055, -0.1160, "Riverside culture strip", "Riverside food market weekends"),
            _hood("Shoreditch", 51.5260, -0.0780, "Street art, warehouses, late bars", "Gallery late nights"),
        ],
        "pois": [
            _poi("Primrose Hill", 51.5390, -0.1606, "viewpoint", "sunrise", 45, 0, "Whole skyline from the Shard to St Paul's, lit from behind"),
            _poi("British Museum", 51.5194, -0.1270, "museum", "morning", 150, 0, "Free; go to the Rosetta Stone before the tour groups arrive"),
            _poi("Tower of London", 51.5081, -0.0759, "landmark", "morning", 150, 40, "Crown Jewels first, while the queue is short"),
            _poi("Borough Market", 51.5055, -0.0910, "market", "midday", 60, 20, "Graze the stalls instead of sitting down for lunch"),
            _poi("Tate Modern", 51.5076, -0.0994, "museum", "afternoon", 90, 0, "Level 10 viewing terrace is free"),
            _poi("Westminster & Big Ben", 51.5007, -0.1246, "landmark", "afternoon", 45, 0, "Cross Westminster Bridge for the classic shot"),
            _poi("Sky Garden", 51.5113, -0.0836, "viewpoint", "sunset", 60, 0, "Free but book ahead; watch the Thames turn gold"),
            _poi("West End show", 51.5115, -0.1300, "nightlife", "evening", 150, 80, "Day-of tickets at the TKTS booth in Leicester Square"),
        ],
        "stays": [
            _stay("Covent Garden Hotel", "Covent Garden", 51.5130, -0.1260, 290.0, 4.7, "hotel"),
            _stay("South Bank Suites", "South Bank", 51.5040, -0.1080, 220.0, 4.5, "hotel"),
            _stay("Shoreditch Loft", "Shoreditch", 51.5250, -0.0790, 170.0, 4.4, "rental"),
            _stay("King's Cross Hostel", "King's Cross", 51.5300, -0.1230, 65.0, 4.0, "hostel"),
        ],
    },
    "lisbon": {
        "display": "Lisbon", "lat": 38.7223, "lon": -9.1393, "tz": 0, "dst": "eu",
        "speed_factor": 0.9, "daily_food_usd": 45,
        "airport": {"name": "Humberto Delgado (LIS)", "lat": 38.7742, "lon": -9.1342},
        "season_summary": "Mild and bright in shoulder season; Atlantic breeze keeps evenings cool.",
        "carriers": ["TAP Air", "Lufthansa", "United"],
        "neighborhoods": [
            _hood("Alfama", 38.7115, -9.1300, "Old-world, fado bars, steep tiled lanes", "Warm evenings without the August crush"),
            _hood("Principe Real", 38.7167, -9.1490, "Leafy, design shops, garden cafes", "Jacarandas in bloom"),
            _hood("Belem", 38.6970, -9.2060, "Riverside monuments and pastry pilgrimage", "Clear light for river views"),
        ],
        "pois": [
            _poi("Miradouro de Santa Luzia", 38.7118, -9.1302, "viewpoint", "sunrise", 40, 0, "Faces east over Alfama's rooftops and the Tagus; best at first light"),
            _poi("São Jorge Castle", 38.7139, -9.1335, "landmark", "morning", 90, 17, "Ramparts before the 10:30 tour buses"),
            _poi("Tram 28 from Martim Moniz", 38.7163, -9.1360, "experience", "morning", 50, 3.5, "Board at the terminus before 9:00 to get a seat"),
            _poi("Jerónimos Monastery", 38.6979, -9.2065, "landmark", "morning", 75, 12, "The cloister is the highlight; skip the church queue"),
            _poi("Belém Tower", 38.6916, -9.2160, "landmark", "morning", 60, 9, "Pair with the monastery; 10 minutes' walk apart"),
            _poi("Pastéis de Belém", 38.6975, -9.2032, "food", "afternoon", 30, 6, "Eat inside; the takeaway line is the long one"),
            _poi("Time Out Market", 38.7069, -9.1459, "market", "midday", 75, 25, "Arrive before 12:30 to get a table"),
            _poi("LX Factory", 38.7033, -9.1784, "neighborhood", "afternoon", 90, 0, "Ler Devagar bookshop and its flying bicycle"),
            _poi("Miradouro da Senhora do Monte", 38.7193, -9.1328, "viewpoint", "sunset", 60, 0, "Highest miradouro; the whole city turns amber at golden hour"),
            _poi("Fado night in Alfama", 38.7110, -9.1290, "nightlife", "evening", 120, 45, "Small tascas start around 20:30; book a table"),
        ],
        "stays": [
            _stay("Casa Alfama", "Alfama", 38.7120, -9.1305, 165.0, 4.6, "boutique"),
            _stay("Principe Garden Suites", "Principe Real", 38.7170, -9.1485, 210.0, 4.8, "hotel"),
            _stay("Baixa Budget Rooms", "Baixa", 38.7110, -9.1380, 95.0, 4.1, "hostel"),
            _stay("Riverside Apartment", "Belem", 38.6960, -9.2000, 140.0, 4.4, "rental"),
        ],
    },
    "reykjavik": {
        "display": "Reykjavik", "lat": 64.1466, "lon": -21.9426, "tz": 0, "dst": None,
        "speed_factor": 1.1, "daily_food_usd": 80,
        "airport": {"name": "Keflavík (KEF)", "lat": 63.9850, "lon": -22.6056},
        "season_summary": "Long twilight, aurora odds climb after September; pack layers.",
        "carriers": ["Icelandair", "Delta", "PLAY"],
        "neighborhoods": [
            _hood("Midborg", 64.1466, -21.9340, "Walkable centre, wool shops, bakeries", "Aurora visible from harbour"),
            _hood("Laugardalur", 64.1430, -21.8770, "Thermal pools and parkland", "Quiet soaking season"),
            _hood("Grandi", 64.1530, -21.9500, "Old harbour, galleries, seafood", "Whale watching still running"),
        ],
        "pois": [
            _poi("Hallgrímskirkja tower", 64.1417, -21.9266, "viewpoint", "morning", 45, 10, "Lift to the top for the coloured-roof panorama"),
            _poi("Old Harbour whale watching", 64.1535, -21.9480, "experience", "morning", 180, 90, "Morning sailings have calmer seas"),
            _poi("Bæjarins Beztu Pylsur", 64.1481, -21.9387, "food", "midday", 20, 6, "Order 'eina með öllu': one with everything"),
            _poi("Golden Circle: Þingvellir", 64.2559, -21.1299, "nature", "morning", 240, 0, "Walk between the tectonic plates at Almannagjá"),
            _poi("Blue Lagoon", 63.8804, -22.4495, "nature", "afternoon", 150, 75, "Book the slot; closer to the airport than to town"),
            _poi("Sun Voyager", 64.1476, -21.9223, "viewpoint", "sunset", 30, 0, "Faces northwest over Faxaflói bay, straight into the sunset"),
            _poi("Perlan observation deck", 64.1291, -21.9187, "viewpoint", "sunset", 60, 30, "360° deck above the geothermal tanks"),
            _poi("Harpa concert hall", 64.1504, -21.9325, "nightlife", "evening", 120, 55, "The glass facade lights up after dark"),
            _poi("Aurora hunt at Grótta lighthouse", 64.1642, -22.0219, "nature", "evening", 90, 0, "Dark sky just outside town; check the aurora forecast first"),
        ],
        "stays": [
            _stay("Midborg Guesthouse", "Midborg", 64.1450, -21.9300, 185.0, 4.5, "guesthouse"),
            _stay("Harbour View Hotel", "Grandi", 64.1530, -21.9470, 240.0, 4.7, "hotel"),
            _stay("Laugardalur Apartments", "Laugardalur", 64.1420, -21.8760, 150.0, 4.4, "rental"),
            _stay("Capital Hostel", "Midborg", 64.1460, -21.9350, 85.0, 4.0, "hostel"),
        ],
    },
    # ---------------------------------------------------------------- Americas
    "new york": {
        "display": "New York", "lat": 40.7128, "lon": -74.0060, "tz": -5, "dst": "us",
        "speed_factor": 0.7, "daily_food_usd": 85,
        "airport": {"name": "JFK International (JFK)", "lat": 40.6413, "lon": -73.7781},
        "season_summary": "Fall foliage in Central Park, clear skies and comfortable walking weather.",
        "carriers": ["JetBlue", "Delta", "American"],
        "neighborhoods": [
            _hood("West Village", 40.7358, -74.0036, "Brownstones, jazz clubs, small restaurants", "Leafy streets turn gold"),
            _hood("Midtown", 40.7549, -73.9840, "Broadway, skyline, transit hub", "Broadway fall season"),
            _hood("Williamsburg", 40.7081, -73.9571, "Brooklyn waterfront, bars, vintage", "Smorgasburg's last weekends"),
        ],
        "pois": [
            _poi("Brooklyn Bridge walk", 40.7061, -73.9969, "viewpoint", "sunrise", 60, 0, "Walk Brooklyn to Manhattan at dawn with the skyline lit ahead"),
            _poi("Statue of Liberty ferry", 40.7003, -74.0122, "landmark", "morning", 180, 25, "First boat from Battery Park; reserve pedestal access"),
            _poi("9/11 Memorial", 40.7115, -74.0134, "landmark", "morning", 60, 0, "Pools are quiet and reflective early in the day"),
            _poi("Central Park: Bethesda Terrace", 40.7740, -73.9712, "nature", "morning", 90, 0, "Enter at 72nd St, walk the Mall to the fountain"),
            _poi("The Met", 40.7794, -73.9632, "museum", "midday", 150, 30, "The roof garden is open through October"),
            _poi("High Line & Chelsea Market", 40.7420, -74.0048, "neighborhood", "afternoon", 90, 15, "Walk south to north, then eat in the market"),
            _poi("Top of the Rock", 40.7593, -73.9794, "viewpoint", "sunset", 60, 40, "Book 30 min before sunset: you get day, dusk and night"),
            _poi("DUMBO waterfront", 40.7033, -73.9881, "viewpoint", "sunset", 45, 0, "Manhattan Bridge framed on Washington Street"),
            _poi("Broadway show", 40.7580, -73.9855, "nightlife", "evening", 150, 120, "TKTS in Times Square for same-day discounts"),
        ],
        "stays": [
            _stay("West Village Inn", "West Village", 40.7350, -74.0030, 320.0, 4.7, "boutique"),
            _stay("Midtown Tower Hotel", "Midtown", 40.7560, -73.9860, 260.0, 4.4, "hotel"),
            _stay("Williamsburg Loft", "Williamsburg", 40.7150, -73.9600, 210.0, 4.5, "rental"),
            _stay("Chelsea Hostel", "Chelsea", 40.7470, -73.9990, 95.0, 4.0, "hostel"),
        ],
    },
    "mexico city": {
        "display": "Mexico City", "lat": 19.4326, "lon": -99.1332, "tz": -6, "dst": None,
        "speed_factor": 0.6, "daily_food_usd": 35,
        "airport": {"name": "Benito Juárez (MEX)", "lat": 19.4361, "lon": -99.0719},
        "season_summary": "Dry season clarity; warm days, cool nights, jacaranda colour in spring.",
        "carriers": ["Aeromexico", "Delta", "Volaris"],
        "neighborhoods": [
            _hood("Roma Norte", 19.4190, -99.1620, "Art deco, cafes, galleries", "Patio weather"),
            _hood("Condesa", 19.4110, -99.1740, "Park loops and bistros", "Jacarandas over Amsterdam Ave"),
            _hood("Centro Historico", 19.4330, -99.1350, "Grand plazas and museums", "Cool mornings for walking"),
        ],
        "pois": [
            _poi("Zócalo & Metropolitan Cathedral", 19.4326, -99.1332, "landmark", "morning", 60, 0, "Flag ceremony at 8:00 most mornings"),
            _poi("Teotihuacan pyramids", 19.6925, -98.8438, "landmark", "morning", 180, 6, "Arrive at 8:00 opening, climb the Pyramid of the Sun before the heat"),
            _poi("Chapultepec Castle", 19.4204, -99.1817, "landmark", "morning", 90, 5, "Terrace views down Paseo de la Reforma"),
            _poi("Museo Nacional de Antropología", 19.4260, -99.1863, "museum", "midday", 150, 5, "Aztec Sun Stone hall; allow the full visit"),
            _poi("Frida Kahlo Museum", 19.3551, -99.1624, "museum", "morning", 75, 15, "Timed tickets sell out; book days ahead"),
            _poi("Mercado de Coyoacán", 19.3500, -99.1620, "market", "midday", 45, 10, "Tostadas at the back stalls"),
            _poi("Xochimilco trajineras", 19.2620, -99.1050, "experience", "afternoon", 120, 30, "Share a boat; mariachis come to you"),
            _poi("Torre Latinoamericana mirador", 19.4339, -99.1406, "viewpoint", "sunset", 45, 10, "Watch the city lights switch on from the 44th floor"),
            _poi("Lucha libre at Arena México", 19.4247, -99.1523, "nightlife", "evening", 150, 25, "Tuesday and Friday nights; buy masks outside"),
        ],
        "stays": [
            _stay("Roma Norte Loft", "Roma Norte", 19.4180, -99.1610, 130.0, 4.7, "rental"),
            _stay("Condesa Garden Hotel", "Condesa", 19.4120, -99.1720, 175.0, 4.6, "hotel"),
            _stay("Centro Hostel", "Centro", 19.4340, -99.1370, 55.0, 4.2, "hostel"),
            _stay("Polanco Grand", "Polanco", 19.4330, -99.1940, 320.0, 4.9, "luxury"),
        ],
    },
    # -------------------------------------------------------------------- Asia
    "tokyo": {
        "display": "Tokyo", "lat": 35.6762, "lon": 139.6503, "tz": 9, "dst": None,
        "speed_factor": 1.0, "daily_food_usd": 55,
        "airport": {"name": "Haneda (HND)", "lat": 35.5494, "lon": 139.7798},
        "season_summary": "Clear autumn air; Mount Fuji visible from the towers on crisp days.",
        "carriers": ["ANA", "JAL", "United"],
        "neighborhoods": [
            _hood("Shinjuku", 35.6938, 139.7034, "Neon, izakaya alleys, rail hub", "Autumn garden colour in Gyoen"),
            _hood("Asakusa", 35.7148, 139.7967, "Old Edo temples and craft shops", "Festival season"),
            _hood("Shibuya", 35.6580, 139.7016, "Crossings, fashion, rooftop views", "Clear evenings for Fuji views"),
        ],
        "pois": [
            _poi("Senso-ji Temple", 35.7148, 139.7967, "landmark", "sunrise", 60, 0, "Lanterns glow and Nakamise is empty before 7:00"),
            _poi("Tsukiji Outer Market", 35.6654, 139.7707, "market", "morning", 75, 30, "Sushi breakfast; most stalls close by 14:00"),
            _poi("Meiji Jingu", 35.6764, 139.6993, "landmark", "morning", 60, 0, "Forest approach is coolest in the morning"),
            _poi("Harajuku: Takeshita Street", 35.6716, 139.7031, "neighborhood", "afternoon", 60, 0, "Crepes and street fashion, a short walk from Meiji Jingu"),
            _poi("teamLab Planets", 35.6491, 139.7897, "museum", "afternoon", 120, 28, "Wear shorts; you wade through water"),
            _poi("Shibuya Sky", 35.6585, 139.7022, "viewpoint", "sunset", 60, 18, "Fuji silhouette on clear evenings; book the sunset slot"),
            _poi("Shibuya Crossing", 35.6595, 139.7005, "neighborhood", "evening", 30, 0, "Watch from the Starbucks window above the scramble"),
            _poi("Golden Gai, Shinjuku", 35.6938, 139.7036, "nightlife", "evening", 120, 40, "Six-seat bars; some charge a small cover"),
        ],
        "stays": [
            _stay("Shinjuku Granbell", "Shinjuku", 35.6950, 139.7040, 190.0, 4.5, "hotel"),
            _stay("Asakusa Ryokan", "Asakusa", 35.7120, 139.7950, 160.0, 4.6, "ryokan"),
            _stay("Shibuya Stream Hotel", "Shibuya", 35.6570, 139.7030, 240.0, 4.7, "hotel"),
            _stay("Ueno Capsule", "Ueno", 35.7100, 139.7740, 45.0, 4.0, "capsule"),
        ],
    },
    "kyoto": {
        "display": "Kyoto", "lat": 35.0116, "lon": 135.7681, "tz": 9, "dst": None,
        "speed_factor": 1.0, "daily_food_usd": 60,
        "airport": {"name": "Kansai International (KIX)", "lat": 34.4320, "lon": 135.2304},
        "season_summary": "Temple gardens peak in autumn; mornings crisp, afternoons golden.",
        "carriers": ["ANA", "JAL", "Singapore Airlines"],
        "neighborhoods": [
            _hood("Gion", 35.0037, 135.7751, "Machiya townhouses, lantern-lit lanes", "Maple colour along the canal"),
            _hood("Arashiyama", 35.0094, 135.6668, "Bamboo groves and riverside temples", "Fewer crowds at dawn"),
            _hood("Downtown Nakagyo", 35.0080, 135.7630, "Covered markets, coffee, easy transit", "Central base for day trips"),
        ],
        "pois": [
            _poi("Fushimi Inari Taisha", 34.9671, 135.7727, "landmark", "sunrise", 120, 0, "Climb the torii tunnels before 7:00; near-empty at dawn"),
            _poi("Arashiyama Bamboo Grove", 35.0170, 135.6717, "nature", "sunrise", 60, 0, "Only truly quiet in the first hour after sunrise"),
            _poi("Kinkaku-ji (Golden Pavilion)", 35.0394, 135.7292, "landmark", "morning", 60, 3.5, "Morning sun lights the gold leaf across the pond"),
            _poi("Kiyomizu-dera", 34.9949, 135.7850, "landmark", "morning", 90, 3, "Wooden stage over the maple valley"),
            _poi("Nishiki Market", 35.0050, 135.7649, "market", "midday", 75, 20, "Graze; eating while walking is frowned upon"),
            _poi("Philosopher's Path", 35.0270, 135.7948, "nature", "afternoon", 60, 0, "Canal-side walk from Ginkaku-ji south"),
            _poi("Yasaka Pagoda from Ninenzaka", 34.9986, 135.7788, "viewpoint", "sunset", 45, 0, "The pagoda silhouettes against the western sky"),
            _poi("Gion evening walk, Hanamikoji", 35.0037, 135.7751, "neighborhood", "evening", 60, 0, "Lanterns lit; you may glimpse a maiko"),
            _poi("Kaiseki dinner in Pontocho", 35.0050, 135.7710, "food", "evening", 120, 90, "Riverside platforms in season"),
        ],
        "stays": [
            _stay("Gion Machiya Inn", "Gion", 35.0040, 135.7760, 245.0, 4.9, "ryokan"),
            _stay("Nakagyo Business Hotel", "Nakagyo", 35.0090, 135.7610, 120.0, 4.3, "hotel"),
            _stay("Arashiyama Riverside", "Arashiyama", 35.0130, 135.6770, 190.0, 4.7, "ryokan"),
            _stay("Kyoto Station Pods", "Shimogyo", 34.9858, 135.7588, 70.0, 4.0, "capsule"),
        ],
    },
}

_DEFAULT_KEY = "lisbon"

_ALIASES = {
    "nyc": "new york", "new york city": "new york", "manhattan": "new york",
    "cdmx": "mexico city", "ciudad de mexico": "mexico city",
    "reykjavík": "reykjavik",
}


def resolve_destination(name: str) -> tuple[str, dict[str, Any]]:
    """Fuzzy-match a destination, falling back to a sensible default."""
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key in DESTINATIONS:
        return DESTINATIONS[key]["display"], DESTINATIONS[key]
    for k, v in DESTINATIONS.items():
        if k in key or key in k:
            return v["display"], v
    d = DESTINATIONS[_DEFAULT_KEY]
    return d["display"], d


def supported_destinations() -> list[str]:
    return [v["display"] for v in DESTINATIONS.values()]


def match_destination(name: str) -> str | None:
    """Catalog display name for `name`, or None if the catalog doesn't cover it.

    Same matching as resolve_destination() minus the silent default, so the API
    can reject an unsupported city instead of quietly planning a different one.
    Substring matches need 3+ characters so "a" doesn't match "Paris".
    """
    key = (name or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key in DESTINATIONS:
        return DESTINATIONS[key]["display"]
    if len(key) >= 3:
        for k, v in DESTINATIONS.items():
            if k in key or key in k:
                return v["display"]
    return None


def _nth_sunday(year: int, month: int, n: int) -> date:
    """n-th Sunday of a month; n=-1 means last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def utc_offset(dest: dict[str, Any], day: date) -> float:
    """Local UTC offset for a date, applying the EU or US DST rule."""
    base = float(dest.get("tz", 0))
    rule = dest.get("dst")
    if rule == "eu":
        start, end = _nth_sunday(day.year, 3, -1), _nth_sunday(day.year, 10, -1)
    elif rule == "us":
        start, end = _nth_sunday(day.year, 3, 2), _nth_sunday(day.year, 11, 1)
    else:
        return base
    return base + 1.0 if start <= day < end else base


def stable_seed(*parts: str) -> int:
    """Deterministic seed from string parts."""
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:4], "big")
