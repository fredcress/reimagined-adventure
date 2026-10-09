"""Seerr / Jellyseerr / Overseerr API client: request counts and request lists."""

import httpx

from ..config import Settings

REQUEST_STATUS = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}
MEDIA_STATUS = {
    1: "unknown",
    2: "pending",
    3: "processing",
    4: "partial",
    5: "available",
    6: "blacklisted",
    7: "deleted",
}
# Filters accepted by GET /request
FILTERS = ("all", "pending", "approved", "processing", "available", "unavailable", "failed")
TMDB_IMG = "https://image.tmdb.org/t/p/w154"


class Seerr:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.base = f"{settings.seerr_url}/api/v1"
        self.client = client
        self.headers = {"X-Api-Key": settings.seerr_api_key or ""}
        self._media_cache: dict[tuple[str, int], dict] = {}  # request lists don't include titles

    async def _get(self, path: str, params: dict | None = None):
        resp = await self.client.get(f"{self.base}{path}", params=params, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    async def _media(self, media_type: str, tmdb_id: int) -> dict:
        key = (media_type, tmdb_id)
        if key not in self._media_cache:
            try:
                info = await self._get(f"/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}")
            except httpx.HTTPError:
                return {"title": f"TMDB {tmdb_id}", "year": None, "poster": None, "overview": None}
            date = info.get("releaseDate") or info.get("firstAirDate") or ""
            poster = info.get("posterPath")
            self._media_cache[key] = {
                "title": info.get("title") or info.get("name") or f"TMDB {tmdb_id}",
                "year": date[:4] or None,
                "poster": f"{TMDB_IMG}{poster}" if poster else None,
                "overview": info.get("overview"),
            }
        return self._media_cache[key]

    async def counts(self) -> dict:
        return await self._get("/request/count")

    async def requests(self, take: int = 6, skip: int = 0, filter: str = "all") -> dict:
        if filter not in FILTERS:
            filter = "all"
        body = await self._get(
            "/request", {"take": take, "skip": skip, "sort": "added", "filter": filter}
        )
        out = []
        for r in body.get("results", []):
            media = r.get("media") or {}
            media_type = media.get("mediaType") or r.get("type") or "movie"
            info = await self._media(media_type, media.get("tmdbId"))
            status = REQUEST_STATUS.get(r.get("status"), "unknown")
            # Once approved, the media status says how far along it is.
            if status == "approved":
                status = MEDIA_STATUS.get(media.get("status"), status)
                if status in ("unknown", "pending"):
                    status = "approved"
            out.append(
                {
                    **info,
                    "type": media_type,
                    "status": status,
                    "user": (r.get("requestedBy") or {}).get("displayName"),
                    "created": r.get("createdAt"),
                }
            )
        page = body.get("pageInfo") or {}
        return {"results": out, "total": page.get("results", len(out)), "skip": skip, "take": take}
