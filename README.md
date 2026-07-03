# Pi Touchscreen Dashboard

A widget dashboard for a Raspberry Pi 5 + official 7" touchscreen, served by a
zero-dependency* Python backend. Clock, weather (Open-Meteo), RSS news, system
monitor (CPU/temp/fan/RAM/disk + hourly speed tests), reminders, hardware
backlight dimming on a night schedule, and a guarded power-off button.

*only `python3-feedparser` and `speedtest-cli` from apt.

- `server.py` — HTTP server (port 8080): serves the UI plus /news /stats /speed
  /todos /backlight /power /chat endpoints
- `index.html` — the dashboard UI (frosted-glass widgets over the wallpaper)
- `kiosk.sh` — launches Chromium in app mode; called by labwc autostart and a
  3 AM refresh cron
- `speedtest_log.py` — hourly speed test, logged to speedlog.jsonl (cron)
- `dashboard_tests.py` — 29-check Playwright e2e suite (run from another machine:
  set DASHBOARD_URL=http://<pi-ip>:8080)
