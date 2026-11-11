from datetime import date, datetime, timedelta, timezone

from app.history import FULL_EVERY, HistoryCache

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
day = lambda d, kwh=1.0: {"date": d, "kwh": kwh, "complete": True}  # noqa: E731


def test_full_load_then_only_recent_days(tmp_path):
    path = tmp_path / "history.json"
    h = HistoryCache(path)
    assert h.needs_full("mpan1", NOW)
    h.replace("mpan1", [day("2026-10-01"), day("2026-11-01"), day("2026-11-09", 0.5)], NOW)

    h = HistoryCache(path)  # after a restart: nothing to download again
    assert not h.needs_full("mpan1", NOW + timedelta(hours=6))
    # the refresh re-reads from 1 Nov: late readings complete the 9th, the 10th is new
    days = h.merge([day("2026-11-01"), day("2026-11-09", 9.0), day("2026-11-10")], date(2026, 11, 1), date(2026, 10, 15))
    assert [d["date"] for d in days] == ["2026-11-01", "2026-11-09", "2026-11-10"]  # 1 Oct aged out
    assert days[1]["kwh"] == 9.0
    assert HistoryCache(path).days == days


def test_full_reload_when_due_or_meter_changes(tmp_path):
    h = HistoryCache(tmp_path / "history.json")
    h.replace("mpan1", [day("2026-11-01")], NOW)
    assert h.needs_full("mpan2", NOW)
    assert h.needs_full("mpan1", NOW + FULL_EVERY)


def test_bad_file_is_ignored(tmp_path):
    path = tmp_path / "history.json"
    path.write_text('{"days": "nope"}')
    h = HistoryCache(path)
    assert h.days == [] and h.needs_full("mpan1", NOW)
