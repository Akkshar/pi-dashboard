#!/usr/bin/env python3
"""Telegram bot for the Pi - control the dashboard from your phone, anywhere.

Pure stdlib, long-polling (works behind the hostel portal: outbound HTTPS only).
Config: ~/.pibot_token  (chmod 600), two lines:
    TOKEN=123456:ABC-your-botfather-token
    CHAT_ID=123456789        <- add after first contact; bot ignores everyone else
Commands: /status /brief /todos /todo <text> /dim /bright /help
"""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

CFG_FILE = os.path.expanduser("~/.pibot_token")
DASH = "http://127.0.0.1:8080"
DIGEST = os.path.expanduser("~/dashboard/digest.json")

HELP = (
    "Pi at your service. Commands:\n"
    "/status - CPU, temp, RAM, disk\n"
    "/brief - latest AI news brief\n"
    "/todos - list reminders\n"
    "/todo <text> - add a reminder\n"
    "/dim /bright - screen backlight"
)


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


def handle(text):
    t = text.strip()
    if t.startswith("/status"):
        return cmd_status()
    if t.startswith("/brief"):
        return cmd_brief()
    if t.startswith("/todos"):
        return cmd_todos()
    if t.startswith("/todo "):
        added = t[6:].strip()
        if added:
            dash_post("/todos", {"action": "add", "text": added})
            return f"Added: {added}"
        return "Usage: /todo buy detergent"
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
        except Exception as e:
            print(f"network hiccup: {e}", flush=True)
            time.sleep(10)


conf = cfg()
TOKEN = conf.get("TOKEN", "")
CHAT_ID = conf.get("CHAT_ID", "")
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("No TOKEN in ~/.pibot_token - create the bot with @BotFather first")
    main()
