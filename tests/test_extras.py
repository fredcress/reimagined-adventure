from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.sources.truenas import summarise as nas_summary
from app.sources.weather import parse, summarise, view

LONDON = ZoneInfo("Europe/London")


def hours(start, rows):
    return [
        {"time": start + timedelta(hours=i), "temp": t, "pop": p, "precip": mm, "wind": w, "code": 1, "day": True}
        for i, (t, p, mm, w) in enumerate(rows)
    ]


def test_weather_next_rain_and_drying():
    now = datetime(2026, 6, 1, 9, 20, tzinfo=LONDON)
    start = now.replace(minute=0)
    dry = hours(start, [(16, 10, 0, 8)] * 12)
    s = summarise(dry, now, sunset=start + timedelta(hours=11))
    assert s["rain_at"] is None and s["drying"]
    assert s["dry_until"] == start + timedelta(hours=12)

    wet = hours(start, [(16, 10, 0, 8)] * 4 + [(14, 70, 1.2, 8)] * 8)
    s = summarise(wet, now, sunset=start + timedelta(hours=11))
    assert s["rain_at"] == start + timedelta(hours=4)
    assert not s["drying"]

    cold = hours(start, [(6, 0, 0, 8)] * 12)
    assert not summarise(cold, now, sunset=None)["drying"]


def test_weather_parse_and_view_shape():
    body = {
        "current": {"temperature_2m": 12.5, "apparent_temperature": 10.0, "weather_code": 0, "wind_speed_10m": 6, "is_day": 0},
        "hourly": {
            "time": ["2026-06-01T09:00", "2026-06-01T10:00"],
            "temperature_2m": [12, 13],
            "precipitation_probability": [5, 80],
            "precipitation": [0, 1.0],
            "weather_code": [1, 61],
            "wind_speed_10m": [5, 6],
            "is_day": [1, 1],
        },
        "daily": {
            "time": ["2026-06-01"],
            "weather_code": [61],
            "temperature_2m_max": [15],
            "temperature_2m_min": [9],
            "precipitation_probability_max": [80],
            "precipitation_sum": [3.1],
            "sunrise": ["2026-06-01T04:45"],
            "sunset": ["2026-06-01T21:10"],
        },
    }
    fc = parse(body, LONDON)
    assert fc["hourly"][0]["time"].tzinfo is LONDON
    v = view(fc, datetime(2026, 6, 1, 9, 30, tzinfo=LONDON))
    assert v["current"]["icon"] == "night"  # clear but is_day=0
    assert v["rain_at"].startswith("2026-06-01T10:00")
    assert len(v["hourly"]) == 2 and v["hourly"][1]["desc"] == "Light rain"


def test_nas_summary_states():
    ok = nas_summary([{"name": "tank", "status": "ONLINE", "healthy": True, "size": 100, "allocated": 50}], [], {})
    assert ok["state"] == "ok" and ok["pools"][0]["used_pct"] == 50.0

    full = nas_summary([{"name": "tank", "status": "ONLINE", "healthy": True, "size": 100, "allocated": 95}], [], {})
    assert full["state"] == "warn"

    alerts = [
        {"level": "INFO", "formatted": "update available"},
        {"level": "WARNING", "formatted": "SMART warning", "dismissed": False},
        {"level": "CRITICAL", "formatted": "old", "dismissed": True},
    ]
    warn = nas_summary([], alerts, None)
    assert warn["state"] == "warn"
    assert [a["text"] for a in warn["alerts"]] == ["SMART warning", "update available"]

    degraded = nas_summary([{"name": "tank", "status": "DEGRADED", "healthy": False}], [], {})
    assert degraded["state"] == "error"


def test_price_summary_mentions_plunge_and_appliances(monkeypatch):
    import app.main as m
    from app.agile import SLOT, Rate

    start = datetime(2026, 6, 2, tzinfo=LONDON).astimezone(timezone.utc)
    prices = [20.0] * 26 + [-2.0, -1.0] + [20.0] * 20
    slots = [Rate(start + i * SLOT, start + (i + 1) * SLOT, p) for i, p in enumerate(prices)]
    now = start - timedelta(hours=6)
    title, message, tags = m.price_summary(slots, slots, now)
    assert "plunge" in title
    assert "PLUNGE (paid to use): 13:00-14:00, as low as -2.0p" in message
    assert "Dishwasher" in message or not m.S.appliances
