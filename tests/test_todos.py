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
