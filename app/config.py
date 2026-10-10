"""Settings, read once from environment variables (see .env.example)."""

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _float(name: str, default: float | None) -> float | None:
    value = _env(name)
    return float(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    return default if value is None else value.lower() in ("1", "true", "yes", "on")


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
    octopus_api_url: str

    jellyfin_url: str | None
    jellyfin_api_key: str | None

    seerr_url: str | None
    seerr_api_key: str | None
    seerr_allow_requests: bool
    seerr_request_user_id: int | None

    latitude: float | None
    longitude: float | None

    truenas_url: str | None
    truenas_api_key: str | None
    truenas_verify_ssl: bool

    ntfy_url: str | None
    ntfy_topic: str | None
    ntfy_token: str | None
    ntfy_prices: bool
    ntfy_timers: bool
    ntfy_nas: bool

    screen_off_at_night: bool

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
    def weather_enabled(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def truenas_enabled(self) -> bool:
        return bool(self.truenas_url and self.truenas_api_key)

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
        octopus_api_url=_url("OCTOPUS_API_URL") or "https://api.octopus.energy",
        jellyfin_url=_url("JELLYFIN_URL"),
        jellyfin_api_key=_env("JELLYFIN_API_KEY"),
        seerr_url=_url("SEERR_URL"),
        seerr_api_key=_env("SEERR_API_KEY"),
        seerr_allow_requests=_bool("SEERR_ALLOW_REQUESTS", True),
        seerr_request_user_id=int(_env("SEERR_REQUEST_USER_ID")) if _env("SEERR_REQUEST_USER_ID") else None,
        latitude=_float("LATITUDE", None),
        longitude=_float("LONGITUDE", None),
        truenas_url=_url("TRUENAS_URL"),
        truenas_api_key=_env("TRUENAS_API_KEY"),
        truenas_verify_ssl=_bool("TRUENAS_VERIFY_SSL", False),
        ntfy_url=_url("NTFY_URL"),
        ntfy_topic=_env("NTFY_TOPIC"),
        ntfy_token=_env("NTFY_TOKEN"),
        ntfy_prices=_bool("NTFY_PRICES", True),
        ntfy_timers=_bool("NTFY_TIMERS", True),
        ntfy_nas=_bool("NTFY_NAS", True),
        screen_off_at_night=_bool("SCREEN_OFF_AT_NIGHT", False),
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
