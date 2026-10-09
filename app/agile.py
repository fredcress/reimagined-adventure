"""Pure tariff maths: half-hour slots, cheapest windows, daily usage cost.

Everything here works on timezone-aware datetimes and has no I/O, so it is
easy to unit test. Slot arithmetic is done in UTC: Python adds timedeltas to
wall-clock time when both sides share a tzinfo, which is wrong across clock
changes. Prices are p/kWh including VAT; standing charges are p/day.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SLOT = timedelta(minutes=30)


@dataclass(frozen=True)
class Rate:
    start: datetime
    end: datetime | None  # None = open-ended (e.g. a fixed tariff)
    price: float

    def covers(self, t: datetime) -> bool:
        return self.start <= t and (self.end is None or t < self.end)


@dataclass(frozen=True)
class Reading:
    start: datetime
    kwh: float


def parse_rates(results: list[dict]) -> list[Rate]:
    """Octopus unit-rate/standing-charge rows -> Rates sorted by start."""
    rates = []
    for row in results:
        # Direct debit and non-DD prices can both be listed; prefer DD.
        if row.get("payment_method") not in (None, "DIRECT_DEBIT"):
            continue
        end = row.get("valid_to")
        rates.append(
            Rate(
                start=datetime.fromisoformat(row["valid_from"]),
                end=datetime.fromisoformat(end) if end else None,
                price=float(row["value_inc_vat"]),
            )
        )
    return sorted(rates, key=lambda r: r.start)


def parse_readings(results: list[dict]) -> list[Reading]:
    return sorted(
        (
            Reading(datetime.fromisoformat(r["interval_start"]), float(r["consumption"]))
            for r in results
        ),
        key=lambda r: r.start,
    )


def rate_at(rates: list[Rate], t: datetime) -> Rate | None:
    # Later rows win when periods overlap (e.g. a tariff switch).
    for rate in reversed(rates):
        if rate.covers(t):
            return rate
    return None


def floor_slot(t: datetime) -> datetime:
    t = t.astimezone(timezone.utc)
    return t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)


def last_known(rates: list[Rate]) -> datetime | None:
    """End of the last bounded rate, i.e. how far ahead prices are published."""
    ends = [r.end for r in rates if r.end is not None]
    return max(ends) if ends else None


def half_hours(rates: list[Rate], start: datetime, end: datetime) -> list[Rate]:
    """Expand rates into contiguous 30-minute slots over [start, end).

    Slots with no known price are skipped, so the result can have gaps.
    """
    slots = []
    t = floor_slot(start)
    while t < end:
        rate = rate_at(rates, t)
        if rate:
            slots.append(Rate(t, t + SLOT, rate.price))
        t += SLOT
    return slots


def cheapest_window(slots: list[Rate], hours: float) -> Rate | None:
    """Cheapest contiguous run of `hours` within `slots`; price is the average."""
    n = max(1, round(hours * 2))
    best = None
    for i in range(len(slots) - n + 1):
        run = slots[i : i + n]
        if run[-1].start - run[0].start != SLOT * (n - 1):
            continue  # gap in published prices
        avg = sum(s.price for s in run) / n
        if best is None or avg < best.price:
            best = Rate(run[0].start, run[-1].end, avg)
    return best


def band(price: float, cheap: float, expensive: float) -> str:
    if price <= 0:
        return "plunge"
    if price < cheap:
        return "cheap"
    if price < expensive:
        return "normal"
    return "expensive"


def local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def day_slot_count(d: date, tz: ZoneInfo) -> int:
    """48 normally, 46/50 on clock-change days."""
    start = local_midnight(d, tz).astimezone(timezone.utc)
    end = local_midnight(d + timedelta(days=1), tz).astimezone(timezone.utc)
    return int((end - start).total_seconds() // SLOT.total_seconds())


def daily_usage(
    readings: list[Reading],
    unit_rates: list[Rate],
    standing: list[Rate],
    tz: ZoneInfo,
) -> list[dict]:
    """Group half-hourly readings into local days with kWh and cost (pence).

    A day is `complete` when every half-hour reading has arrived.
    """
    days: dict[date, dict] = {}
    for r in readings:
        d = r.start.astimezone(tz).date()
        day = days.setdefault(d, {"kwh": 0.0, "energy_p": 0.0, "slots": 0, "priced": 0})
        day["kwh"] += r.kwh
        day["slots"] += 1
        rate = rate_at(unit_rates, r.start)
        if rate:
            day["energy_p"] += r.kwh * rate.price
            day["priced"] += 1

    out = []
    for d in sorted(days):
        day = days[d]
        sc = rate_at(standing, local_midnight(d, tz))
        standing_p = sc.price if sc else 0.0
        out.append(
            {
                "date": d.isoformat(),
                "kwh": round(day["kwh"], 3),
                "energy_p": round(day["energy_p"], 2),
                "standing_p": round(standing_p, 2),
                "cost_p": round(day["energy_p"] + standing_p, 2),
                "avg_price": round(day["energy_p"] / day["kwh"], 2) if day["kwh"] else None,
                "complete": day["slots"] >= day_slot_count(d, tz)
                and day["priced"] == day["slots"],
            }
        )
    return out


def avg_price(rates: list[Rate], start: datetime, end: datetime) -> float | None:
    """Time-weighted average price over [start, end), assuming flat power draw.

    None if any part of the period has no published price.
    """
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    total, t = 0.0, start
    while t < end:
        rate = rate_at(rates, t)
        if not rate:
            return None
        seg_end = min(floor_slot(t) + SLOT, end)
        total += rate.price * (seg_end - t).total_seconds()
        t = seg_end
    return total / (end - start).total_seconds()


def best_start(
    rates: list[Rate], now: datetime, duration: timedelta, step: timedelta, horizon: datetime
) -> dict | None:
    """Cheapest time to start a fixed-length cycle, in `step` increments from now.

    Steps match an appliance's delay-start button (e.g. whole hours). Only
    cycles that finish by `horizon` (end of published prices) are considered.
    """
    now = now.astimezone(timezone.utc)
    best, now_avg, k = None, None, 0
    while (start := now + k * step) + duration <= horizon:
        avg = avg_price(rates, start, start + duration)
        if k == 0:
            now_avg = avg
        if avg is not None and (best is None or avg < best["avg"] - 1e-9):
            best = {"start": start, "end": start + duration, "delay": k * step, "avg": avg}
        k += 1
    if best:
        best["now_avg"] = now_avg
    return best
