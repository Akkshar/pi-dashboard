#!/usr/bin/env python3
"""Run a speed test and append the result to speedlog.jsonl (keeps ~3 weeks hourly)."""
import json, os, subprocess, time

LOG = os.path.expanduser("~/dashboard/speedlog.jsonl")
MAX_LINES = 500

try:
    out = subprocess.run(["speedtest-cli", "--json"], capture_output=True, text=True, timeout=240).stdout
    d = json.loads(out)
    rec = {"ts": int(time.time()),
           "down": round(d["download"] / 1e6, 1),
           "up": round(d["upload"] / 1e6, 1),
           "ping": round(d["ping"])}
    lines = []
    if os.path.exists(LOG):
        with open(LOG) as f:
            lines = f.read().splitlines()
    lines.append(json.dumps(rec))
    with open(LOG, "w") as f:
        f.write("\n".join(lines[-MAX_LINES:]) + "\n")
except Exception:
    pass
