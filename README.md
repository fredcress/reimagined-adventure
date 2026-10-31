# Kitchen Monitor

A touch dashboard for a 10" 1024×600 screen in the kitchen showing Octopus Agile
prices and usage, a shared to-do list, kitchen timers, weather and Seerr requests.

```
[Octopus API]   [Seerr]   [Open-Meteo]   [TrueNAS]
        \            |          /           /
   kitchen-monitor container (TrueNAS / Dockge)
     - polls each source, caches, does the maths
     - serves the page + JSON
                     |
     Raspberry Pi: Chromium kiosk, stateless
```

The Pi holds no config, keys or state. If the SD card dies, re-flash it and run
one script.

## What's on screen

**Home**: current/next Agile price, the cheapest 1h and 3h windows,
**appliance advice** ("Dishwasher eco in 6h": the delay to set for the
cheapest run), a price chart to the end of published prices, yesterday's usage and cost,
and the **shared to-do list** (tick items off with a tap; add from any phone or the screen; items can repeat daily, weekly on chosen days or monthly on a date, and come back unticked when due).
The top bar has Timer, Weather, Requests (Seerr, with the pending count) and NAS chips.

Tap a panel to drill in:

| Panel | Tabs |
|---|---|
| Energy | Today / Tomorrow (every half hour, tap a bar), Usage (31 days, tap a day for its half-hourly kWh coloured by price) |
| To do | Add (on-screen keyboard on the kiosk, the phone's own keyboard on a phone), tick, untick, delete, clear done. Ticked items vanish after a week. |
| Requests (top bar) | Pending / Processing / Available / All, **Search** (on-screen keyboard) and **Trending**. Tap a poster, then Request, then tap again to confirm. |
| Weather (top bar) | Now, next rain, drying-day hint, next 12h, rain chance for 24h, 7 days |
| NAS (top bar) | Pool health and usage, active TrueNAS alerts |
| Timers (top bar) | Presets 1-45 min, custom length, optional label (Pasta, Rice…), +1/+5 min, cancel |

**Kitchen timers** live on the server, so they survive a page reload and show
on any screen that has the page open. Running timers count down in the top bar
on every view. When one finishes, the whole screen flashes with a beep, even in
night mode, and offers +1 min, +5 min or Dismiss. Timers are kept in memory, so
a container restart clears them. For sound, the Pi needs an audio output: most
10" HDMI screens have small speakers, or use a USB speaker.

**Seerr requests** go through the API key, so Seerr treats them as admin
requests and normally auto-approves them. Set `SEERR_REQUEST_USER_ID` to request
as a specific user instead (their request limits and approval rules then apply),
or `SEERR_ALLOW_REQUESTS=0` to keep the screen browse-only. TV requests ask for
all seasons.

**Push notifications (ntfy)** go to your phone:
- tomorrow's prices when they land (~4pm): range, cheapest 3h, any plunge slots, and when to start each appliance;
- kitchen timers finishing;
- the NAS going amber/red, and back to OK.

Set `NTFY_URL`/`NTFY_TOPIC` and install the ntfy app subscribed to the same
topic. Until you host your own server, `https://ntfy.sh` works. Anyone who
guesses the topic name can read it there, so make it long and random.
`POST /api/ntfy/test` sends a test message. Seerr has its own notification
settings, so "your request is available" alerts are best set up there (its
webhook agent can post to ntfy).

**Screen off.** The browser can't turn the backlight off, so this runs on the Pi:
`pi/kiosk-display.py` (installed by the setup script) asks the server every
15 s whether the screen should be on.
- `SCREEN_OFF_AT_NIGHT=1` turns it off between `NIGHT_START` and `NIGHT_END`.
- A touch wakes it for 2 minutes.
- A ringing timer always turns it on.
- With a PIR motion sensor (`--pir <gpio>` at setup, e.g. an HC-SR501 on GPIO17),
  the screen is only on when someone has moved or touched it in the last
  10 minutes, day or night.

It switches the display with `wlopm` (or `wlr-randr` as a fallback). Some cheap
HDMI screens keep the backlight on with no signal and just show "No signal".
If yours does, the dim night clock is the better option.

**Appliance advice** checks start times in steps of the delay-start button
(`APPLIANCE_DELAY_STEP`, default 1 h) and picks the start with the lowest
average price over the whole cycle. It only uses published prices. Between noon
and ~4pm tomorrow's prices aren't out yet, so the advice is flagged "may change
at 4pm".

Detail views go back to Home after 90 s without a touch. Between `NIGHT_START`
and `NIGHT_END` the screen shows a dim clock and price. Tap it to wake.

Notes:
- Octopus consumption is **T+1**: yesterday's readings usually arrive during
  the day. A day is marked "partial" until all 48 half-hours are in.
- Tomorrow's Agile prices are usually published around **4pm**.
- The detail views are read-only. Nothing on the kitchen screen can approve
  or delete anything.

## Run it

### 1. Image: built by GitHub

Every push to `main` runs the tests and publishes
`ghcr.io/fredcress/reimagined-adventure:latest` (`.github/workflows/docker.yml`).
The NAS never builds anything.

After the first run, open the package on GitHub (your profile → Packages →
reimagined-adventure → Package settings) and make sure it's **Public**, so the
NAS can pull it without logging in.

### 2. Deploy on TrueNAS with Dockge

1. In Dockge: **+ Compose** → name it `kitchen-monitor` → paste `compose.yaml`.
2. In the stack's `.env` box, paste `.env.example` and fill in the keys (see
   below). Leave `DEMO=1` first to check the screen, then set `DEMO=0`.
3. **Deploy**. Open `http://<nas>:8085` in a browser sized ~1024×600 to check.
4. To update after a push: wait for the GitHub Action to go green, then click **Update** in
   Dockge. The kitchen screen notices the new version and reloads itself.

Port `8085` is set in `compose.yaml`. Change it if it clashes. `compose.dev.yaml`
builds from a local checkout instead.

### Mealie (recipes)

`extras/mealie/compose.yaml` is a ready Dockge stack for Mealie: paste it as a
new stack called `mealie`, deploy, and log in at `http://<nas>:9925` with
`changeme@example.com` / `MyPassword` (change it at once). Once it has some
recipes and a meal plan, the dashboard can pull "tonight's dinner" and step-by-step
recipes from Mealie's API. That hasn't been built yet.

### 3. Set up the Pi

Flash **Raspberry Pi OS Lite** (64-bit) with Raspberry Pi Imager, setting up
Wi-Fi/SSH and a user in the Imager settings. Then:

```sh
scp pi/setup-kiosk.sh pi/kiosk-display.py <user>@<pi>:
ssh <user>@<pi> 'sudo ./setup-kiosk.sh http://<nas-ip>:8085 && sudo reboot'
# with a motion sensor on GPIO17:  sudo ./setup-kiosk.sh http://<nas-ip>:8085 --pir 17
```

The script installs `cage` (a minimal Wayland kiosk compositor) and Chromium,
and adds a systemd service that waits for the server and then opens the page
fullscreen. Once you're happy, enable the read-only overlay filesystem as the
script prints.

Any Pi 3B+/4/5 is fine. A Zero 2 W (512 MB) works but is sluggish; on any Pi
with under 1 GB of RAM the script also turns on compressed swap in RAM (zram,
instead of swapping to the SD card), starts Chromium in low-memory mode, and
restarts the browser every night at 04:00. Check headroom with `free -m`.

### 4. Phones

Open `http://<nas-ip>:8085` on the phone and add it to the home screen. It then
opens full screen with its own icon, like an app.

- **iPhone (Safari):** Share → Add to Home Screen. Works over plain http.
- **Android (Chrome):** ⋮ → Add to home screen / Install app. Over plain http,
  Chrome only makes a shortcut that opens in a browser tab. For a real app
  window it needs https, for example through a reverse proxy with a certificate,
  or `tailscale serve`.

Over https, a service worker also keeps the page itself for offline starts (never
the data: prices, timers and the to-do list always come live from the server).
The kiosk is unaffected: it uses plain http, which never gets the service worker.

## Configuration (`.env`)

| Variable | |
|---|---|
| `DEMO` | `1` = fake data for everything |
| `OCTOPUS_API_KEY`, `OCTOPUS_ACCOUNT` | From the Octopus dashboard → Personal details → API access. The meter, MPAN and tariff are discovered from the account. |
| `OCTOPUS_MPAN`, `OCTOPUS_SERIAL` | Only if you have several import meters |
| `OCTOPUS_API_URL` | Optional API host (default `https://api.octopus.energy`; `https://api.oegb-kraken.energy` also works) |
| `SEERR_URL`, `SEERR_API_KEY` | Settings → General. Works with Jellyseerr/Overseerr too. |
| `PRICE_CHEAP`, `PRICE_EXPENSIVE` | Colour thresholds in p/kWh (default 15 / 25). Prices ≤ 0 show as "plunge". |
| `CHEAPEST_WINDOWS` | Window lengths in hours for the home tiles (default `1,3`) |
| `USAGE_DAYS` | Days in the home usage mini-chart (default 7) |
| `APPLIANCES` | `"Dishwasher eco=4:30@0.85,Dishwasher quick=2:30@1.15,Clothes 40 + dry=5:15@3.5 finish"`: name=cycle length, optional `@kWh` per cycle (compare by cost per run, show savings), optional ` finish` for timers that set the end time. Programmes sharing a first word are alternatives: home shows the better one |
| `APPLIANCE_DELAY_STEP` | Hours per press of the delay-start button (default 1) |
| `TIMER_LABELS` | Quick labels on the Timers screen |
| `NIGHT_START`, `NIGHT_END` | Night clock, `HH:MM`. Leave blank to disable. |
| `SEERR_ALLOW_REQUESTS`, `SEERR_REQUEST_USER_ID` | Requests from the screen (default on, as admin) |
| `LATITUDE`, `LONGITUDE` | Weather location |
| `TRUENAS_URL`, `TRUENAS_API_KEY`, `TRUENAS_VERIFY_SSL` | NAS chip. `https://` only. Needs TrueNAS 25.04+ (JSON-RPC API). |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | Push notifications |
| `NTFY_PRICES`, `NTFY_TIMERS`, `NTFY_NAS` | Which pushes to send (all on by default) |
| `WIFI_SSID`, `WIFI_PASSWORD` | Guest Wi-Fi: a "Wi-Fi" chip in the top bar opens a QR code to join. Anyone on the dashboard can see it, so use the guest network. |
| `WIFI_SECURITY`, `WIFI_HIDDEN` | `WPA` (default, covers WPA2/3), `WEP` or `nopass`; `1` if the name isn't broadcast |
| `SCREEN_OFF_AT_NIGHT` | `1` = switch the screen off at night (needs kiosk-display on the Pi) |
| `TZ` | Default `Europe/London` |
| `DATA_DIR` | Where the to-do list is saved (default `/data`, a named volume in `compose.yaml`) |

A service left unconfigured shows "Not configured". The rest still work.

## How it works

`app/main.py` runs background pollers and keeps the last good result. If an API
is down, the screen keeps showing old data and a ⚠ chip appears in the top bar.

| Data | Refresh |
|---|---|
| Agile unit rates + standing charge (31 days back to tomorrow) | 15 min |
| Consumption (31 days) | 60 min |
| Seerr counts + latest | 2 min |
| Weather | 15 min |
| TrueNAS health | 5 min |

Detail views (Seerr lists, paging) are fetched live when you tap.
The to-do list is saved to `DATA_DIR/todos.json` after every change. In demo
mode it stays in memory.
Seerr posters come straight from TMDB's image CDN, so the Pi needs internet for those.

The page polls `/api/state` every 10 s. If the server is unreachable it keeps
the last data on screen and shows "Server offline".

API endpoints: `/api/state`, `/api/energy/prices?day=YYYY-MM-DD`,
`/api/energy/usage`, `/api/energy/usage/YYYY-MM-DD`,
`/api/seerr/requests?filter=all|pending|approved|processing|available&skip=&take=`,
`/api/seerr/search?q=`, `/api/seerr/discover?kind=trending|movies|tv`,
`POST /api/seerr/request`, `/api/todos` (GET/POST, `PATCH|DELETE /api/todos/{id}`, `POST /api/todos/clear-done`), `/api/wifi`, `/api/wifi/qr.svg`, `/api/display`, `POST /api/ntfy/test`,
`GET/POST /api/timers`, `POST /api/timers/{id}/add`, `DELETE /api/timers/{id}`,
`/healthz`.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
pytest                                         # tariff maths, incl. clock changes
DEMO=1 uvicorn app.main:app --reload --port 8080
```

The frontend is plain HTML/CSS/JS in `static/`, with no build step and no
external libraries, so it works offline on the Pi.
