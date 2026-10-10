"""Open-Meteo forecast (free, no key) plus kitchen-friendly summaries."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> (description, icon name used by the frontend)
WMO = {
    0: ("Clear", "clear"),
    1: ("Mainly clear", "clear"),
    2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "fog"),
    48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "rain"),
    53: ("Drizzle", "rain"),
    55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"),
    57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm, hail", "storm"),
    99: ("Thunderstorm, hail", "storm"),
}

RAIN_POP = 50  # % chance at which an hour counts as "rainy"
RAIN_MM = 0.2


def describe(code: int | None) -> tuple[str, str]:
    return WMO.get(code if code is not None else -1, ("?", "cloud"))


def is_wet(hour: dict) -> bool:
    return (hour.get("pop") or 0) >= RAIN_POP or (hour.get("precip") or 0) >= RAIN_MM


def summarise(hourly: list[dict], now: datetime, sunset: datetime | None) -> dict:
    """Next rain and a drying hint from the hourly forecast.

    `hourly` rows have aware `time`; only hours from now are considered.
    """
    ahead = [h for h in hourly if h["time"] + timedelta(hours=1) > now][:12]
    rain_at = next((h["time"] for h in ahead if is_wet(h)), None)
    dry_until = None if rain_at else (ahead[-1]["time"] + timedelta(hours=1) if ahead else None)

    # Good drying: dry for the rest of daylight (at least 3h of it), mild, a bit of wind.
    daylight = [h for h in ahead if sunset is None or h["time"] < sunset]
    drying = (
        len(daylight) >= 3
        and not any(is_wet(h) for h in daylight)
        and sum(h["temp"] for h in daylight) / len(daylight) >= 10
        and sum(h.get("wind") or 0 for h in daylight) / len(daylight) >= 4
    )
    return {"rain_at": rain_at, "dry_until": dry_until, "drying": drying}


class Weather:
    def __init__(self, lat: float, lon: float, tz: ZoneInfo, client: httpx.AsyncClient):
        self.lat, self.lon, self.tz, self.client = lat, lon, tz, client

    async def forecast(self) -> dict:
        resp = await self.client.get(
            URL,
            params={
                "latitude": self.lat,
                "longitude": self.lon,
                "timezone": str(self.tz),
                "forecast_days": 7,
                "wind_speed_unit": "mph",
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
                "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,precipitation_sum,sunrise,sunset",
            },
        )
        resp.raise_for_status()
        return parse(resp.json(), self.tz)


def parse(body: dict, tz: ZoneInfo) -> dict:
    """Open-Meteo response (local naive times) -> normalised dict with aware times."""
    t = lambda s: datetime.fromisoformat(s).replace(tzinfo=tz)  # noqa: E731
    h = body["hourly"]
    hourly = [
        {
            "time": t(h["time"][i]),
            "temp": h["temperature_2m"][i],
            "pop": h["precipitation_probability"][i],
            "precip": h["precipitation"][i],
            "code": h["weather_code"][i],
            "wind": h["wind_speed_10m"][i],
            "day": bool(h["is_day"][i]),
        }
        for i in range(len(h["time"]))
    ]
    d = body["daily"]
    daily = [
        {
            "date": d["time"][i],
            "code": d["weather_code"][i],
            "max": d["temperature_2m_max"][i],
            "min": d["temperature_2m_min"][i],
            "pop": d["precipitation_probability_max"][i],
            "precip": d["precipitation_sum"][i],
            "sunrise": t(d["sunrise"][i]),
            "sunset": t(d["sunset"][i]),
        }
        for i in range(len(d["time"]))
    ]
    c = body["current"]
    return {
        "current": {
            "temp": c["temperature_2m"],
            "feels": c["apparent_temperature"],
            "code": c["weather_code"],
            "wind": c["wind_speed_10m"],
            "day": bool(c["is_day"]),
        },
        "hourly": hourly,
        "daily": daily,
    }


def view(fc: dict, now: datetime) -> dict:
    """Forecast -> JSON for the page: current, summary, next 24h, 7 days."""
    today = fc["daily"][0] if fc["daily"] else None
    summary = summarise(fc["hourly"], now, today["sunset"] if today else None)
    iso = lambda x: x.isoformat() if x else None  # noqa: E731

    def with_desc(row: dict) -> dict:
        desc, icon = describe(row.get("code"))
        if icon == "clear" and row.get("day") is False:
            icon = "night"
        return {**row, "desc": desc, "icon": icon}

    hours = [h for h in fc["hourly"] if h["time"] + timedelta(hours=1) > now][:24]
    return {
        "current": with_desc(fc["current"]),
        "today": {"max": today["max"], "min": today["min"], "pop": today["pop"]} if today else None,
        "rain_at": iso(summary["rain_at"]),
        "dry_until": iso(summary["dry_until"]),
        "drying": summary["drying"],
        "hourly": [{**with_desc(h), "time": iso(h["time"])} for h in hours],
        "daily": [
            {**with_desc(d), "sunrise": iso(d["sunrise"]), "sunset": iso(d["sunset"])} for d in fc["daily"]
        ],
    }
