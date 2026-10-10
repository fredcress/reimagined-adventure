"""Octopus Energy REST API client (https://developer.octopus.energy/)."""

import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from ..agile import Rate, Reading, parse_rates, parse_readings
from ..config import Settings

# E-1R-AGILE-24-10-01-C -> AGILE-24-10-01
TARIFF_RE = re.compile(r"^E-1R-(?P<product>.+)-[A-P]$")


@dataclass
class Agreement:
    tariff_code: str
    product_code: str
    start: datetime
    end: datetime | None


@dataclass
class Meter:
    mpan: str
    serial: str
    agreements: list[Agreement] = field(default_factory=list)

    def agreements_between(self, start: datetime, end: datetime) -> list[Agreement]:
        return [a for a in self.agreements if a.start < end and (a.end is None or a.end > start)]


class Octopus:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s = settings
        self.client = client
        self.auth = (settings.octopus_api_key or "", "")
        self.base = f"{settings.octopus_api_url}/v1"
        self.meter: Meter | None = None

    async def _get_all(self, url: str, params: dict, auth: bool = False) -> list[dict]:
        rows: list[dict] = []
        next_url: str | None = url
        while next_url:
            resp = await self.client.get(
                next_url,
                params=params if next_url == url else None,
                auth=self.auth if auth else None,
            )
            resp.raise_for_status()
            body = resp.json()
            rows.extend(body.get("results", []))
            next_url = body.get("next")
        return rows

    async def discover(self) -> Meter:
        """Find the import electricity meter and its tariff history."""
        resp = await self.client.get(
            f"{self.base}/accounts/{self.s.octopus_account}/", auth=self.auth
        )
        resp.raise_for_status()
        account = resp.json()

        for prop in account.get("properties", []):
            if prop.get("moved_out_at"):
                continue
            for point in prop.get("electricity_meter_points", []):
                if point.get("is_export"):
                    continue
                mpan = point["mpan"]
                if self.s.octopus_mpan and mpan != self.s.octopus_mpan:
                    continue
                serials = [m["serial_number"] for m in point.get("meters", [])]
                serial = self.s.octopus_serial or (serials[-1] if serials else None)
                if not serial:
                    continue
                agreements = []
                for a in point.get("agreements", []):
                    m = TARIFF_RE.match(a["tariff_code"])
                    if not m:
                        continue  # e.g. Economy 7 (2R) - not supported
                    agreements.append(
                        Agreement(
                            tariff_code=a["tariff_code"],
                            product_code=m["product"],
                            start=datetime.fromisoformat(a["valid_from"]),
                            end=datetime.fromisoformat(a["valid_to"]) if a.get("valid_to") else None,
                        )
                    )
                self.meter = Meter(mpan, serial, sorted(agreements, key=lambda a: a.start))
                return self.meter

        raise RuntimeError("No active import electricity meter found on the account")

    async def _tariff_series(self, kind: str, start: datetime, end: datetime) -> list[Rate]:
        meter = self.meter or await self.discover()
        rates: list[Rate] = []
        for a in meter.agreements_between(start, end):
            url = (
                f"{self.base}/products/{a.product_code}/electricity-tariffs/"
                f"{a.tariff_code}/{kind}/"
            )
            rows = await self._get_all(
                url,
                {
                    "period_from": max(start, a.start).isoformat(),
                    "period_to": min(end, a.end).isoformat() if a.end else end.isoformat(),
                    "page_size": 1500,
                },
            )
            rates.extend(parse_rates(rows))
        return sorted(rates, key=lambda r: r.start)

    async def unit_rates(self, start: datetime, end: datetime) -> list[Rate]:
        return await self._tariff_series("standard-unit-rates", start, end)

    async def standing_charges(self, start: datetime, end: datetime) -> list[Rate]:
        return await self._tariff_series("standing-charges", start, end)

    async def consumption(self, start: datetime, end: datetime) -> list[Reading]:
        meter = self.meter or await self.discover()
        rows = await self._get_all(
            f"{self.base}/electricity-meter-points/{meter.mpan}/meters/{meter.serial}/consumption/",
            {
                "period_from": start.isoformat(),
                "period_to": end.isoformat(),
                "page_size": 25000,
                "order_by": "period",
            },
            auth=True,
        )
        return parse_readings(rows)
