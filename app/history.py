"""Long-run daily usage (kWh and cost per day), kept between refreshes.

The first load downloads everything since the first Octopus agreement, which
is dozens of requests for a few years of half-hourly prices. After that only
the last RECENT_DAYS are fetched and swapped in: older days no longer change.
The days are saved to DATA_DIR/history.json so a restart or image update
doesn't start again, and a full reload still runs every FULL_EVERY to pick up
any late corrections.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("kitchen.history")

RECENT_DAYS = 14  # readings usually arrive the next day, now and then a few days late
FULL_EVERY = timedelta(days=30)


class HistoryCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.mpan: str | None = None
        self.full_at: datetime | None = None
        self.days: list[dict] = []
        if path is None or not path.exists():
            return
        try:
            saved = json.loads(path.read_text())
            days = saved["days"]
            if not isinstance(days, list) or not all(isinstance(d, dict) and "date" in d for d in days):
                raise ValueError("bad 'days'")
            self.mpan, self.full_at, self.days = saved["mpan"], datetime.fromisoformat(saved["full_at"]), days
        except (OSError, ValueError, KeyError, TypeError) as e:  # just reload it all
            log.warning("ignoring %s: %s", path, e)

    def needs_full(self, mpan: str, now: datetime) -> bool:
        """True with nothing saved, a different meter, or a full reload due."""
        return not self.days or self.mpan != mpan or self.full_at is None or now - self.full_at >= FULL_EVERY

    def replace(self, mpan: str, days: list[dict], now: datetime) -> list[dict]:
        self.mpan, self.full_at, self.days = mpan, now, days
        self._save()
        return self.days

    def merge(self, recent: list[dict], since: date, oldest: date) -> list[dict]:
        """Swap in freshly fetched days from `since` on; drop days before `oldest`."""
        kept = [d for d in self.days if oldest.isoformat() <= d["date"] < since.isoformat()]
        self.days = kept + [d for d in recent if d["date"] >= since.isoformat()]
        self._save()
        return self.days

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mpan": self.mpan, "full_at": self.full_at.isoformat(), "days": self.days}))
            os.replace(tmp, self.path)
        except OSError as e:  # still works, from memory, until the next restart
            log.warning("couldn't save %s: %s", self.path, e)
