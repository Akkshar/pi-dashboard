#!/usr/bin/env python3
"""Telegram bot for the Pi - control the dashboard from your phone, anywhere.

Pure stdlib, long-polling (works behind the hostel portal: outbound HTTPS only).
Config: ~/.pibot_token  (chmod 600), two lines:
    TOKEN=123456:ABC-your-botfather-token
    CHAT_ID=123456789        <- add after first contact; bot ignores everyone else
Commands: /status /brief /todos /todo <text> /dim /bright /help
"""
import datetime
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

CFG_FILE = os.path.expanduser("~/.pibot_token")
DASH = "http://127.0.0.1:8080"
STUDY = "http://127.0.0.1:8100"
DIGEST = os.path.expanduser("~/dashboard/digest.json")

HELP = (
    "Pi at your service. Commands:\n"
    "/status - CPU, temp, RAM, disk\n"
    "/brief - latest AI news brief\n"
    "/todos - list reminders\n"
    "/deadlines - upcoming StudyHub deadlines\n"
    "/deadline <YYYY-MM-DD> [DA|Lab|CAT|FAT] <title> - add a deadline\n"
    "/todo <text> - add a reminder\n"
    "/portal pause [min] | resume - let me log into the portal directly\n"
    "/wake - wake the laptop (WoL)\n"
    "/dim /bright - screen backlight"
)

PAUSE_FILE = os.path.expanduser("~/.portal_pause")
LAPTOP_MACS = ["58:11:22:DD:6A:91", "C4:BD:E5:BB:1E:1C"]  # ethernet, wifi


def cfg():
    conf = {}
    with open(CFG_FILE) as f:
        for line in f:
            k, _, v = line.strip().partition("=")
            if k and v:
                conf[k] = v
    return conf


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.load(r)


def dash(path):
    with urllib.request.urlopen(DASH + path, timeout=15) as r:
        return json.load(r)


def dash_post(path, obj):
    req = urllib.request.Request(DASH + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def cmd_status():
    s = dash("/stats")
    up = subprocess.run(["uptime", "-p"], capture_output=True, text=True).stdout.strip()
    return (f"CPU {s['cpu_pct']}% at {s['cpu_temp']} C, fan {s.get('fan_rpm', '?')} rpm\n"
            f"RAM {s['mem']['used']}/{s['mem']['total']} GB ({s['mem']['pct']}%)\n"
            f"Disk {s['disk']['used']}/{s['disk']['total']} GB ({s['disk']['pct']}%)\n"
            f"{up}")


def cmd_brief():
    try:
        with open(DIGEST) as f:
            d = json.load(f)
        when = time.strftime("%a %H:%M", time.localtime(d["generated"]))
        return f"Brief from {when}:\n" + "\n".join("- " + b for b in d["bullets"])
    except Exception:
        return "No brief generated yet."


def cmd_todos():
    ts = dash("/todos")["todos"]
    if not ts:
        return "No reminders."
    return "\n".join(("[done] " if t["done"] else "[ ] ") + t["text"] for t in ts)


def cmd_deadlines():
    with urllib.request.urlopen(STUDY + "/api/tasks", timeout=15) as r:
        ts = json.load(r)
    today = datetime.date.today()
    rows = []
    for t in ts:
        if t["done"]:
            continue
        d = (datetime.date.fromisoformat(t["due"]) - today).days
        if d < 0:
            when = f"{-d}d OVERDUE"
        elif d == 0:
            when = "TODAY"
        elif d == 1:
            when = "tomorrow"
        else:
            when = f"in {d}d"
        due = datetime.date.fromisoformat(t["due"]).strftime("%d %b")
        rows.append((d, f"{when}: {t['course']} {t['title']} ({due})"))
    if not rows:
        return "No open deadlines. Free!"
    rows.sort(key=lambda x: x[0])
    return "\n".join(r for _, r in rows[:20])


def cmd_deadline_add(rest):
    parts = rest.split()
    if len(parts) < 2:
        return "Usage: /deadline 2026-08-12 [DA|Lab|CAT|FAT] OS record submission"
    try:
        datetime.date.fromisoformat(parts[0])
    except ValueError:
        return "First word must be the due date, YYYY-MM-DD."
    due, parts = parts[0], parts[1:]
    typ = "DA"
    if parts and parts[0] in ("DA", "Lab", "CAT", "FAT", "Other"):
        typ, parts = parts[0], parts[1:]
    if not parts:
        return "Give the deadline a title."
    title = " ".join(parts)
    req = urllib.request.Request(
        STUDY + "/api/tasks",
        data=json.dumps({"title": title, "type": typ, "due": due}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15):
        pass
    return f"Added: {title} ({typ}) due {due}"


def cmd_portal(rest):
    parts = rest.split()
    if parts and parts[0] == "pause":
        mins = 30
        if len(parts) > 1 and parts[1].isdigit():
            mins = min(int(parts[1]), 480)
        with open(PAUSE_FILE, "w") as f:
            f.write(str(int(time.time()) + mins * 60))
        return (f"Portal watchdog paused for {mins} min - you can log into the "
                "portal directly now. It resumes on its own.")
    if parts and parts[0] == "resume":
        try:
            os.remove(PAUSE_FILE)
        except FileNotFoundError:
            pass
        return "Portal watchdog resumed."
    return "Usage: /portal pause [minutes] or /portal resume"


def cmd_wake():
    import socket
    # 172.16.99.255 = wired link subnet broadcast (goes out eth0, the path that
    # actually reaches the sleeping laptop); global broadcast covers wifi too
    for target in ("172.16.99.255", "255.255.255.255"):
        for mac in LAPTOP_MACS:
            pkt = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                s.sendto(pkt, (target, 9))
            except OSError:
                pass
            s.close()
    return "Magic packets sent over the wire + wifi."


def handle(text):
    t = text.strip()
    if t.startswith("/status"):
        return cmd_status()
    if t.startswith("/brief"):
        return cmd_brief()
    if t.startswith("/todos"):
        return cmd_todos()
    if t.startswith("/deadlines"):
        return cmd_deadlines()
    if t.startswith("/todo "):
        added = t[6:].strip()
        if added:
            dash_post("/todos", {"action": "add", "text": added})
            return f"Added: {added}"
        return "Usage: /todo buy detergent"
    if t.startswith("/deadline "):
        return cmd_deadline_add(t[10:].strip())
    if t.startswith("/portal"):
        return cmd_portal(t[7:].strip())
    if t.startswith("/wake"):
        return cmd_wake()
    if t.startswith("/dim"):
        dash("/backlight?set=15")
        return "Screen dimmed."
    if t.startswith("/bright"):
        dash("/backlight?set=100")
        return "Screen brightened."
    return HELP


def main():
    offset = 0
    print("pibot running", flush=True)
    while True:
        try:
            updates = tg("getUpdates", offset=offset, timeout=50)["result"]
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not chat or not text:
                    continue
                if not CHAT_ID:
                    tg("sendMessage", chat_id=chat, text=(
                        f"Hello! Your chat id is {chat}.\n"
                        f"Add the line CHAT_ID={chat} to ~/.pibot_token on the Pi "
                        "and restart pibot (sudo systemctl restart pibot). "
                        "Until then I answer no commands."))
                    print(f"first contact from chat {chat}", flush=True)
                    continue
                if chat != CHAT_ID:
                    continue  # not you: silently ignore strangers
                try:
                    reply = handle(text)
                except Exception as e:
                    reply = f"Error: {e}"
                tg("sendMessage", chat_id=chat, text=reply)
        except urllib.error.HTTPError as e:
            if e.code in (401, 404):
                print(f"token rejected by Telegram (HTTP {e.code}) - check ~/.pibot_token; retrying in 5 min", flush=True)
                time.sleep(300)
            else:
                print(f"network hiccup: {e}", flush=True)
                time.sleep(10)
        except Exception as e:
            print(f"network hiccup: {e}", flush=True)
            time.sleep(10)


conf = cfg()
TOKEN = conf.get("TOKEN", "")
CHAT_ID = conf.get("CHAT_ID", "")
if __name__ == "__main__":
    if not TOKEN or ":" not in TOKEN:
        raise SystemExit("No real TOKEN in ~/.pibot_token (expected 123456:ABC... from @BotFather)")
    main()
