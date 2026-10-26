import json
from datetime import datetime, timedelta, timezone

from app.todos import TodoStore


def test_add_tick_order_and_persist(tmp_path):
    path = tmp_path / "data" / "todos.json"
    s = TodoStore(path)
    a = s.add("  Milk   + eggs ")
    b = s.add("Book boiler")
    assert a["text"] == "Milk + eggs"
    assert [i["text"] for i in s.list()] == ["Book boiler", "Milk + eggs"]  # newest first
    s.update(b["id"], done=True)
    assert [i["text"] for i in s.list()] == ["Milk + eggs", "Book boiler"]  # ticked go last
    assert json.loads(path.read_text())["items"][1]["done"] is True

    again = TodoStore(path)  # survives a restart
    assert [i["text"] for i in again.list()] == ["Milk + eggs", "Book boiler"]
    assert again.clear_done() == 1 and len(again.list()) == 1


def test_old_ticked_items_expire(tmp_path):
    s = TodoStore(tmp_path / "t.json")
    s.update(s.add("old")["id"], done=True)
    s.add("open")
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert [i["text"] for i in s.list(later)] == ["open"]


def test_unwritable_dir_keeps_working_in_memory(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    s = TodoStore(blocker / "todos.json")
    s.add("still works")
    assert s.error and "not saved" in s.error
    assert [i["text"] for i in s.list()] == ["still works"]


def test_next_due():
    from datetime import date

    from app.todos import next_due

    mon = date(2026, 10, 26)
    assert next_due({"every": "day"}, mon, inclusive=False) == date(2026, 10, 27)
    assert next_due({"every": "week", "on": 0}, mon, inclusive=True) == mon
    assert next_due({"every": "week", "on": 0}, mon, inclusive=False) == date(2026, 11, 2)
    assert next_due({"every": "week", "on": 3}, mon, inclusive=True) == date(2026, 10, 29)
    assert next_due({"every": "month", "on": 26}, mon, inclusive=False) == date(2026, 11, 26)
    assert next_due({"every": "month", "on": 31}, date(2026, 11, 1), inclusive=True) == date(2026, 11, 30)
    assert next_due({"every": "month", "on": 5}, date(2026, 12, 20), inclusive=True) == date(2027, 1, 5)


def test_repeating_item_comes_back(tmp_path):
    s = TodoStore(tmp_path / "t.json")
    it = s.add("Water plants", {"every": "day"})
    assert it["due"] == datetime.now(timezone.utc).date().isoformat()
    assert s.list()[0]["waiting"] is False
    s.update(it["id"], done=True)
    assert s.list()[0]["waiting"] is True and s.list()[0]["done"]
    assert s.clear_done() == 0  # never cleared with the one-offs
    later = datetime.now(timezone.utc) + timedelta(days=30)  # long past KEEP_DONE, still kept
    back = s.list(later)[0]
    assert not back["done"] and not back["waiting"]


def test_repeating_untick_is_due_now(tmp_path):
    s = TodoStore(tmp_path / "t.json")
    it = s.add("Bins", {"every": "week", "on": (datetime.now(timezone.utc).weekday() + 3) % 7})
    assert s.list()[0]["waiting"] is True  # first shows on its weekday
    s.update(it["id"], done=True)
    s.update(it["id"], done=False)
    assert s.list()[0]["waiting"] is False


def test_bad_repeat_rejected(tmp_path):
    import pytest

    s = TodoStore(tmp_path / "t.json")
    for bad in ({"every": "hour"}, {"every": "week", "on": 7}, {"every": "month", "on": 0}, {"every": "week"}):
        with pytest.raises(ValueError):
            s.add("x", bad)
