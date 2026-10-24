"""Fake data for DEMO=1, so the screen can be previewed without any API keys."""

import math
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .agile import SLOT, Rate, Reading, local_midnight


def _price(t: datetime, tz: ZoneInfo) -> float:
    local = t.astimezone(tz)
    h = local.hour + local.minute / 60
    rnd = random.Random(int(t.timestamp()))
    base = 18 + 6 * math.sin((h - 9) / 24 * 2 * math.pi)
    if 16 <= h < 19:
        base += 14  # Agile evening peak
    if 1 <= h < 5:
        base -= 9
    if local.weekday() == 6 and 13 <= h < 15:
        base -= 20  # occasional plunge pricing
    return round(base + rnd.uniform(-3, 3), 2)


def unit_rates(tz: ZoneInfo, days_back: int) -> list[Rate]:
    now = datetime.now(tz)
    start = local_midnight(now.date() - timedelta(days=days_back), tz)
    # Tomorrow's prices "publish" at 16:00, like the real thing.
    end_day = now.date() + timedelta(days=2 if now.hour >= 16 else 1)
    end = local_midnight(end_day, tz)
    rates, t = [], start.astimezone(timezone.utc)
    while t < end:
        rates.append(Rate(t, t + SLOT, _price(t, tz)))
        t += SLOT
    return rates


def standing(tz: ZoneInfo) -> list[Rate]:
    return [Rate(datetime(2024, 1, 1, tzinfo=tz), None, 47.85)]


def readings(tz: ZoneInfo, days_back: int) -> list[Reading]:
    today = local_midnight(datetime.now(tz).date(), tz)
    start = local_midnight(today.date() - timedelta(days=days_back), tz)
    out, t = [], start.astimezone(timezone.utc)
    while t < today:  # T+1: nothing for today yet
        h = t.astimezone(tz).hour
        rnd = random.Random(int(t.timestamp()) + 7)
        kwh = 0.12 + (0.5 if h in (7, 18, 19) else 0) + rnd.uniform(0, 0.15)
        out.append(Reading(t, round(kwh, 3)))
        t += SLOT
    return out


_REQUESTS = [
    ("Wicked", "2024", "movie", "pending", "Sam"),
    ("Andor", "2022", "tv", "processing", "Alex"),
    ("The Wild Robot", "2024", "movie", "available", "Sam"),
    ("Blue Lights", "2023", "tv", "partial", "Alex"),
    ("Nosferatu", "2024", "movie", "declined", "Jo"),
    ("Moana 2", "2024", "movie", "approved", "Jo"),
    ("Shrinking", "2023", "tv", "available", "Sam"),
    ("A Complete Unknown", "2024", "movie", "pending", "Alex"),
    ("Silo", "2023", "tv", "available", "Jo"),
]
_FILTER_MATCH = {
    "pending": {"pending"},
    "approved": {"approved", "processing", "partial", "available"},
    "processing": {"approved", "processing"},
    "available": {"available", "partial"},
    "unavailable": {"approved", "processing", "pending"},
    "failed": {"failed"},
}


def seerr_counts() -> dict:
    statuses = [r[3] for r in _REQUESTS]
    return {
        "total": len(statuses),
        "pending": statuses.count("pending"),
        "approved": sum(s in _FILTER_MATCH["approved"] for s in statuses),
        "declined": statuses.count("declined"),
        "processing": sum(s in _FILTER_MATCH["processing"] for s in statuses),
        "available": sum(s in _FILTER_MATCH["available"] for s in statuses),
    }


def seerr_requests(take: int = 6, skip: int = 0, filter: str = "all") -> dict:
    now = datetime.now().astimezone()
    rows = [
        {
            "title": t,
            "year": y,
            "type": mt,
            "status": st,
            "user": u,
            "created": (now - timedelta(hours=9 * i + 2)).isoformat(),
            "poster": None,
            "overview": "Demo request - real data appears once SEERR_URL is set.",
        }
        for i, (t, y, mt, st, u) in enumerate(_REQUESTS)
        if filter == "all" or st in _FILTER_MATCH.get(filter, set())
    ]
    return {"results": rows[skip : skip + take], "total": len(rows), "skip": skip, "take": take}


def weather(tz: ZoneInfo) -> dict:
    """Forecast in the shape sources.weather.parse() returns: dry morning, rain from mid-afternoon."""
    now = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    start = now.replace(hour=0)
    hourly = []
    for i in range(24 * 7):
        t = start + timedelta(hours=i)
        rnd = random.Random(int(t.timestamp()) + 3)
        temp = 11 + 5 * math.sin((t.hour - 9) / 24 * 2 * math.pi) + rnd.uniform(-1, 1)
        wet = (15 <= t.hour <= 19 and t.date() == now.date()) or rnd.random() < 0.12
        hourly.append(
            {
                "time": t,
                "temp": round(temp, 1),
                "pop": rnd.randint(55, 90) if wet else rnd.randint(0, 25),
                "precip": round(rnd.uniform(0.3, 2.5), 1) if wet else 0.0,
                "code": 61 if wet else (2 if rnd.random() < 0.5 else 1),
                "wind": round(rnd.uniform(5, 14), 1),
                "day": 7 <= t.hour < 19,
            }
        )
    daily = []
    for d in range(7):
        rows = hourly[d * 24 : (d + 1) * 24]
        day = rows[0]["time"]
        daily.append(
            {
                "date": day.date().isoformat(),
                "code": 61 if any(r["code"] == 61 for r in rows[8:20]) else 2,
                "max": max(r["temp"] for r in rows),
                "min": min(r["temp"] for r in rows),
                "pop": max(r["pop"] for r in rows),
                "precip": round(sum(r["precip"] for r in rows), 1),
                "sunrise": day.replace(hour=7, minute=12),
                "sunset": day.replace(hour=18, minute=24),
            }
        )
    cur = next(h for h in hourly if h["time"] == now)
    return {
        "current": {"temp": cur["temp"], "feels": cur["temp"] - 2, "code": cur["code"], "wind": cur["wind"], "day": cur["day"]},
        "hourly": hourly,
        "daily": daily,
    }


def nas() -> dict:
    return {
        "state": "warn",
        "pools": [
            {"name": "tank", "status": "ONLINE", "healthy": True, "used_pct": 71.4},
            {"name": "apps", "status": "ONLINE", "healthy": True, "used_pct": 38.0},
        ],
        "alerts": [
            {"level": "WARNING", "text": "Demo: scrub of pool 'tank' found 0 errors but took longer than usual."},
            {"level": "INFO", "text": "Demo: an update is available."},
        ],
        "version": "TrueNAS-25.10 (demo)",
        "uptime_s": 12 * 86400,
    }


_requested: set[tuple[str, int]] = set()

_DISCOVER = [
    ("movie", 1, "Sinners", "2025", "available"),
    ("tv", 2, "The Last of Us", "2023", "partial"),
    ("movie", 3, "Mickey 17", "2025", "unknown"),
    ("movie", 4, "Thunderbolts*", "2025", "unknown"),
    ("tv", 5, "Adolescence", "2025", "available"),
    ("movie", 6, "Wicked: For Good", "2025", "pending"),
    ("tv", 7, "The Pitt", "2025", "unknown"),
    ("movie", 8, "Superman", "2025", "unknown"),
    ("movie", 9, "The Gorge", "2025", "unknown"),
    ("tv", 10, "Slow Horses", "2022", "processing"),
    ("movie", 11, "Paddington 2", "2017", "unknown"),
    ("movie", 12, "Paddington", "2014", "available"),
]


def seerr_discover(query: str | None = None, page: int = 1) -> dict:
    rows = [
        {
            "tmdb_id": i,
            "type": t,
            "title": title,
            "year": y,
            "poster": None,
            "overview": "Demo result - real results appear once SEERR_URL is set.",
            "status": "pending" if (t, i) in _requested else st,
        }
        for t, i, title, y, st in _DISCOVER
        if not query or query.lower() in title.lower()
    ]
    return {"results": rows, "page": 1, "pages": 1}


def seerr_request(media_type: str, tmdb_id: int) -> dict:
    _requested.add((media_type, tmdb_id))
    return {"status": "pending"}


TODOS = ["Book boiler service", "Milk + eggs", "Return parcel", "Bin day: recycling", "Descale kettle"]
