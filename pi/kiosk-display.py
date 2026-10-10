#!/usr/bin/env python3
"""Switch the kiosk screen on/off. Runs on the Pi next to the kiosk.

    kiosk-display.py http://nas:8085

- Asks the server (/api/display) whether the screen should be on. The server
  says off overnight when SCREEN_OFF_AT_NIGHT=1, and forces it on while a timer
  alarm is ringing.
- Any touch wakes the screen for WAKE_MINUTES.
- With PIR_GPIO set (a motion sensor on that GPIO pin), the screen is only on
  while someone is around: motion wakes it, and it turns off after
  PIR_IDLE_MINUTES without motion or touch, day or night.

Turns the display off with `wlopm` (or `wlr-randr` as a fallback) through the
Wayland compositor (cage). Stdlib only, plus gpiozero for the motion sensor.
"""

import glob
import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

URL = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KIOSK_URL", "")).rstrip("/")
WAKE = float(os.environ.get("WAKE_MINUTES", "2")) * 60
PIR_GPIO = os.environ.get("PIR_GPIO")
PIR_IDLE = float(os.environ.get("PIR_IDLE_MINUTES", "10")) * 60
POLL = 15

last_activity = time.monotonic()
lock = threading.Lock()


def log(*args):
    print(*args, flush=True)


def activity():
    global last_activity
    with lock:
        last_activity = time.monotonic()


def wayland_env() -> dict:
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime
    sockets = sorted(p for p in glob.glob(f"{runtime}/wayland-*") if not p.endswith(".lock"))
    if sockets:
        env["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])
    return env


def set_screen(on: bool):
    env = wayland_env()
    if shutil.which("wlopm"):
        cmd = ["wlopm", "--on" if on else "--off", "*"]
        subprocess.run(cmd, env=env, check=False, capture_output=True)
        return
    # Fallback: wlr-randr per output
    out = subprocess.run(["wlr-randr"], env=env, capture_output=True, text=True).stdout
    for name in [line.split()[0] for line in out.splitlines() if line and not line[0].isspace()]:
        subprocess.run(["wlr-randr", "--output", name, "--on" if on else "--off"], env=env, check=False)


def server_state(previous: dict) -> dict:
    try:
        with urllib.request.urlopen(f"{URL}/api/display", timeout=5) as r:
            return json.load(r)
    except Exception as e:  # server down: keep the screen on rather than leave it dark
        log("display poll failed:", e)
        return {"on": True, "alarm": previous.get("alarm", False)}


def open_inputs() -> list[int]:
    fds = []
    for path in glob.glob("/dev/input/event*"):
        try:
            fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            pass
    return fds


def start_pir():
    try:
        from gpiozero import MotionSensor
    except ImportError:
        log("PIR_GPIO set but gpiozero is not installed (sudo apt install python3-gpiozero)")
        return None
    pir = MotionSensor(int(PIR_GPIO))
    pir.when_motion = activity
    log(f"motion sensor on GPIO{PIR_GPIO}")
    return pir


def main():
    if not URL:
        sys.exit("usage: kiosk-display.py http://<server>:8085")
    pir = start_pir() if PIR_GPIO else None
    fds, fds_at = open_inputs(), time.monotonic()
    server, polled = {"on": True, "alarm": False}, 0.0
    screen = None
    while True:
        ready, _, _ = select.select(fds, [], [], 1.0)
        for fd in ready:
            try:
                if os.read(fd, 4096):
                    activity()
            except OSError:
                pass
        now = time.monotonic()
        if now - fds_at > 60:  # pick up re-plugged touch screens
            for fd in fds:
                os.close(fd)
            fds, fds_at = open_inputs(), now
        if now - polled > POLL:
            server, polled = server_state(server), now
        with lock:
            idle = now - last_activity
        if pir:
            want = server.get("alarm") or idle < PIR_IDLE
        else:
            want = server.get("on", True) or idle < WAKE
        if want != screen:
            log("screen", "on" if want else "off")
            set_screen(want)
            screen = want


if __name__ == "__main__":
    main()
