#!/usr/bin/env python3
"""Forensics summary of the Cowrie honeypot's JSON log.

Reads cowrie.json out of the container's var volume and reports: total
sessions, unique source IPs, top attackers, top username/password combos
tried, top commands run, and any files attackers tried to download.

  ./honeypot-report.py [--since HOURS] [--top N]
"""
import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone

VOLUME = "honeypot_cowrie-var"   # docker named volume holding cowrie's var/


def read_events():
    # the cowrie image is distroless (no shell), so read the log straight from
    # the docker volume on the host instead of `docker exec`. sudo: the volume
    # dir is root-owned. rotated files (cowrie.json.YYYY-MM-DD) are included.
    mp = subprocess.run(
        ["docker", "volume", "inspect", VOLUME, "-f", "{{.Mountpoint}}"],
        capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    if not mp:
        return
    r = subprocess.run(
        ["sudo", "sh", "-c", f"cat {mp}/log/cowrie/cowrie.json* 2>/dev/null"],
        capture_output=True, text=True, timeout=30,
    )
    for line in r.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except ValueError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=float, default=0, help="only last N hours (0=all)")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    cutoff = None
    if a.since:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=a.since)

    sessions, ips = set(), Counter()
    creds, cmds, downloads = Counter(), Counter(), Counter()
    login_ok = 0

    for e in read_events():
        ts = e.get("timestamp", "")
        if cutoff:
            try:
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
        ev = e.get("eventid", "")
        if e.get("session"):
            sessions.add(e["session"])
        if e.get("src_ip"):
            ips[e["src_ip"]] += 1
        if ev in ("cowrie.login.success", "cowrie.login.failed"):
            creds[f'{e.get("username","")} / {e.get("password","")}'] += 1
            if ev == "cowrie.login.success":
                login_ok += 1
        elif ev == "cowrie.command.input":
            cmds[e.get("input", "").strip()[:80]] += 1
        elif ev in ("cowrie.session.file_download", "cowrie.session.file_upload"):
            downloads[e.get("url") or e.get("filename") or "?"] += 1

    def block(title, counter):
        print(f"\n== {title} ==")
        if not counter:
            print("  (none)")
        for k, n in counter.most_common(a.top):
            print(f"  {n:5}  {k}")

    span = f"last {a.since}h" if a.since else "all time"
    print(f"Cowrie honeypot report ({span})")
    print(f"  sessions: {len(sessions)}   unique source IPs: {len(ips)}   "
          f"successful logins: {login_ok}")
    block("Top source IPs", ips)
    block("Top credentials tried (user / pass)", creds)
    block("Top commands run", cmds)
    block("Files fetched", downloads)


if __name__ == "__main__":
    main()
