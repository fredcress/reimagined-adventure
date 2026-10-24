"""Endpoint smoke tests in demo mode, plus the ntfy payload against a fake server."""

import asyncio
import importlib
import json

import httpx
import pytest


@pytest.fixture(scope="module")
def client(monkeypatch_module):
    monkeypatch_module.setenv("DEMO", "1")
    import app.main as m

    importlib.reload(m.config)
    m = importlib.reload(m)
    from fastapi.testclient import TestClient

    with TestClient(m.app) as c:
        yield c


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_state_has_new_sections(client):
    s = client.get("/api/state").json()
    assert s["nas"]["state"] in ("ok", "warn", "error")
    assert s["features"]["seerr_requests"] is True
    assert "timers" in s and "weather" in s


def test_seerr_search_and_request(client):
    res = client.get("/api/seerr/search", params={"q": "padd"}).json()["results"]
    target = next(r for r in res if r["status"] == "unknown")
    assert client.post("/api/seerr/request", json={"type": target["type"], "tmdb_id": target["tmdb_id"]}).json() == {"status": "pending"}
    again = client.get("/api/seerr/search", params={"q": "padd"}).json()["results"]
    assert next(r for r in again if r["tmdb_id"] == target["tmdb_id"])["status"] == "pending"
    assert client.post("/api/seerr/request", json={"type": "person", "tmdb_id": 1}).status_code == 422


def test_display_follows_alarm(client):
    assert client.get("/api/display").json()["alarm"] is False
    t = client.post("/api/timers", json={"seconds": 1}).json()[0]
    import time

    time.sleep(1.1)
    d = client.get("/api/display").json()
    assert d["alarm"] is True and d["on"] is True
    client.delete(f"/api/timers/{t['id']}")


def test_ntfy_payload():
    from app.notify import Ntfy

    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "x"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            n = Ntfy("https://ntfy.example/", "kitchen", "tk_123", c)
            return await n.send("Title", "Body", tags=["zap"], priority=4)

    assert asyncio.run(run()) is True
    assert seen["url"] == "https://ntfy.example"
    assert seen["auth"] == "Bearer tk_123"
    assert seen["body"] == {"topic": "kitchen", "title": "Title", "message": "Body", "priority": 4, "tags": ["zap"]}


def test_usual_prices_and_day_navigation(client):
    s = client.get("/api/state").json()
    assert s["energy"]["typical_avg"] > 0
    assert client.get("/api/energy/prices").json()["typical_avg"] > 0
    days = client.get("/api/energy/usage").json()["days"]
    mid = days[len(days) // 2]["date"]
    d = client.get(f"/api/energy/usage/{mid}").json()
    assert d["typical_paid"] > 0
    assert d["prev"] < mid < d["next"]
    first = client.get(f"/api/energy/usage/{days[0]['date']}").json()
    assert first["prev"] is None


def test_todo_api_round_trip(client):
    items = client.post("/api/todos", json={"text": "Buy bread"}).json()["items"]
    bread = next(i for i in items if i["text"] == "Buy bread")
    assert items[0]["id"] == bread["id"]
    items = client.patch(f"/api/todos/{bread['id']}", json={"done": True}).json()["items"]
    assert next(i for i in items if i["id"] == bread["id"])["done"] is True
    assert client.post("/api/todos", json={"text": "   "}).status_code == 422
    assert client.patch("/api/todos/nope", json={"done": True}).status_code == 404
    client.delete(f"/api/todos/{bread['id']}")
    assert all(i["id"] != bread["id"] for i in client.get("/api/state").json()["todos"]["items"])
