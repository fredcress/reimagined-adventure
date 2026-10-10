"""TrueNAS health over its JSON-RPC WebSocket API (TrueNAS 25.04+).

Uses wss:// only: TrueNAS revokes API keys that are sent over plain HTTP/ws.
"""

import itertools
import json
import ssl

import websockets

BAD_LEVELS = {"ERROR", "CRITICAL", "ALERT", "EMERGENCY"}
WARN_LEVELS = {"WARNING"}


class TrueNAS:
    def __init__(self, url: str, api_key: str, verify_ssl: bool):
        if not url.startswith("https://"):
            raise ValueError("TRUENAS_URL must be https:// (TrueNAS revokes API keys sent over http)")
        self.ws_url = "wss://" + url.removeprefix("https://").rstrip("/") + "/api/current"
        self.api_key = api_key
        self.ssl = ssl.create_default_context()
        if not verify_ssl:  # the NAS usually has a self-signed certificate
            self.ssl.check_hostname = False
            self.ssl.verify_mode = ssl.CERT_NONE

    async def health(self) -> dict:
        ids = itertools.count(1)
        async with websockets.connect(self.ws_url, ssl=self.ssl, open_timeout=10, max_size=None) as ws:

            async def call(method: str, params: list | None = None):
                msg_id = next(ids)
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or []}))
                while True:
                    reply = json.loads(await ws.recv())
                    if reply.get("id") != msg_id:
                        continue  # event notifications
                    if "error" in reply:
                        err = reply["error"]
                        raise RuntimeError(f"{method}: {err.get('message') or err}")
                    return reply.get("result")

            if not await call("auth.login_with_api_key", [self.api_key]):
                raise RuntimeError("TrueNAS rejected the API key")
            pools = await call("pool.query")
            alerts = await call("alert.list")
            info = await call("system.info")
        return summarise(pools, alerts, info)


def summarise(pools: list[dict], alerts: list[dict], info: dict | None) -> dict:
    out_pools = []
    for p in pools or []:
        size, used = p.get("size"), p.get("allocated")
        out_pools.append(
            {
                "name": p.get("name"),
                "status": p.get("status"),
                "healthy": bool(p.get("healthy")) and p.get("status") == "ONLINE",
                "used_pct": round(100 * used / size, 1) if size and used is not None else None,
            }
        )
    active = [
        {"level": a.get("level", "INFO"), "text": (a.get("formatted") or a.get("text") or "").strip()}
        for a in alerts or []
        if not a.get("dismissed")
    ]
    levels = {a["level"] for a in active}
    if any(not p["healthy"] for p in out_pools) or levels & BAD_LEVELS:
        state = "error"
    elif levels & WARN_LEVELS or any((p["used_pct"] or 0) >= 90 for p in out_pools):
        state = "warn"
    else:
        state = "ok"
    important = [a for a in active if a["level"] in BAD_LEVELS | WARN_LEVELS]
    return {
        "state": state,
        "pools": out_pools,
        "alerts": important + [a for a in active if a not in important],
        "version": (info or {}).get("version"),
        "uptime_s": (info or {}).get("uptime_seconds"),
    }
