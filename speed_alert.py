#!/usr/bin/env python3
"""Telegram ping when the hostel link is unusually fast (good download window).

Cron: 12 * * * *  - runs 5 min after the hourly speedtest. Alerts when the
latest measurement beats THRESH, at most once per COOLDOWN, never at night.
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
LOG = HOME + "/dashboard/speedlog.jsonl"
STATE = HOME + "/dashboard/.speed_alert_ts"
THRESH = 12.0        # Mbit down; history p75 is ~9.8, so this is a genuinely good window
COOLDOWN = 6 * 3600  # seconds between alerts

now = time.time()
hour = datetime.datetime.now().hour
if hour < 8 or hour >= 23:
    sys.exit(0)

try:
    last = json.loads(open(LOG).read().strip().splitlines()[-1])
except Exception:
    sys.exit(0)
if now - last["ts"] > 1200 or last["down"] < THRESH:
    sys.exit(0)
try:
    if now - float(open(STATE).read().strip()) < COOLDOWN:
        sys.exit(0)
except Exception:
    pass

conf = dict(l.strip().split("=", 1) for l in open(HOME + "/.pibot_token") if "=" in l)
msg = (f"Fast net window: {last['down']:.1f} down / {last['up']:.1f} up Mbit, "
       f"ping {last['ping']}ms - good time for big downloads.")
data = urllib.parse.urlencode({"chat_id": conf["CHAT_ID"], "text": msg}).encode()
urllib.request.urlopen(urllib.request.Request(
    f"https://api.telegram.org/bot{conf['TOKEN']}/sendMessage", data=data), timeout=15)
open(STATE, "w").write(str(now))
