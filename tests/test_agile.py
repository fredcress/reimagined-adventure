from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.agile import (
    SLOT,
    Rate,
    Reading,
    cheapest_window,
    daily_usage,
    day_slot_count,
    floor_slot,
    half_hours,
    parse_rates,
    rate_at,
)

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")


def slots_from(prices, start=datetime(2026, 1, 10, tzinfo=UTC)):
    return [Rate(start + i * SLOT, start + (i + 1) * SLOT, p) for i, p in enumerate(prices)]


def test_parse_rates_sorts_and_prefers_direct_debit():
    rows = [
        {"valid_from": "2026-01-10T00:30:00Z", "valid_to": "2026-01-10T01:00:00Z", "value_inc_vat": 12.0, "payment_method": None},
        {"valid_from": "2026-01-10T00:00:00Z", "valid_to": "2026-01-10T00:30:00Z", "value_inc_vat": 10.0, "payment_method": None},
        {"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 50.0, "payment_method": "NON_DIRECT_DEBIT"},
        {"valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "value_inc_vat": 45.0, "payment_method": "DIRECT_DEBIT"},
    ]
    rates = parse_rates(rows)
    assert [r.price for r in rates] == [45.0, 10.0, 12.0]
    assert rates[0].end is None


def test_rate_at_prefers_latest_when_overlapping():
    fixed = Rate(datetime(2026, 1, 1, tzinfo=UTC), None, 25.0)
    agile = slots_from([10.0])
    rates = sorted([fixed, *agile], key=lambda r: r.start)
    assert rate_at(rates, agile[0].start).price == 10.0
    assert rate_at(rates, agile[0].end).price == 25.0
    assert rate_at(rates, datetime(2025, 1, 1, tzinfo=UTC)) is None


def test_floor_slot():
    assert floor_slot(datetime(2026, 1, 1, 14, 47, 12, tzinfo=UTC)).minute == 30
    assert floor_slot(datetime(2026, 1, 1, 14, 12, tzinfo=UTC)).minute == 0


def test_half_hours_skips_gaps():
    rates = slots_from([10, 11]) + slots_from([12], datetime(2026, 1, 10, 2, tzinfo=UTC))
    out = half_hours(rates, datetime(2026, 1, 10, tzinfo=UTC), datetime(2026, 1, 10, 3, tzinfo=UTC))
    assert [s.price for s in out] == [10, 11, 12]


def test_cheapest_window_average_and_contiguity():
    slots = slots_from([30, 5, 6, 40, 1, 1])
    w = cheapest_window(slots, 1)
    assert w.price == 1 and w.start == slots[4].start and w.end == slots[5].end
    assert round(cheapest_window(slots, 1.5).price, 3) == round(min(
        sum(slots[i + k].price for k in range(3)) / 3 for i in range(4)), 3)

    gappy = slots_from([1]) + slots_from([1], datetime(2026, 1, 10, 5, tzinfo=UTC))
    assert cheapest_window(gappy, 1) is None
    assert cheapest_window(slots, 10) is None


def test_day_slot_count_handles_clock_changes():
    assert day_slot_count(date(2026, 3, 29), LONDON) == 46
    assert day_slot_count(date(2026, 10, 25), LONDON) == 50
    assert day_slot_count(date(2026, 6, 1), LONDON) == 48


def test_daily_usage_cost_and_completeness():
    start = datetime(2026, 6, 1, tzinfo=LONDON)
    unit = [Rate(start - timedelta(days=1), None, 20.0)]
    standing = [Rate(start - timedelta(days=1), None, 50.0)]
    readings = [Reading(start + i * SLOT, 0.5) for i in range(48)]
    readings += [Reading(start + timedelta(days=1), 1.0)]  # partial next day

    days = daily_usage(readings, unit, standing, LONDON)
    assert len(days) == 2
    d0, d1 = days
    assert d0["date"] == "2026-06-01" and d0["complete"]
    assert d0["kwh"] == 24.0
    assert d0["energy_p"] == 480.0 and d0["cost_p"] == 530.0
    assert d0["avg_price"] == 20.0
    assert not d1["complete"]


def test_daily_usage_groups_by_local_day():
    # 23:30 UTC on 1 June is 00:30 BST on 2 June.
    r = Reading(datetime(2026, 6, 1, 23, 30, tzinfo=UTC), 1.0)
    days = daily_usage([r], [], [], LONDON)
    assert days[0]["date"] == "2026-06-02"
    assert not days[0]["complete"]  # unpriced


def test_half_hours_across_clock_change():
    # 25 Oct 2026: clocks go back, so the local day has 50 half hours.
    start = datetime(2026, 10, 25, tzinfo=LONDON)
    end = datetime(2026, 10, 26, tzinfo=LONDON)
    rates = [Rate(start - timedelta(days=1), None, 20.0)]
    out = half_hours(rates, start, end)
    assert len(out) == 50
    assert all(b.start - a.start == SLOT for a, b in zip(out, out[1:]))


def test_avg_price_weights_partial_slots():
    from app.agile import avg_price

    slots = slots_from([10, 20])
    t0 = slots[0].start
    # 15 min at 10p + 30 min at 20p
    assert avg_price(slots, t0 + timedelta(minutes=15), t0 + timedelta(minutes=60)) == (10 * 15 + 20 * 30) / 45
    assert avg_price(slots, t0, t0 + timedelta(minutes=90)) is None  # runs past published prices


def test_best_start_uses_delay_steps():
    from app.agile import best_start

    # 30p for 2h, then 5p for 2h, then 30p
    slots = slots_from([30] * 4 + [5] * 4 + [30] * 4)
    now = slots[0].start + timedelta(minutes=10)
    horizon = slots[-1].end
    hourly = best_start(slots, now, timedelta(hours=2), timedelta(hours=1), horizon)
    assert hourly["delay"] == timedelta(hours=2)  # 2h10 -> 4h10: 1h50 cheap, 10 min dear
    assert hourly["now_avg"] == (110 * 30 + 10 * 5) / 120  # already overlaps the cheap block
    half_hourly = best_start(slots, now, timedelta(hours=2), timedelta(minutes=30), horizon)
    assert half_hourly["avg"] <= hourly["avg"]
    assert best_start(slots, now, timedelta(hours=7), timedelta(hours=1), horizon) is None


def test_appliance_config_parsing():
    from app.config import _appliances

    a = _appliances("Dishwasher eco=4:30@0.9kWh, Dishwasher quick = 2.5 ,junk, Washer=1:05")
    assert [(x.name, x.hours, x.kwh) for x in a] == [
        ("Dishwasher eco", 4.5, 0.9),
        ("Dishwasher quick", 2.5, None),
        ("Washer", 1 + 5 / 60, None),
    ]


def test_pick_programs_prefers_cheaper_run_per_machine():
    from app.config import Appliance
    from app.main import pick_programs

    progs = [Appliance("Dishwasher eco", 4.5, None), Appliance("Dishwasher quick", 2.5, None), Appliance("Washer 40", 2, None)]
    items = [{"name": a.name, "start": "x", "avg": avg} for a, avg in zip(progs, [11.5, -1.6, 9.0])]
    pick_programs(items, progs)
    assert [i["best"] for i in items] == [False, True, True]
    assert items[1]["beats"] == [{"name": "Dishwasher eco", "avg": 11.5, "run_p": None}]

    # near tie: the program listed first wins; with kWh the cheaper run in pence wins
    items = [{"name": a.name, "start": "x", "avg": avg} for a, avg in zip(progs, [10.2, 10.0, 9.0])]
    pick_programs(items, progs)
    assert items[0]["best"]
    kwh = [Appliance("Dishwasher eco", 4.5, 0.8), Appliance("Dishwasher quick", 2.5, 1.2)]
    items = [{"name": a.name, "start": "x", "avg": avg, "run_p": a.kwh * avg} for a, avg in zip(kwh, [12.0, 9.0])]
    pick_programs(items, kwh)  # eco 9.6p vs quick 10.8p
    assert items[0]["best"] and not items[1]["best"]

    # a program that doesn't fit the known prices can't win
    items = [{"name": "Dishwasher eco"}, {"name": "Dishwasher quick", "start": "x", "avg": 20.0, "run_p": 24.0}]
    pick_programs(items, kwh)
    assert items[1]["best"]


def test_best_start_finish_in_timer():
    from app.agile import best_start
    from app.config import _appliances

    # 30p for 3h, then 5p for 3h, then 30p
    slots = slots_from([30] * 6 + [5] * 6 + [30] * 6)
    now = slots[0].start
    horizon = slots[-1].end
    # A 2h45 cycle on a "Finish in" timer can start now, or at 15 min, 1h15, 2h15...
    b = best_start(slots, now, timedelta(hours=2, minutes=45), timedelta(hours=1), horizon, finish=True)
    assert b["delay"] == timedelta(hours=3, minutes=15)  # finish in 6h: 3h15 -> 6h00, all cheap
    assert (b["delay"] + timedelta(hours=2, minutes=45)) % timedelta(hours=1) == timedelta(0)
    # whole-hour cycles: the first timer step after "now" is one step, not zero
    b = best_start(slots, now, timedelta(hours=3), timedelta(hours=1), horizon, finish=True)
    assert b["delay"] == timedelta(hours=3)

    a = _appliances("Washer 40 + dry=5:15@3.5 finish, Dishwasher=4:30@0.85")
    assert [(x.name, x.hours, x.kwh, x.finish) for x in a] == [
        ("Washer 40 + dry", 5.25, 3.5, True),
        ("Dishwasher", 4.5, 0.85, False),
    ]
