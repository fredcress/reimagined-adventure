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


def sessions() -> list[dict]:
    return [
        {
            "user": "living room",
            "device": "Living Room TV",
            "client": "Jellyfin Android TV",
            "type": "Episode",
            "title": "Severance",
            "subtitle": "S2E4 · Woe's Hollow",
            "paused": False,
            "progress": 0.37,
            "remaining_s": 1960,
            "image": None,
        }
    ]


_LATEST = [
    ("Episode", "The Bear", "S3E2 · Next"),
    ("Movie", "Dune: Part Two", "2024"),
    ("Episode", "Slow Horses", "S4E6 · Hello Goodbye"),
    ("Movie", "Paddington in Peru", "2024"),
    ("Movie", "Conclave", "2024"),
    ("Episode", "Taskmaster", "S18E1 · Bus Wankers"),
    ("Movie", "The Wild Robot", "2024"),
    ("Episode", "Andor", "S2E1 · One Year Later"),
    ("Movie", "Flow", "2024"),
    ("Episode", "Blue Lights", "S2E5 · Episode 5"),
    ("Movie", "Anora", "2024"),
    ("Movie", "Heretic", "2024"),
    ("Episode", "Severance", "S2E3 · Who Is Alive?"),
    ("Movie", "Gladiator II", "2024"),
]


def latest(limit: int = 8, skip: int = 0, types: str = "Movie,Episode") -> dict:
    now = datetime.now().astimezone()
    wanted = set(types.split(","))
    items = [
        {
            "type": t,
            "title": title,
            "subtitle": sub,
            "added": (now - timedelta(hours=5 * i + 1)).isoformat(),
            "image": None,
            "overview": "Demo item - real data appears once JELLYFIN_URL is set.",
        }
        for i, (t, title, sub) in enumerate(_LATEST)
        if t in wanted
    ]
    return {"results": items[skip : skip + limit], "total": len(items), "skip": skip, "take": limit}


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
