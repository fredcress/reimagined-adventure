#!/usr/bin/env bash
# Turn a Raspberry Pi OS Lite install into a stateless kiosk for the kitchen monitor.
#
#   sudo ./setup-kiosk.sh http://truenas.local:8085 [--pir <gpio>]
#
# Boots straight into Chromium (fullscreen, under the `cage` Wayland compositor)
# showing the URL, plus kiosk-display, which turns the screen off/on (night
# schedule from the server, touch to wake, optional motion sensor on <gpio>).
# Run it from the folder that also holds kiosk-display.py.
set -euo pipefail

URL="${1:-}"
PIR_GPIO=""
if [[ "${2:-}" == "--pir" ]]; then PIR_GPIO="${3:-}"; fi
if [[ -z "$URL" || $EUID -ne 0 ]]; then
  echo "usage: sudo $0 http://<server>:8085 [--pir <gpio>]" >&2
  exit 1
fi
URL="${URL%/}"
KIOSK_USER="${SUDO_USER:-pi}"
KIOSK_UID="$(id -u "$KIOSK_USER")"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing cage + chromium"
apt-get update
apt-get install -y --no-install-recommends cage curl fonts-dejavu-core python3
# Screen power control under Wayland: wlopm if packaged, else wlr-randr
apt-get install -y --no-install-recommends wlopm || apt-get install -y --no-install-recommends wlr-randr
if [[ -n "$PIR_GPIO" ]]; then apt-get install -y --no-install-recommends python3-gpiozero; fi
if apt-cache show chromium >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends chromium
else
  apt-get install -y --no-install-recommends chromium-browser
fi
CHROMIUM="$(command -v chromium || command -v chromium-browser)"

usermod -aG video,render,input "$KIOSK_USER"

echo "==> Writing helper scripts"
cat >/usr/local/bin/kiosk-wait <<EOF
#!/bin/sh
# Block until the server answers, so Chromium never boots onto an error page.
until curl -sf --max-time 3 "$URL/healthz" >/dev/null; do sleep 5; done
EOF
chmod +x /usr/local/bin/kiosk-wait

echo "==> Writing kiosk.service (user: $KIOSK_USER)"
cat >/etc/systemd/system/kiosk.service <<EOF
[Unit]
Description=Kitchen monitor kiosk
After=systemd-user-sessions.service plymouth-quit-wait.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=$KIOSK_USER
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
ExecStartPre=/usr/local/bin/kiosk-wait
ExecStart=/usr/bin/cage -d -- $CHROMIUM \\
  --kiosk $URL \\
  --ozone-platform=wayland \\
  --noerrdialogs --disable-infobars --no-first-run \\
  --disable-session-crashed-bubble --disable-features=Translate \\
  --overscroll-history-navigation=0 --disable-pinch \\
  --password-store=basic --check-for-update-interval=31536000 \\
  --autoplay-policy=no-user-gesture-required \\
  --user-data-dir=/tmp/kiosk-chromium
Restart=always
RestartSec=5
TimeoutStartSec=infinity

[Install]
WantedBy=graphical.target
EOF

echo "==> Writing kiosk-display.service"
install -m 755 "$HERE/kiosk-display.py" /usr/local/bin/kiosk-display
usermod -aG gpio "$KIOSK_USER" 2>/dev/null || true
cat >/etc/systemd/system/kiosk-display.service <<EOF
[Unit]
Description=Kitchen monitor screen power (night off, touch/motion wake)
After=kiosk.service
Wants=kiosk.service

[Service]
User=$KIOSK_USER
Environment=XDG_RUNTIME_DIR=/run/user/$KIOSK_UID
Environment=WAKE_MINUTES=2
Environment=PIR_GPIO=$PIR_GPIO
Environment=PIR_IDLE_MINUTES=10
ExecStart=/usr/local/bin/kiosk-display $URL
Restart=always
RestartSec=10

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload
systemctl disable getty@tty1.service || true
systemctl enable kiosk.service kiosk-display.service
systemctl set-default graphical.target

cat <<EOF

Done. Reboot to start the kiosk:  sudo reboot

Once it looks right, make the SD card read-only so power cuts can't corrupt it:
  sudo raspi-config nonint do_overlayfs 0 && sudo reboot
(To change anything later: sudo raspi-config nonint do_overlayfs 1, reboot, edit, re-enable.)

Logs: journalctl -u kiosk -u kiosk-display -f
Screen schedule: set NIGHT_START/NIGHT_END and SCREEN_OFF_AT_NIGHT=1 in the server's .env.
Timings: sudo systemctl edit kiosk-display  (WAKE_MINUTES, PIR_IDLE_MINUTES)
EOF
