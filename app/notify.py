"""Push notifications via ntfy (self-hosted or ntfy.sh)."""

import logging

import httpx

log = logging.getLogger("kitchen.ntfy")


class Ntfy:
    def __init__(self, url: str | None, topic: str | None, token: str | None, client: httpx.AsyncClient):
        self.url = (url or "").rstrip("/")
        self.topic = topic
        self.token = token
        self.client = client

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.topic)

    async def send(
        self, title: str, message: str, tags: list[str] | None = None, priority: int = 3, click: str | None = None
    ) -> bool:
        """Publish one message; never raises (a missed push isn't worth crashing a poller)."""
        if not self.enabled:
            return False
        body = {"topic": self.topic, "title": title, "message": message, "priority": priority}
        if tags:
            body["tags"] = tags
        if click:
            body["click"] = click
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = await self.client.post(self.url, json=body, headers=headers)
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.warning("ntfy send failed: %s", e)
            return False
