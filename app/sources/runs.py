"""Running widget: this week's distance, today's session and race countdown.

Read from the runs app's small JSON endpoint (GET /api/widget, X-Kiosk-Token).
Distances are km, times seconds, paces seconds per km; blocks are None when
there is nothing to show.
"""

import httpx

BLOCKS = ("week", "month", "year", "fitness", "readiness", "plan", "today_session", "last_run")


def clean(body: dict) -> dict:
    """Keep the parts the screen knows, so a changed reply can't break the page."""
    if not isinstance(body, dict):
        raise ValueError("unexpected reply from the runs app")
    out = {k: body.get(k) if isinstance(body.get(k), dict) else None for k in BLOCKS}
    for k in ("today", "coach_note", "url"):
        out[k] = body.get(k) if isinstance(body.get(k), str) else None
    return out


class Runs:
    def __init__(self, url: str, token: str, client: httpx.AsyncClient):
        self.url, self.token, self.client = f"{url}/api/widget", token, client

    async def widget(self) -> dict:
        resp = await self.client.get(self.url, headers={"X-Kiosk-Token": self.token})
        if resp.status_code == 401:
            raise RuntimeError("the runs app rejected RUNS_KIOSK_TOKEN (it must match its KIOSK_TOKEN)")
        resp.raise_for_status()
        return clean(resp.json())
