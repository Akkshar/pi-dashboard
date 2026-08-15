#!/usr/bin/env python3
"""Telegram bot for the Pi - control the dashboard from your phone, anywhere.

Pure stdlib, long-polling (works behind the hostel portal: outbound HTTPS only).
Config: ~/.pibot_token  (chmod 600), two lines:
    TOKEN=123456:ABC-your-botfather-token
    CHAT_ID=123456789        <- add after first contact; bot ignores everyone else
Commands: /status /brief /todos /todo <text> /dim /bright /forget /help
Anything that isn't a command goes to jarvis.ask() - the local AI brain.
"""
import datetime
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import jarvis
import voicebox

CFG_FILE = os.path.expanduser("~/.pibot_token")
DASH = "http://127.0.0.1:8080"
STUDY = "http://127.0.0.1:8100"
DIGEST = os.path.expanduser("~/dashboard/digest.json")

HELP = (
    "Pi at your service. Just type normally to talk to Jarvis,\n"
    "send a voice note and he'll talk back, or say\n"
    "'ask claude <question>' to escalate to the big brain.\n"
    "Commands:\n"
    "/status - CPU, temp, RAM, disk\n"
    "/brief - latest AI news brief\n"
    "/todos - list reminders\n"
    "/deadlines - upcoming StudyHub deadlines\n"
    "/deadline <YYYY-MM-DD> [DA|Lab|CAT|FAT] <title> - add a deadline\n"
    "/todo <text> - add a reminder\n"
    "/portal pause [min] | resume - let me log into the portal directly\n"
    "/net [hotspot|rvit] - show or switch the Pi's WiFi network\n"
    "/wake - wake the laptop (WoL)\n"
    "/dim /bright - screen backlight\n"
    "/forget - wipe Jarvis's short-term memory"
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


def send_long(chat, text):
    """sendMessage, split under Telegram's 4096-char limit (prefer newlines)."""
    text = text or "..."
    while text:
        if len(text) <= 3900:
            chunk, text = text, ""
        else:
            cut = text.rfind("\n", 0, 3900)
            if cut < 500:
                cut = 3900
            chunk, text = text[:cut], text[cut:].lstrip("\n")
        tg("sendMessage", chat_id=chat, text=chunk)


def _typing_ticker(chat, done):
    # Telegram's "typing..." indicator expires after ~5s; refresh until done
    while True:
        try:
            tg("sendChatAction", chat_id=chat, action="typing")
        except Exception:
            pass
        if done.wait(4.5):
            break


def tg_send_voice(chat, path):
    """sendVoice needs multipart/form-data; stdlib-only implementation."""
    with open(path, "rb") as f:
        audio = f.read()
    boundary = f"----pibot{int(time.time() * 1000)}"
    b = boundary.encode()
    body = b"".join([
        b"--" + b + b"\r\n",
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        str(chat).encode() + b"\r\n",
        b"--" + b + b"\r\n",
        b'Content-Disposition: form-data; name="voice"; filename="reply.ogg"\r\n',
        b"Content-Type: audio/ogg\r\n\r\n",
        audio + b"\r\n",
        b"--" + b + b"--\r\n",
    ])
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendVoice", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


VOICE_MAX_SECONDS = 120
SPEAK_MAX_CHARS = 900


def handle_voice(voice):
    """Telegram voice object -> (text reply, ogg reply path or None)."""
    if voice.get("duration", 0) > VOICE_MAX_SECONDS:
        return ("Over two minutes of audio, sir - brevity is a virtue. "
                "Do try a shorter one."), None
    if not voicebox.stt_available():
        return ("I heard you, sir, but my ears (whisper.cpp) aren't "
                "installed yet."), None
    info = tg("getFile", file_id=voice["file_id"])
    url = f"https://api.telegram.org/file/bot{TOKEN}/{info['result']['file_path']}"
    audio = "/tmp/pibot_voice_in.oga"
    with urllib.request.urlopen(url, timeout=60) as r, open(audio, "wb") as f:
        f.write(r.read())
    try:
        heard = voicebox.transcribe(audio)
    except voicebox.VoiceError as e:
        return f"Transcription failed, sir: {e}", None
    finally:
        try:
            os.remove(audio)
        except OSError:
            pass
    if not heard or not any(c.isalnum() for c in heard):
        return "I couldn't make out a word of that, sir.", None
    try:
        reply = handle(heard)
    except Exception as e:
        reply = f"Error: {e}"
    text_reply = f'\U0001F3A4 "{heard}"\n\n{reply}'
    ogg = None
    if voicebox.tts_available():
        spoken = reply
        if len(spoken) > SPEAK_MAX_CHARS:
            spoken = (spoken[:SPEAK_MAX_CHARS].rsplit(" ", 1)[0]
                      + " ... the rest is in the text, sir.")
        try:
            ogg = voicebox.speak(spoken, "/tmp/pibot_reply.ogg")
        except voicebox.VoiceError as e:
            print(f"tts failed: {e}", flush=True)
    return text_reply, ogg


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


def cmd_net(rest):
    arg = rest.strip().lower()
    if arg in ("hotspot", "rvit"):
        dash(f"/net?do={arg}")
        if arg == "hotspot":
            return ("Switching to the iPhone hotspot - keep the phone's Personal "
                    "Hotspot screen open. Give it ~20s, then /net to check. "
                    "(Uses phone data; I return to R-VIT after curfew.)")
        return "Switching back to hostel WiFi - the watchdog will re-auth the portal."
    s = dash("/net")
    name = "iPhone hotspot" if s.get("net") == "hotspot" else s.get("conn", "?")
    return f"On {name}, internet {'OK' if s.get('online') else 'DOWN'}."


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
    if t.startswith("/net"):
        return cmd_net(t[4:].strip())
    if t.startswith("/wake"):
        return cmd_wake()
    if t.startswith("/dim"):
        dash("/backlight?set=15")
        return "Screen dimmed."
    if t.startswith("/bright"):
        dash("/backlight?set=100")
        return "Screen brightened."
    if t.startswith("/forget"):
        jarvis.reset_history()
        return "Memory wiped, sir. Who are you again?"
    if t.startswith("/help") or t.startswith("/start") or t.startswith("/"):
        return HELP  # unknown slash commands get help, not the LLM
    # free text -> Jarvis; keep the typing indicator alive while it thinks
    done = threading.Event()
    threading.Thread(target=_typing_ticker, args=(CHAT_ID, done),
                     daemon=True).start()
    try:
        return jarvis.ask(t).text
    finally:
        done.set()


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
                voice = msg.get("voice")
                if not chat or not (text or voice):
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
                if voice:
                    done = threading.Event()
                    threading.Thread(target=_typing_ticker, args=(chat, done),
                                     daemon=True).start()
                    try:
                        reply, ogg = handle_voice(voice)
                    except Exception as e:
                        reply, ogg = f"Error: {e}", None
                    finally:
                        done.set()
                    send_long(chat, reply)
                    if ogg:
                        try:
                            tg_send_voice(chat, ogg)
                        except Exception as e:
                            print(f"sendVoice failed: {e}", flush=True)
                    continue
                try:
                    reply = handle(text)
                except Exception as e:
                    reply = f"Error: {e}"
                send_long(chat, reply)
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
