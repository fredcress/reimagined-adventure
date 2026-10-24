"""Shared to-do list, edited from phones and the kitchen screen.

Kept in memory and saved to a small JSON file (DATA_DIR/todos.json) after
every change, so it survives container restarts and image updates as long as
DATA_DIR is on a volume. If the file can't be written the list still works,
in memory only, and `error` says why.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("kitchen.todos")

MAX_ITEMS = 300
MAX_TEXT = 120
KEEP_DONE = timedelta(days=7)  # ticked items disappear after this


class TodoStore:
    def __init__(self, path: Path | None):
        self.path = path
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

    def list(self, now: datetime | None = None) -> list[dict]:
        """Open items newest first, then ticked items most recently ticked first."""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - KEEP_DONE).isoformat()
        kept = [it for it in self.items if not it["done"] or (it.get("done_at") or "") > cutoff]
        if len(kept) != len(self.items):
            self.items = kept
            self._save()
        open_ = sorted((it for it in kept if not it["done"]), key=lambda it: it["created"], reverse=True)
        done = sorted((it for it in kept if it["done"]), key=lambda it: it.get("done_at") or "", reverse=True)
        return open_ + done

    def add(self, text: str) -> dict:
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
        self.items.append(item)
        self._save()
        return item

    def update(self, item_id: str, done: bool | None = None, text: str | None = None) -> dict:
        it = self._find(item_id)
        if done is not None and done != it["done"]:
            it["done"] = done
            it["done_at"] = datetime.now(timezone.utc).isoformat() if done else None
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
        self.items = [it for it in self.items if not it["done"]]
        self._save()
        return before - len(self.items)
