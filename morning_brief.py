#!/usr/bin/env python3
"""Morning Telegram brief: upcoming deadlines + weather + news digest.

Cron: */15 7-9 * * *  - fires every 15 min in the morning window, sends once
per day (first run where the Pi is online), so power cuts / offline spells
just delay it instead of killing it. --force ignores the once-a-day guard.
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
STATE = HOME + "/dashboard/.morning_brief_date"
LAT, LON = 12.97, 79.16  # Vellore

today = datetime.date.today().isoformat()
force = "--force" in sys.argv
if not force and os.path.exists(STATE) and open(STATE).read().strip() == today:
    sys.exit(0)

conf = dict(l.strip().split("=", 1) for l in open(HOME + "/.pibot_token") if "=" in l)

try:
    urllib.request.urlopen(
        "http://connectivitycheck.gstatic.com/generate_204", timeout=6)
except Exception:
    sys.exit(0)  # offline - next cron run will retry


def get(url):
    with urllib.request.urlopen(url, timeout=12) as r:
        return json.load(r)


parts = []

try:
    up = []
    for t in get("http://127.0.0.1:8100/api/tasks"):
        if t["done"]:
            continue
        d = (datetime.date.fromisoformat(t["due"]) - datetime.date.today()).days
        if d < 0:
            label = f"OVERDUE {-d}d"
        elif d == 0:
            label = "TODAY"
        elif d == 1:
            label = "tomorrow"
        elif d <= 7:
            label = f"in {d}d"
        else:
            continue
        course = f" {t['course']}" if t["course"] else ""
        up.append((d, f"- {label}:{course} {t['title']}"))
    up.sort(key=lambda x: x[0])
    parts.append("Deadlines:\n" + "\n".join(x[1] for x in up)
                 if up else "Deadlines: nothing due this week.")
except Exception:
    parts.append("Deadlines: StudyHub unreachable.")

try:
    d = get(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&timezone=auto&forecast_days=1")["daily"]
    parts.append(f"Weather: {round(d['temperature_2m_min'][0])}-"
                 f"{round(d['temperature_2m_max'][0])} C, "
                 f"rain {d['precipitation_probability_max'][0]}%")
except Exception:
    parts.append("Weather: unavailable.")

try:
    dg = json.load(open(HOME + "/dashboard/digest.json"))
    parts.append("News:\n" + "\n".join("- " + b for b in dg["bullets"][:5]))
except Exception:
    pass

msg = "Good morning!\n\n" + "\n\n".join(parts)
data = urllib.parse.urlencode({"chat_id": conf["CHAT_ID"], "text": msg}).encode()
urllib.request.urlopen(urllib.request.Request(
    f"https://api.telegram.org/bot{conf['TOKEN']}/sendMessage", data=data), timeout=15)
open(STATE, "w").write(today)
print(f"{time.strftime('%F %T')} brief sent")
