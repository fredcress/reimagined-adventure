"""Kitchen monitor: polls Octopus / Seerr / weather / TrueNAS and serves the kiosk page.

The Pi only renders what this serves. Overview data is polled in the
background and cached; detail views (tapping a panel) fetch on demand.
"""

import asyncio
import hashlib
import logging
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

from . import agile, config, demo, wifi
from .agile import SLOT
from .notify import Ntfy
from .todos import TodoStore
from .sources import weather as wx
from .sources.octopus import Octopus
from .sources.seerr import FILTERS, Seerr
from .sources.truenas import TrueNAS

log = logging.getLogger("kitchen")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

S = config.load()
TZ = ZoneInfo(S.tz)
STATIC = Path(__file__).parent.parent / "static"
HISTORY_DAYS = 31  # usage/rates history kept for the energy detail view
# Demo mode keeps its to-do list in memory so it never touches real data.
todos = TodoStore(None if S.demo else Path(S.data_dir) / "todos.json", TZ)
if S.demo:
    for _text, _repeat in demo.TODOS:
        todos.add(_text, _repeat)
    todos.update(todos.items[-1]["id"], done=True)

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
seerr_on = S.demo or S.seerr_enabled
weather_on = S.demo or S.weather_enabled
nas_on = S.demo or S.truenas_enabled

store = {
    "rates": Source(octopus_on),
    "usage": Source(octopus_on),
    "seerr": Source(seerr_on),
    "weather": Source(weather_on),
    "nas": Source(nas_on),
}

client: httpx.AsyncClient
octopus: Octopus
seerr: Seerr
weather: wx.Weather | None = None
truenas: TrueNAS | None = None
ntfy: Ntfy


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
    await push_tomorrow_prices(unit)
    return {"unit": unit, "standing": standing}


_prices_pushed_for: date | None = None


async def push_tomorrow_prices(unit: list[agile.Rate]):
    """Once tomorrow's prices are published, push a summary (once per day)."""
    global _prices_pushed_for
    now = datetime.now(TZ)
    tomorrow = now.date() + timedelta(days=1)
    if not (ntfy.enabled and S.ntfy_prices) or _prices_pushed_for == tomorrow or now.hour >= 22:
        return
    slots = day_slots(unit, tomorrow)
    if len(slots) < agile.day_slot_count(tomorrow, TZ):
        return
    _prices_pushed_for = tomorrow
    title, message, tags = price_summary(unit, slots, now)
    await ntfy.send(title, message, tags=tags)


def price_summary(unit: list[agile.Rate], slots: list[agile.Rate], now: datetime) -> tuple[str, str, list[str]]:
    hm = lambda t: t.astimezone(TZ).strftime("%H:%M")  # noqa: E731
    st = stats(slots)
    lines = [f"Range {st['min']:.1f}p to {st['max']:.1f}p, average {st['avg']:.1f}p"]
    if w := agile.cheapest_window(slots, 3):
        lines.append(f"Cheapest 3h: {hm(w.start)}-{hm(w.end)} at {w.price:.1f}p")
    plunge = [s for s in slots if s.price <= 0]
    if plunge:
        runs, run = [], [plunge[0]]
        for s in plunge[1:]:
            if s.start == run[-1].end:
                run.append(s)
            else:
                runs.append(run)
                run = [s]
        runs.append(run)
        spans = ", ".join(f"{hm(r[0].start)}-{hm(r[-1].end)}" for r in runs)
        lines.append(f"PLUNGE (paid to use): {spans}, as low as {min(s.price for s in plunge):.1f}p")
    horizon = agile.last_known(unit) or slots[-1].end
    for a in S.appliances:
        step = timedelta(hours=S.appliance_step_hours)
        if b := agile.best_start(unit, now, timedelta(hours=a.hours), step, horizon, finish=a.finish):
            when = f"start {hm(b['start'])}" + (f", done {hm(b['end'])}" if a.finish else "")
            lines.append(f"{a.name}: {when} ({b['avg']:.1f}p avg)")
    title = "Tomorrow's prices are out" + (" - plunge pricing!" if plunge else "")
    return title, "\n".join(lines), ["zap"] + (["tada"] if plunge else [])


async def fetch_usage():
    if S.demo:
        return demo.readings(TZ, HISTORY_DAYS)
    return await octopus.consumption(history_start(), datetime.now(timezone.utc))


async def fetch_seerr() -> dict:
    if S.demo:
        return {"counts": demo.seerr_counts(), "requests": demo.seerr_requests(take=5)["results"]}
    counts, reqs = await asyncio.gather(seerr.counts(), seerr.requests(take=5))
    return {"counts": counts, "requests": reqs["results"]}


async def fetch_weather() -> dict:
    return demo.weather(TZ) if S.demo and not S.weather_enabled else await weather.forecast()


_nas_last_state: str | None = None


async def fetch_nas() -> dict:
    global _nas_last_state
    health = demo.nas() if S.demo else await truenas.health()
    state = health["state"]
    if S.ntfy_nas and ntfy.enabled and not S.demo and state != _nas_last_state:
        if state != "ok":
            text = "\n".join(f"[{a['level']}] {a['text']}" for a in health["alerts"][:5]) or "A pool is not healthy."
            await ntfy.send(f"NAS {state}", text, tags=["warning"], priority=4 if state == "error" else 3)
        elif _nas_last_state is not None:
            await ntfy.send("NAS back to OK", "All pools healthy, no warnings.", tags=["white_check_mark"])
    _nas_last_state = state
    return health


async def watch_timers():
    """Push finished timers to phones (for when nobody's in the kitchen)."""
    pushed: set[str] = set()
    while True:
        now = datetime.now(timezone.utc)
        for t in list(timers.values()):
            if t.ends <= now and t.id not in pushed:
                pushed.add(t.id)
                await ntfy.send(f"{t.label or 'Timer'} done", f"{t.label or 'Your'} timer has finished.", tags=["alarm_clock"], priority=4)
        pushed &= set(timers)
        await asyncio.sleep(2)


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
    global client, octopus, seerr, weather, truenas, ntfy
    client = httpx.AsyncClient(timeout=15)
    octopus, seerr = Octopus(S, client), Seerr(S, client)
    ntfy = Ntfy(S.ntfy_url, S.ntfy_topic, S.ntfy_token, client)
    if S.weather_enabled:
        weather = wx.Weather(S.latitude, S.longitude, TZ, client)
    if S.truenas_enabled:
        try:
            truenas = TrueNAS(S.truenas_url, S.truenas_api_key, S.truenas_verify_ssl)
        except ValueError as e:
            store["nas"].error = str(e)
            store["nas"].enabled = False
    jobs = [
        ("rates", fetch_rates, 15 * 60),
        ("usage", fetch_usage, 60 * 60),
        ("seerr", fetch_seerr, 2 * 60),
        ("weather", fetch_weather, 15 * 60),
        ("nas", fetch_nas, 5 * 60),
    ]
    tasks = [asyncio.create_task(poll(n, f, t)) for n, f, t in jobs if store[n].enabled]
    if ntfy.enabled and S.ntfy_timers:
        tasks.append(asyncio.create_task(watch_timers()))
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


def typical_price(unit: list[agile.Rate], before: date, days: int = 30) -> float | None:
    """Mean daily average unit price over the complete days before `before`."""
    avgs = []
    for i in range(1, days + 1):
        d = before - timedelta(days=i)
        slots = day_slots(unit, d)
        if slots and len(slots) == agile.day_slot_count(d, TZ):
            avgs.append(sum(s.price for s in slots) / len(slots))
    return round(sum(avgs) / len(avgs), 2) if len(avgs) >= 3 else None


def typical_paid(days: list[dict], exclude: str | None = None) -> float | None:
    """Your usual average price paid per kWh, over complete usage days."""
    paid = [d["avg_price"] for d in days if d["complete"] and d["avg_price"] is not None and d["date"] != exclude]
    return round(sum(paid) / len(paid), 2) if len(paid) >= 3 else None


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
        typical_avg=typical_price(unit, today),
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
        best = agile.best_start(unit, now, timedelta(hours=a.hours), step, horizon, finish=a.finish)
        # Before tomorrow's prices land (~4pm) the overnight cheap slots are unknown,
        # so an afternoon recommendation may get better later.
        partial = not tomorrow_known and now.hour >= 12
        item: dict[str, Any] = {"name": a.name, "hours": a.hours, "partial": partial, "finish": a.finish}
        if best:
            saving = None
            if a.kwh and best["now_avg"] is not None:
                saving = round(a.kwh * (best["now_avg"] - best["avg"]), 1)
            item.update(
                start=best["start"].astimezone(TZ).isoformat(),
                end=best["end"].astimezone(TZ).isoformat(),
                delay_h=round(best["delay"].total_seconds() / 3600, 2),
                # what to set on a "Finish in" timer: delay + cycle, whole steps
                finish_in_h=round((best["delay"].total_seconds() / 3600) + a.hours, 2) if a.finish else None,
                avg=round(best["avg"], 2),
                now_avg=round(best["now_avg"], 2) if best["now_avg"] is not None else None,
                saving_p=saving,
                run_p=round(a.kwh * best["avg"], 1) if a.kwh else None,
                band=agile.band(best["avg"], S.price_cheap, S.price_expensive),
            )
        out.append(item)
    pick_programs(out, S.appliances)
    return out


def pick_programs(items: list[dict], appliances: list) -> None:
    """Mark the program to recommend for each machine (`best`, plus `beats` for the others).

    Programs of one machine share the first word of their name ("Dishwasher eco",
    "Dishwasher quick"). With kWh for every program the cheapest run in pence wins,
    otherwise the lowest average price. Near-ties go to the program listed first.
    """
    groups: dict[str, list[int]] = {}
    for i, a in enumerate(appliances):
        groups.setdefault(a.name.split()[0].lower(), []).append(i)
    for idx in groups.values():
        runs = [i for i in idx if items[i].get("start")]
        by_cost = all(appliances[i].kwh for i in runs)
        score = (lambda i: appliances[i].kwh * items[i]["avg"]) if by_cost else (lambda i: items[i]["avg"])
        margin = 1.0 if by_cost else 0.5
        best = idx[0]
        if runs:
            low = min(score(i) for i in runs)
            best = next(i for i in runs if score(i) <= low + margin)
        for i in idx:
            items[i]["best"] = i == best
        items[best]["beats"] = [
            {"name": items[i]["name"], "avg": items[i]["avg"], "run_p": items[i]["run_p"] if by_cost else None}
            for i in runs
            if i != best
        ]


# --- routes ------------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
async def service_worker():
    # Served from the root so it can control the whole app, not just /static.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


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
        "seerr": {**store["seerr"].meta(), **(store["seerr"].data or {})},
        "timers": timer_list(),
        "timer_labels": S.timer_labels,
        "todos": {"items": todos.list(), "error": todos.error},
        "weather": {
            **store["weather"].meta(),
            **(wx.view(store["weather"].data, now) if store["weather"].data else {}),
        },
        "nas": {**store["nas"].meta(), **(store["nas"].data or {})},
        "features": {
            "seerr_requests": seerr_on and S.seerr_allow_requests,
            "ntfy": ntfy.enabled,
            "wifi": guest_wifi() is not None,
        },
    }


@app.get("/api/energy/prices")
async def energy_prices(day: date | None = None):
    """All half-hour prices for one day (default today)."""
    rates = store["rates"].data
    if not rates:
        raise HTTPException(503, "No price data yet")
    d = day or datetime.now(TZ).date()
    slots = day_slots(rates["unit"], d)
    return {
        "date": d.isoformat(),
        "slots": [slot_json(s) for s in slots],
        "stats": stats(slots),
        "typical_avg": typical_price(rates["unit"], min(d, datetime.now(TZ).date())),
    }


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
    days = usage_days()
    summary = next((d for d in days if d["date"] == day.isoformat()), None)
    dates = [d["date"] for d in days]
    i = dates.index(day.isoformat()) if day.isoformat() in dates else None
    return {
        "date": day.isoformat(),
        "slots": rows,
        "summary": summary,
        "typical_paid": typical_paid(days, exclude=day.isoformat()),
        "prev": dates[i - 1] if i else None,
        "next": dates[i + 1] if i is not None and i + 1 < len(dates) else None,
    }


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


@app.get("/api/seerr/search")
async def seerr_search(q: str = Query(min_length=1, max_length=100), page: int = Query(1, ge=1, le=50)):
    if not seerr_on:
        raise HTTPException(404, "Seerr not configured")
    try:
        return demo.seerr_discover(q) if S.demo else await seerr.search(q, page)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Seerr: {e}")


@app.get("/api/seerr/discover")
async def seerr_discover(kind: str = "trending", page: int = Query(1, ge=1, le=50)):
    if not seerr_on:
        raise HTTPException(404, "Seerr not configured")
    if kind not in ("trending", "movies", "tv"):
        raise HTTPException(400, "kind must be trending, movies or tv")
    try:
        return demo.seerr_discover() if S.demo else await seerr.discover(kind, page)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Seerr: {e}")


class RequestIn(BaseModel):
    type: str = Field(pattern="^(movie|tv)$")
    tmdb_id: int = Field(gt=0)


@app.post("/api/seerr/request")
async def seerr_request(body: RequestIn):
    if not (seerr_on and S.seerr_allow_requests):
        raise HTTPException(403, "Requests are turned off (SEERR_ALLOW_REQUESTS)")
    try:
        if S.demo:
            return demo.seerr_request(body.type, body.tmdb_id)
        result = await seerr.request(body.type, body.tmdb_id, S.seerr_request_user_id)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Seerr: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Seerr: {e}")
    log.info("requested %s %s via kitchen screen", body.type, body.tmdb_id)
    return result


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


# --- shared to-do list ------------------------------------------------------------


class TodoIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    repeat: dict | None = None  # {"every": "day"|"week"|"month", "on": [weekdays 0=Mon] | day of month}


class TodoPatch(BaseModel):
    done: bool | None = None
    text: str | None = Field(None, max_length=200)


def todo_list() -> dict:
    return {"items": todos.list(), "error": todos.error}


@app.get("/api/todos")
async def get_todos():
    return todo_list()


@app.post("/api/todos")
async def add_todo(body: TodoIn):
    try:
        todos.add(body.text, body.repeat)
    except ValueError as e:
        raise HTTPException(422, "Empty item" if str(e) == "empty" else str(e))
    except OverflowError as e:
        raise HTTPException(409, str(e))
    return todo_list()


@app.patch("/api/todos/{item_id}")
async def update_todo(item_id: str, body: TodoPatch):
    try:
        todos.update(item_id, done=body.done, text=body.text)
    except KeyError:
        raise HTTPException(404, "No such item")
    return todo_list()


@app.delete("/api/todos/{item_id}")
async def delete_todo(item_id: str):
    try:
        todos.delete(item_id)
    except KeyError:
        pass  # already gone, e.g. deleted from another phone
    return todo_list()


@app.post("/api/todos/clear-done")
async def clear_done_todos():
    todos.clear_done()
    return todo_list()


# --- guest Wi-Fi ---------------------------------------------------------------


def guest_wifi() -> dict | None:
    if S.wifi_ssid:
        return {"ssid": S.wifi_ssid, "password": S.wifi_password, "security": S.wifi_security, "hidden": S.wifi_hidden}
    return {"ssid": "Kitchen-Guest", "password": "demo-password", "security": "WPA", "hidden": False} if S.demo else None


@app.get("/api/wifi")
async def get_wifi():
    """Name and password, fetched only when someone opens the Wi-Fi view."""
    w = guest_wifi()
    if not w:
        raise HTTPException(404, "Wi-Fi not configured")
    return {"ssid": w["ssid"], "password": w["password"]}


@app.get("/api/wifi/qr.svg")
async def wifi_qr():
    w = guest_wifi()
    if not w:
        raise HTTPException(404, "Wi-Fi not configured")
    return Response(wifi.qr_svg(wifi.payload(**w)), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# --- screen power + ntfy test ---------------------------------------------------


@app.get("/api/display")
async def display():
    """What the Pi's screen should do. Polled by pi/kiosk-display.py."""
    now = datetime.now(TZ)
    alarm = any(t.ends <= datetime.now(timezone.utc) for t in timers.values())
    off = S.screen_off_at_night and is_night(now)
    return {"on": alarm or not off, "alarm": alarm, "night": is_night(now)}


@app.post("/api/ntfy/test")
async def ntfy_test():
    if not ntfy.enabled:
        raise HTTPException(404, "ntfy not configured (NTFY_URL, NTFY_TOPIC)")
    ok = await ntfy.send("Kitchen monitor", "Test notification - ntfy is working.", tags=["wave"])
    if not ok:
        raise HTTPException(502, "ntfy send failed - check the server logs")
    return {"ok": True}
