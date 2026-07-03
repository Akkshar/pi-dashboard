#!/bin/bash
# Launch (or relaunch) the dashboard browser
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export WAYLAND_DISPLAY=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -m1 '^wayland-' || echo wayland-0)
for i in $(seq 1 30); do
  /usr/bin/curl -s http://127.0.0.1:8080/ >/dev/null && break
  sleep 1
done
exec chromium --app=http://127.0.0.1:8080 --ozone-platform=wayland --start-maximized \
  --user-data-dir=/home/screenrpi/.config/chromium-dashboard --password-store=basic \
  --disable-session-crashed-bubble --disable-features=Translate
