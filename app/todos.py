"""Shared to-do list, edited from phones and the kitchen screen.

Kept in memory and saved to a small JSON file (DATA_DIR/todos.json) after
every change, so it survives container restarts and image updates as long as
DATA_DIR is on a volume. If the file can't be written the list still works,
in memory only, and `error` says why.

An item can repeat daily, weekly (on one or more weekdays) or monthly (on a day of the
month). A repeating item has a `due` date: ticking it moves `due` to the next
occurrence, and once that day arrives it comes back unticked. Until then it
is "waiting" and the screens only show it in the Repeating section. Repeating
items are never cleared away with the ticked one-offs; only ✕ removes them.
"""

import json
import logging
import os
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

log = logging.getLogger("kitchen.todos")

MAX_ITEMS = 300
MAX_TEXT = 120
KEEP_DONE = timedelta(days=7)  # ticked items disappear after this
EVERY = ("day", "week", "month")


def check_repeat(repeat: dict | None) -> dict | None:
    """Normalise {"every": "day"|"week"|"month", "on": [weekdays 0=Mon..6] | day 1-31}."""
    if not repeat:
        return None
    every, on = repeat.get("every"), repeat.get("on")
    if every not in EVERY:
        raise ValueError("every must be day, week or month")
    if every == "day":
        return {"every": "day"}
    if every == "week":
        days = weekdays(on)
        if not days or any(not isinstance(d, int) or not 0 <= d <= 6 for d in days):
            raise ValueError("bad 'on' for week")
        return {"every": "week", "on": sorted(set(days))}
    if not isinstance(on, int) or not 1 <= on <= 31:
        raise ValueError("bad 'on' for month")
    return {"every": every, "on": on}


def weekdays(on) -> list:
    """Weekly `on` is a list of weekdays; items saved before that hold a single int."""
    return [on] if isinstance(on, int) else list(on or [])


def next_due(repeat: dict, after: date, inclusive: bool) -> date:
    """First day on or after (inclusive) / strictly after `after` that the rule hits.
    Monthly on the 31st falls on the last day of shorter months."""
    d = after if inclusive else after + timedelta(days=1)
    if repeat["every"] == "day":
        return d
    if repeat["every"] == "week":
        return d + timedelta(days=min((w - d.weekday()) % 7 for w in weekdays(repeat["on"])))
    y, m = d.year, d.month
    while True:
        hit = date(y, m, min(repeat["on"], monthrange(y, m)[1]))
        if hit >= d:
            return hit
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


class TodoStore:
    def __init__(self, path: Path | None, tz: tzinfo = timezone.utc):
        self.path = path
        self.tz = tz
        self.items: list[dict] = []
        self.error: str | None = None
        if path is None:
            return
        try:
            if path.exists():
                self.items = json.loads(path.read_text())["items"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.error = f"couldn't read {path}: {e}"
            log.error(self.error)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"items": self.items}, indent=1))
            os.replace(tmp, self.path)  # atomic: never a half-written file
            self.error = None
        except OSError as e:
            self.error = f"not saved, changes are lost on restart ({e.strerror or e})"
            log.error("saving %s: %s", self.path, e)

    def _find(self, item_id: str) -> dict:
        for it in self.items:
            if it["id"] == item_id:
                return it
        raise KeyError(item_id)

    def _today(self, now: datetime | None = None) -> date:
        return (now or datetime.now(timezone.utc)).astimezone(self.tz).date()

    def list(self, now: datetime | None = None) -> list[dict]:
        """Open items newest first, then ticked items most recently ticked first.
        Each comes back with `waiting`: a repeating item not due again yet."""
        now = now or datetime.now(timezone.utc)
        today = self._today(now).isoformat()
        cutoff = (now - KEEP_DONE).isoformat()
        changed = False
        for it in self.items:
            if it.get("repeat") and it["done"] and it["due"] <= today:  # its day has come round again
                it["done"], it["done_at"] = False, None
                changed = True
        kept = [it for it in self.items if not it["done"] or it.get("repeat") or (it.get("done_at") or "") > cutoff]
        if changed or len(kept) != len(self.items):
            self.items = kept
            self._save()
        open_ = sorted((it for it in kept if not it["done"]), key=lambda it: it["created"], reverse=True)
        done = sorted((it for it in kept if it["done"]), key=lambda it: it.get("done_at") or "", reverse=True)
        return [{**it, "waiting": bool(it.get("repeat")) and it["due"] > today} for it in open_ + done]

    def add(self, text: str, repeat: dict | None = None) -> dict:
        repeat = check_repeat(repeat)
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise ValueError("empty")
        if len(self.items) >= MAX_ITEMS:
            raise OverflowError("list is full")
        item = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "done": False,
            "created": datetime.now(timezone.utc).isoformat(),
            "done_at": None,
        }
        if repeat:  # first shows on the next matching day, today included
            item["repeat"] = repeat
            item["due"] = next_due(repeat, self._today(), inclusive=True).isoformat()
        self.items.append(item)
        self._save()
        return item

    def update(self, item_id: str, done: bool | None = None, text: str | None = None) -> dict:
        it = self._find(item_id)
        if done is not None and done != it["done"]:
            it["done"] = done
            it["done_at"] = datetime.now(timezone.utc).isoformat() if done else None
            if it.get("repeat"):  # ticked: wait for the next one; unticked (a stray tap): due now
                today = self._today()
                it["due"] = next_due(it["repeat"], today, inclusive=False).isoformat() if done else today.isoformat()
        if text is not None:
            text = " ".join(text.split())[:MAX_TEXT]
            if text:
                it["text"] = text
        self._save()
        return it

    def delete(self, item_id: str) -> None:
        self.items.remove(self._find(item_id))
        self._save()

    def clear_done(self) -> int:
        before = len(self.items)
        self.items = [it for it in self.items if not it["done"] or it.get("repeat")]
        self._save()
        return before - len(self.items)
