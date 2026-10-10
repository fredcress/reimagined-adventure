"""Seerr / Jellyseerr / Overseerr API client: request counts and request lists."""

from urllib.parse import quote

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

    # --- discovery + requests ---------------------------------------------------

    @staticmethod
    def _result(r: dict) -> dict | None:
        """Search/discover row -> display dict (None for people)."""
        media_type = r.get("mediaType")
        if media_type not in ("movie", "tv"):
            return None
        date = r.get("releaseDate") or r.get("firstAirDate") or ""
        poster = r.get("posterPath")
        status = MEDIA_STATUS.get((r.get("mediaInfo") or {}).get("status"), "unknown")
        return {
            "tmdb_id": r.get("id"),
            "type": media_type,
            "title": r.get("title") or r.get("name") or "?",
            "year": date[:4] or None,
            "poster": f"{TMDB_IMG}{poster}" if poster else None,
            "overview": r.get("overview"),
            # "unknown" = nobody has requested it yet
            "status": status,
        }

    async def _results(self, path: str, page: int) -> dict:
        body = await self._get(path, {"page": page}) if "?" not in path else await self._get(f"{path}&page={page}")
        rows = [x for x in map(self._result, body.get("results", [])) if x]
        return {"results": rows, "page": body.get("page", page), "pages": body.get("totalPages", 1)}

    async def search(self, query: str, page: int = 1) -> dict:
        # Seerr rejects '+' for spaces, so encode the query by hand.
        return await self._results(f"/search?query={quote(query, safe='')}", page)

    async def discover(self, kind: str, page: int = 1) -> dict:
        path = {"trending": "/discover/trending", "movies": "/discover/movies", "tv": "/discover/tv"}[kind]
        return await self._results(path, page)

    async def request(self, media_type: str, tmdb_id: int, user_id: int | None = None) -> dict:
        body: dict = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            body["seasons"] = "all"
        headers = dict(self.headers)
        if user_id:
            headers["X-Api-User"] = str(user_id)  # request on behalf of this Seerr user
        resp = await self.client.post(f"{self.base}/request", json=body, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message")
            except ValueError:
                detail = None
            raise httpx.HTTPStatusError(detail or f"HTTP {resp.status_code}", request=resp.request, response=resp)
        r = resp.json()
        return {"status": REQUEST_STATUS.get(r.get("status"), "pending")}
