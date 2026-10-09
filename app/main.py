"""Kitchen monitor: polls Octopus / Jellyfin / Seerr and serves the kiosk page.

The Pi only renders what this serves. Overview data is polled in the
background and cached; detail views (tapping a panel) fetch on demand.
"""

import asyncio
import hashlib
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agile, config, demo
from .agile import SLOT
from .sources.jellyfin import Jellyfin
from .sources.octopus import Octopus
from .sources.seerr import FILTERS, Seerr

log = logging.getLogger("kitchen")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

S = config.load()
TZ = ZoneInfo(S.tz)
STATIC = Path(__file__).parent.parent / "static"
HISTORY_DAYS = 31  # usage/rates history kept for the energy detail view

# Changes whenever the frontend files change, so the Pi reloads itself after a deploy.
BUILD = hashlib.sha1(
    b"".join(p.read_bytes() for p in sorted(STATIC.rglob("*")) if p.is_file())
).hexdigest()[:10]


@dataclass
class Source:
    enabled: bool
    data: Any = None
    updated: datetime | None = None
    error: str | None = None

    def meta(self) -> dict:
        return {
            "enabled": self.enabled,
            "updated": self.updated.isoformat() if self.updated else None,
            "error": self.error,
        }


octopus_on = S.demo or S.octopus_enabled
jellyfin_on = S.demo or S.jellyfin_enabled
seerr_on = S.demo or S.seerr_enabled

store = {
    "rates": Source(octopus_on),
    "usage": Source(octopus_on),
    "sessions": Source(jellyfin_on),
    "latest": Source(jellyfin_on),
    "seerr": Source(seerr_on),
}

client: httpx.AsyncClient
octopus: Octopus
jellyfin: Jellyfin
seerr: Seerr


def history_start() -> datetime:
    return agile.local_midnight(datetime.now(TZ).date() - timedelta(days=HISTORY_DAYS), TZ)


# --- pollers -----------------------------------------------------------------


async def fetch_rates() -> dict:
    if S.demo:
        return {"unit": demo.unit_rates(TZ, HISTORY_DAYS), "standing": demo.standing(TZ)}
    await octopus.discover()  # picks up tariff changes
    start = history_start()
    end = agile.local_midnight(datetime.now(TZ).date() + timedelta(days=2), TZ)
    unit, standing = await asyncio.gather(
        octopus.unit_rates(start, end), octopus.standing_charges(start, end)
    )
    return {"unit": unit, "standing": standing}


async def fetch_usage():
    if S.demo:
        return demo.readings(TZ, HISTORY_DAYS)
    return await octopus.consumption(history_start(), datetime.now(timezone.utc))


async def fetch_sessions():
    return demo.sessions() if S.demo else await jellyfin.sessions()


async def fetch_latest():
    return demo.latest(limit=6)["results"] if S.demo else (await jellyfin.latest(limit=6))["results"]


async def fetch_seerr() -> dict:
    if S.demo:
        return {"counts": demo.seerr_counts(), "requests": demo.seerr_requests(take=5)["results"]}
    counts, reqs = await asyncio.gather(seerr.counts(), seerr.requests(take=5))
    return {"counts": counts, "requests": reqs["results"]}


async def poll(name: str, fetch: Callable[[], Awaitable[Any]], every: float):
    src = store[name]
    while True:
        try:
            src.data = await fetch()
            src.updated = datetime.now(timezone.utc)
            src.error = None
        except Exception as e:  # keep serving the last good data
            src.error = f"{type(e).__name__}: {e}"[:200]
            log.warning("%s poll failed: %s", name, src.error)
        await asyncio.sleep(every)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, octopus, jellyfin, seerr
    client = httpx.AsyncClient(timeout=15)
    octopus, jellyfin, seerr = Octopus(S, client), Jellyfin(S, client), Seerr(S, client)
    jobs = [
        ("rates", fetch_rates, 15 * 60),
        ("usage", fetch_usage, 60 * 60),
        ("sessions", fetch_sessions, 10),
        ("latest", fetch_latest, 10 * 60),
        ("seerr", fetch_seerr, 2 * 60),
    ]
    tasks = [asyncio.create_task(poll(n, f, t)) for n, f, t in jobs if store[n].enabled]
    log.info("started (build %s, demo=%s)", BUILD, S.demo)
    yield
    for t in tasks:
        t.cancel()
    await client.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- helpers -----------------------------------------------------------------


def slot_json(r: agile.Rate) -> dict:
    return {
        "start": r.start.astimezone(TZ).isoformat(),
        "end": r.end.astimezone(TZ).isoformat() if r.end else None,
        "price": round(r.price, 2),
        "band": agile.band(r.price, S.price_cheap, S.price_expensive),
    }


def stats(slots: list[agile.Rate]) -> dict | None:
    if not slots:
        return None
    prices = [s.price for s in slots]
    return {
        "min": round(min(prices), 2),
        "max": round(max(prices), 2),
        "avg": round(sum(prices) / len(prices), 2),
    }


def is_night(now: datetime) -> bool:
    if not (S.night_start and S.night_end):
        return False
    hm = now.strftime("%H:%M")
    if S.night_start <= S.night_end:
        return S.night_start <= hm < S.night_end
    return hm >= S.night_start or hm < S.night_end


def day_slots(unit: list[agile.Rate], d: date) -> list[agile.Rate]:
    return agile.half_hours(
        unit, agile.local_midnight(d, TZ), agile.local_midnight(d + timedelta(days=1), TZ)
    )


def usage_days() -> list[dict]:
    rates = store["rates"].data or {"unit": [], "standing": []}
    return agile.daily_usage(store["usage"].data or [], rates["unit"], rates["standing"], TZ)


def energy_overview(now: datetime) -> dict:
    out: dict[str, Any] = {
        **store["rates"].meta(),
        "usage_meta": store["usage"].meta(),
        "thresholds": {"cheap": S.price_cheap, "expensive": S.price_expensive},
    }
    rates = store["rates"].data
    if not rates:
        return out
    unit = rates["unit"]
    cur = agile.floor_slot(now)
    today = now.date()
    horizon = min(
        agile.last_known(unit) or agile.local_midnight(today + timedelta(days=1), TZ),
        agile.local_midnight(today + timedelta(days=2), TZ),
    )
    chart = agile.half_hours(unit, cur - timedelta(hours=2), horizon)
    upcoming = [s for s in chart if s.start >= cur]
    tomorrow = day_slots(unit, today + timedelta(days=1))
    tomorrow_complete = len(tomorrow) == agile.day_slot_count(today + timedelta(days=1), TZ)

    out.update(
        current=slot_json(upcoming[0]) if upcoming and upcoming[0].start == cur else None,
        next=slot_json(upcoming[1]) if len(upcoming) > 1 else None,
        chart=[slot_json(s) for s in chart],
        today=stats(day_slots(unit, today)),
        tomorrow_available=tomorrow_complete,
        windows=[
            {"hours": h, **slot_json(w)}
            for h in S.cheapest_windows
            if (w := agile.cheapest_window(upcoming, h))
        ],
        usage=usage_days()[-S.usage_days :],
        appliances=appliance_advice(unit, now, horizon, tomorrow_complete),
    )
    return out


def appliance_advice(
    unit: list[agile.Rate], now: datetime, horizon: datetime, tomorrow_known: bool
) -> list[dict]:
    """When to start each configured appliance, in delay-timer steps."""
    out = []
    step = timedelta(hours=S.appliance_step_hours)
    for a in S.appliances:
        best = agile.best_start(unit, now, timedelta(hours=a.hours), step, horizon)
        # Before tomorrow's prices land (~4pm) the overnight cheap slots are unknown,
        # so an afternoon recommendation may get better later.
        partial = not tomorrow_known and now.hour >= 12
        item: dict[str, Any] = {"name": a.name, "hours": a.hours, "partial": partial}
        if best:
            saving = None
            if a.kwh and best["now_avg"] is not None:
                saving = round(a.kwh * (best["now_avg"] - best["avg"]), 1)
            item.update(
                start=best["start"].astimezone(TZ).isoformat(),
                end=best["end"].astimezone(TZ).isoformat(),
                delay_h=round(best["delay"].total_seconds() / 3600, 2),
                avg=round(best["avg"], 2),
                now_avg=round(best["now_avg"], 2) if best["now_avg"] is not None else None,
                saving_p=saving,
                band=agile.band(best["avg"], S.price_cheap, S.price_expensive),
            )
        out.append(item)
    return out


# --- routes ------------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
async def healthz():
    return {"ok": True, "build": BUILD}


@app.get("/api/state")
async def state():
    now = datetime.now(TZ)
    return {
        "build": BUILD,
        "now": now.isoformat(),
        "tz": S.tz,
        "demo": S.demo,
        "night": is_night(now),
        "energy": energy_overview(now),
        "jellyfin": {
            **store["sessions"].meta(),
            "sessions": store["sessions"].data or [],
            "latest": store["latest"].data or [],
        },
        "seerr": {**store["seerr"].meta(), **(store["seerr"].data or {})},
        "timers": timer_list(),
        "timer_labels": S.timer_labels,
    }


@app.get("/api/energy/prices")
async def energy_prices(day: date | None = None):
    """All half-hour prices for one day (default today)."""
    rates = store["rates"].data
    if not rates:
        raise HTTPException(503, "No price data yet")
    d = day or datetime.now(TZ).date()
    slots = day_slots(rates["unit"], d)
    return {"date": d.isoformat(), "slots": [slot_json(s) for s in slots], "stats": stats(slots)}


@app.get("/api/energy/usage")
async def energy_usage():
    """Daily kWh and cost for the history window."""
    return {"days": usage_days(), "meta": store["usage"].meta()}


@app.get("/api/energy/usage/{day}")
async def energy_usage_day(day: date):
    """Half-hourly kWh, price and cost for one day."""
    readings = store["usage"].data or []
    unit = (store["rates"].data or {}).get("unit", [])
    rows = []
    for r in readings:
        if r.start.astimezone(TZ).date() != day:
            continue
        rate = agile.rate_at(unit, r.start)
        rows.append(
            {
                "start": r.start.astimezone(TZ).isoformat(),
                "kwh": r.kwh,
                "price": round(rate.price, 2) if rate else None,
                "cost_p": round(r.kwh * rate.price, 2) if rate else None,
            }
        )
    summary = next((d for d in usage_days() if d["date"] == day.isoformat()), None)
    return {"date": day.isoformat(), "slots": rows, "summary": summary}


JELLYFIN_TYPES = {"all": "Movie,Episode", "movies": "Movie", "episodes": "Episode"}


@app.get("/api/jellyfin/latest")
async def jellyfin_latest(
    kind: str = "all", skip: int = Query(0, ge=0), take: int = Query(12, ge=1, le=50)
):
    if not jellyfin_on:
        raise HTTPException(404, "Jellyfin not configured")
    types = JELLYFIN_TYPES.get(kind, JELLYFIN_TYPES["all"])
    try:
        if S.demo:
            return demo.latest(limit=take, skip=skip, types=types)
        return await jellyfin.latest(limit=take, skip=skip, types=types)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Jellyfin: {e}")


@app.get("/api/seerr/requests")
async def seerr_requests(
    filter: str = "all", skip: int = Query(0, ge=0), take: int = Query(10, ge=1, le=50)
):
    if not seerr_on:
        raise HTTPException(404, "Seerr not configured")
    if filter not in FILTERS:
        raise HTTPException(400, f"filter must be one of {FILTERS}")
    try:
        if S.demo:
            return demo.seerr_requests(take=take, skip=skip, filter=filter)
        return await seerr.requests(take=take, skip=skip, filter=filter)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Seerr: {e}")


_img_cache: dict[str, tuple[bytes, str]] = {}
ITEM_ID = re.compile(r"[0-9a-f]{32}")


@app.get("/img/jellyfin/{item_id}")
async def jellyfin_image(item_id: str):
    """Poster proxy, so the Pi never needs the Jellyfin token."""
    if S.demo or not jellyfin_on or not ITEM_ID.fullmatch(item_id):
        raise HTTPException(404)
    if item_id not in _img_cache:
        try:
            _img_cache[item_id] = await jellyfin.image(item_id)
        except httpx.HTTPError:
            raise HTTPException(404)
        if len(_img_cache) > 200:
            _img_cache.pop(next(iter(_img_cache)))
    body, ctype = _img_cache[item_id]
    return Response(body, media_type=ctype, headers={"Cache-Control": "max-age=86400"})


# --- kitchen timers ------------------------------------------------------------
# Kept on the server so they survive a page reload and show on every screen.
# In memory only: a container restart clears them.

TIMER_KEEP_AFTER_DONE = timedelta(minutes=15)  # forget un-dismissed alarms eventually


@dataclass
class Timer:
    id: str
    label: str
    seconds: int
    ends: datetime

    def json(self) -> dict:
        return {"id": self.id, "label": self.label, "seconds": self.seconds, "ends": self.ends.isoformat()}


timers: dict[str, Timer] = {}


class TimerIn(BaseModel):
    seconds: int = Field(ge=1, le=24 * 3600)
    label: str = Field("", max_length=40)


class TimerAdd(BaseModel):
    seconds: int = Field(ge=-3600, le=3600)


def timer_list() -> list[dict]:
    now = datetime.now(timezone.utc)
    for t in [t for t in timers.values() if now - t.ends > TIMER_KEEP_AFTER_DONE]:
        del timers[t.id]
    return [t.json() for t in sorted(timers.values(), key=lambda t: t.ends)]


@app.get("/api/timers")
async def get_timers():
    return timer_list()


@app.post("/api/timers")
async def add_timer(body: TimerIn):
    if len(timers) >= 12:
        raise HTTPException(400, "Too many timers")
    t = Timer(
        id=uuid.uuid4().hex[:8],
        label=body.label.strip(),
        seconds=body.seconds,
        ends=datetime.now(timezone.utc) + timedelta(seconds=body.seconds),
    )
    timers[t.id] = t
    return timer_list()


@app.post("/api/timers/{timer_id}/add")
async def extend_timer(timer_id: str, body: TimerAdd):
    t = timers.get(timer_id)
    if not t:
        raise HTTPException(404)
    now = datetime.now(timezone.utc)
    # Adding time to a finished timer restarts it from now.
    t.ends = max(t.ends, now) + timedelta(seconds=body.seconds)
    t.seconds = max(1, t.seconds + body.seconds)
    return timer_list()


@app.delete("/api/timers/{timer_id}")
async def delete_timer(timer_id: str):
    timers.pop(timer_id, None)
    return timer_list()
