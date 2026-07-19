#!/usr/bin/env python3
"""Reconstruct power-off periods on a Pi with no RTC.

Run at boot (@reboot cron): waits until NTP has fixed the clock, then computes
the true boot moment (now - uptime) and pairs it with the last heartbeat
written before power died. Appends {"off": ..., "on": ...} to powerlog.jsonl.
"""
import json
import os
import subprocess
import time

DIR = os.path.dirname(os.path.abspath(__file__))
HEART = os.path.join(DIR, "heartbeat")
LOG = os.path.join(DIR, "powerlog.jsonl")
MAX_WAIT = 6 * 3600  # give up if NTP never syncs this boot (no internet window)
MIN_GAP = 90         # ignore sub-90s blips (reboots, not power cuts)


def synced():
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out == "yes"
    except Exception:
        return False


def main():
    waited = 0
    while not synced() and waited < MAX_WAIT:
        time.sleep(30)
        waited += 30
    if not synced():
        return  # clock never became trustworthy; skip logging this boot
    with open("/proc/uptime") as f:
        up = float(f.read().split()[0])
    boot = time.time() - up
    last = None
    try:
        with open(HEART) as f:
            last = int(f.read().strip())
    except Exception:
        pass
    if last and boot > last and boot - last > MIN_GAP:
        with open(LOG, "a") as f:
            f.write(json.dumps({"off": last, "on": int(boot)}) + "\n")
    with open(HEART, "w") as f:
        f.write(str(int(time.time())))


if __name__ == "__main__":
    main()
