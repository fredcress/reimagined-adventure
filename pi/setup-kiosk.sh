#!/usr/bin/env bash
# Turn a Raspberry Pi OS Lite install into a stateless kiosk for the kitchen monitor.
#
#   sudo ./setup-kiosk.sh http://truenas.local:8085
#
# Boots straight into Chromium (fullscreen, under the `cage` Wayland compositor)
# showing the URL. Nothing else runs on the Pi; all data comes from the server.
set -euo pipefail

URL="${1:-}"
if [[ -z "$URL" || $EUID -ne 0 ]]; then
  echo "usage: sudo $0 http://<server>:8085" >&2
  exit 1
fi
URL="${URL%/}"
KIOSK_USER="${SUDO_USER:-pi}"

echo "==> Installing cage + chromium"
apt-get update
apt-get install -y --no-install-recommends cage curl fonts-dejavu-core
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

systemctl daemon-reload
systemctl disable getty@tty1.service || true
systemctl enable kiosk.service
systemctl set-default graphical.target

cat <<EOF

Done. Reboot to start the kiosk:  sudo reboot

Once it looks right, make the SD card read-only so power cuts can't corrupt it:
  sudo raspi-config nonint do_overlayfs 0 && sudo reboot
(To change anything later: sudo raspi-config nonint do_overlayfs 1, reboot, edit, re-enable.)

Logs: journalctl -u kiosk -f
EOF
