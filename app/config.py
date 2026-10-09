"""Settings, read once from environment variables (see .env.example)."""

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value else default


def _url(name: str) -> str | None:
    value = _env(name)
    return value.rstrip("/") if value else None


@dataclass(frozen=True)
class Appliance:
    name: str
    hours: float
    kwh: float | None  # per cycle; enables a £ saving estimate


def _duration(text: str) -> float:
    """'4:30' or '4.5' -> 4.5 hours."""
    if ":" in text:
        h, m = text.split(":", 1)
        return int(h or 0) + int(m) / 60
    return float(text)


def _appliances(raw: str | None) -> tuple[Appliance, ...]:
    """'Dishwasher eco=4:30@0.9, Dishwasher quick=2:30' -> Appliances."""
    out = []
    for part in (raw or "").split(","):
        name, sep, spec = part.strip().rpartition("=")
        if not sep or not name.strip():
            continue
        duration, _, kwh = spec.partition("@")
        out.append(
            Appliance(
                name=name.strip(),
                hours=_duration(duration.strip()),
                kwh=float(kwh.lower().removesuffix("kwh")) if kwh.strip() else None,
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    tz: str
    demo: bool

    octopus_api_key: str | None
    octopus_account: str | None
    octopus_mpan: str | None
    octopus_serial: str | None

    jellyfin_url: str | None
    jellyfin_api_key: str | None

    seerr_url: str | None
    seerr_api_key: str | None

    price_cheap: float
    price_expensive: float
    cheapest_windows: tuple[float, ...]
    usage_days: int
    appliances: tuple[Appliance, ...]
    appliance_step_hours: float

    night_start: str | None
    night_end: str | None
    timer_labels: tuple[str, ...]

    @property
    def octopus_enabled(self) -> bool:
        return bool(self.octopus_api_key and self.octopus_account)

    @property
    def jellyfin_enabled(self) -> bool:
        return bool(self.jellyfin_url and self.jellyfin_api_key)

    @property
    def seerr_enabled(self) -> bool:
        return bool(self.seerr_url and self.seerr_api_key)


def load() -> Settings:
    windows = _env("CHEAPEST_WINDOWS", "1,3")
    return Settings(
        tz=_env("TZ", "Europe/London"),
        demo=_env("DEMO", "0").lower() in ("1", "true", "yes"),
        octopus_api_key=_env("OCTOPUS_API_KEY"),
        octopus_account=_env("OCTOPUS_ACCOUNT"),
        octopus_mpan=_env("OCTOPUS_MPAN"),
        octopus_serial=_env("OCTOPUS_SERIAL"),
        jellyfin_url=_url("JELLYFIN_URL"),
        jellyfin_api_key=_env("JELLYFIN_API_KEY"),
        seerr_url=_url("SEERR_URL"),
        seerr_api_key=_env("SEERR_API_KEY"),
        price_cheap=_float("PRICE_CHEAP", 15.0),
        price_expensive=_float("PRICE_EXPENSIVE", 25.0),
        cheapest_windows=tuple(float(w) for w in windows.split(",") if w.strip()),
        usage_days=int(_float("USAGE_DAYS", 7)),
        appliances=_appliances(_env("APPLIANCES", "Dishwasher eco=4:30,Dishwasher quick=2:30")),
        appliance_step_hours=max(0.5, _float("APPLIANCE_DELAY_STEP", 1.0)),
        night_start=_env("NIGHT_START"),
        night_end=_env("NIGHT_END"),
        timer_labels=tuple(
            x.strip() for x in _env("TIMER_LABELS", "Pasta,Rice,Eggs,Oven,Tea").split(",") if x.strip()
        ),
    )
