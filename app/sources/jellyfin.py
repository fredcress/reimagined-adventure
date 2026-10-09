"""Jellyfin API client: active sessions and recently added items."""

import httpx

from ..config import Settings

TICKS_PER_SECOND = 10_000_000


def _title(item: dict) -> tuple[str, str | None]:
    """(title, subtitle) for display, e.g. ("The Bear", "S2E3 · Fishes")."""
    if item.get("Type") == "Episode":
        s, e = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f"S{s}E{e}" if s is not None and e is not None else None
        sub = " · ".join(x for x in (code, item.get("Name")) if x)
        return item.get("SeriesName") or item.get("Name", "?"), sub or None
    if item.get("Type") == "Audio":
        artists = ", ".join(item.get("Artists") or [])
        return item.get("Name", "?"), artists or item.get("Album")
    year = item.get("ProductionYear")
    return item.get("Name", "?"), str(year) if year else None


def _poster_id(item: dict) -> str | None:
    # Prefer the series poster for episodes - it reads better on a small screen.
    if item.get("Type") == "Episode" and item.get("SeriesId"):
        return item["SeriesId"]
    if item.get("Type") == "Audio" and item.get("AlbumId"):
        return item["AlbumId"]
    return item.get("Id")


class Jellyfin:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.base = settings.jellyfin_url
        self.client = client
        self.headers = {"X-Emby-Token": settings.jellyfin_api_key or ""}

    async def _get(self, path: str, params: dict | None = None):
        resp = await self.client.get(f"{self.base}{path}", params=params, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def sessions(self) -> list[dict]:
        out = []
        for s in await self._get("/Sessions", {"activeWithinSeconds": 960}):
            item = s.get("NowPlayingItem")
            if not item:
                continue
            title, subtitle = _title(item)
            runtime = item.get("RunTimeTicks") or 0
            position = (s.get("PlayState") or {}).get("PositionTicks") or 0
            out.append(
                {
                    "user": s.get("UserName"),
                    "device": s.get("DeviceName"),
                    "client": s.get("Client"),
                    "type": item.get("Type"),
                    "title": title,
                    "subtitle": subtitle,
                    "paused": bool((s.get("PlayState") or {}).get("IsPaused")),
                    "progress": round(position / runtime, 4) if runtime else None,
                    "remaining_s": int((runtime - position) / TICKS_PER_SECOND) if runtime else None,
                    "image": _poster_id(item),
                }
            )
        return out

    async def latest(self, limit: int = 8, skip: int = 0, types: str = "Movie,Episode") -> dict:
        body = await self._get(
            "/Items",
            {
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "Recursive": "true",
                "IncludeItemTypes": types,
                "Fields": "DateCreated,Overview",
                "StartIndex": skip,
                "Limit": limit,
            },
        )
        out = []
        for item in body.get("Items", []):
            title, subtitle = _title(item)
            out.append(
                {
                    "type": item.get("Type"),
                    "title": title,
                    "subtitle": subtitle,
                    "added": item.get("DateCreated"),
                    "image": _poster_id(item),
                    "overview": item.get("Overview"),
                }
            )
        return {"results": out, "total": body.get("TotalRecordCount", len(out)), "skip": skip, "take": limit}

    async def image(self, item_id: str) -> tuple[bytes, str]:
        resp = await self.client.get(
            f"{self.base}/Items/{item_id}/Images/Primary",
            params={"maxHeight": 300, "quality": 85},
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "image/jpeg")
