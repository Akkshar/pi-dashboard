#!/usr/bin/env python3
"""Dashboard server, Flask edition: /news, /stats, /speed, /todos, /digest,
/backlight, /power, /chat + static files.

Same endpoints and module exports as the original http.server version, so
digest.py's `from server import ...` and all clients keep working unchanged.
"""
import glob
import json
import logging
import os
import subprocess
import time
import urllib.request

import feedparser
from flask import Flask, Response, abort, jsonify, request, send_from_directory

# ===== Your news sources: (Display name, RSS feed URL) =====
FEEDS = [
    ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    ("Al Jazeera",     "https://www.aljazeera.com/xml/rss/all.xml"),
    ("TechCrunch",     "https://techcrunch.com/feed/"),
    ("CNBC",           "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("Google News",    "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"),
]
ITEMS_PER_FEED = 6
CACHE_TTL = 300
PORT = int(os.environ.get("PORT", 8080))
DIM_MIN = 1

MODEL = "llama3.2:3b"
OLLAMA = "http://127.0.0.1:11434"
SYSTEM_PROMPT = ("You are a helpful assistant running locally on a Raspberry Pi 5. "
                 "Keep answers short and to the point - a few sentences unless asked for more.")

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
SPEEDLOG = os.path.join(DIRECTORY, "speedlog.jsonl")
TODOFILE = os.path.join(DIRECTORY, "todos.json")
DIGESTFILE = os.path.join(DIRECTORY, "digest.json")

_cache = {"data": None, "ts": 0.0}
_bl = {"dir": None}

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # keep journald quiet


def fetch_headlines():
    out = []
    for name, url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:ITEMS_PER_FEED]:
                title = entry.get("title", "").strip()
                if title:
                    out.append({"title": title, "source": name, "link": entry.get("link", "")})
        except Exception:
            continue
    return out


def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def fan_rpm():
    try:
        for p in glob.glob("/sys/class/hwmon/hwmon*/fan1_input"):
            return int(open(p).read().strip())
    except Exception:
        pass
    return None


def cpu_percent():
    def snap():
        with open("/proc/stat") as f:
            vals = list(map(int, f.readline().split()[1:]))
        return vals[3] + vals[4], sum(vals)
    try:
        i1, t1 = snap(); time.sleep(0.15); i2, t2 = snap()
        dt = t2 - t1
        return round(100 * (1 - (i2 - i1) / dt), 1) if dt > 0 else None
    except Exception:
        return None


def mem_info():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.split()[0])
        total = info["MemTotal"]
        used = total - info.get("MemAvailable", info.get("MemFree", 0))
        return {"used": round(used/1048576, 2), "total": round(total/1048576, 2), "pct": round(100*used/total, 1)}
    except Exception:
        return {"used": None, "total": None, "pct": None}


def disk_info():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        used = total - st.f_bavail * st.f_frsize
        return {"used": round(used/1e9, 1), "total": round(total/1e9, 1), "pct": round(100*used/total, 1)}
    except Exception:
        return {"used": None, "total": None, "pct": None}


def speed_history(n=200):
    out = []
    try:
        with open(SPEEDLOG) as f:
            for line in f.read().splitlines()[-n:]:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def load_todos():
    try:
        with open(TODOFILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_todos(todos):
    try:
        with open(TODOFILE, "w") as f:
            json.dump(todos[-100:], f)
    except Exception:
        pass


def bl_dir():
    if _bl["dir"] is None:
        try:
            _bl["dir"] = "/sys/class/backlight/" + sorted(os.listdir("/sys/class/backlight/"))[0]
        except Exception:
            _bl["dir"] = ""
    return _bl["dir"] or None


def get_brightness():
    d = bl_dir()
    if not d:
        return None
    try:
        cur = int(open(d + "/brightness").read())
        mx = int(open(d + "/max_brightness").read())
        return round(100 * cur / mx)
    except Exception:
        return None


def set_brightness(pct):
    d = bl_dir()
    if not d:
        return None
    try:
        pct = max(0, min(100, int(pct)))
        mx = int(open(d + "/max_brightness").read())
        val = max(DIM_MIN, round(mx * pct / 100))
        with open(d + "/brightness", "w") as f:
            f.write(str(val))
        return round(100 * val / mx)
    except Exception:
        return None


@app.after_request
def no_store(resp):
    if resp.mimetype == "application/json":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/news")
def news():
    now = time.time()
    ttl = CACHE_TTL if _cache["data"] else 45
    if _cache["data"] is None or now - _cache["ts"] > ttl:
        _cache["data"] = fetch_headlines()
        _cache["ts"] = now
    return jsonify({"headlines": _cache["data"]})


@app.get("/stats")
def stats():
    return jsonify({"cpu_temp": cpu_temp(), "cpu_pct": cpu_percent(),
                    "fan_rpm": fan_rpm(), "mem": mem_info(), "disk": disk_info()})


@app.get("/speed")
def speed():
    return jsonify({"history": speed_history()})


@app.get("/powerlog")
def powerlog():
    out = []
    try:
        with open(os.path.join(DIRECTORY, "powerlog.jsonl")) as f:
            for line in f.read().splitlines()[-60:]:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return jsonify({"outages": out})


@app.get("/digest")
def digest():
    try:
        with open(DIGESTFILE) as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"generated": None, "bullets": []})


@app.get("/todos")
def todos_get():
    return jsonify({"todos": load_todos()})


@app.post("/todos")
def todos_post():
    data = request.get_json(silent=True) or {}
    todos = load_todos()
    act = data.get("action")
    if act == "add" and data.get("text", "").strip():
        todos.append({"id": int(time.time() * 1000),
                      "text": data["text"].strip()[:200], "done": False})
    elif act == "toggle":
        for t in todos:
            if t["id"] == data.get("id"):
                t["done"] = not t["done"]
    elif act == "delete":
        todos = [t for t in todos if t["id"] != data.get("id")]
    save_todos(todos)
    return jsonify({"todos": todos})


@app.get("/backlight")
def backlight():
    pct = set_brightness(request.args["set"]) if "set" in request.args else get_brightness()
    return jsonify({"pct": pct})


@app.get("/power")
def power():
    # kiosk-button only: hostel LAN has no client isolation, so never
    # let another device on the network reach the shutdown endpoint
    if request.remote_addr != "127.0.0.1":
        abort(403)
    if request.args.get("do") == "off":
        resp = jsonify({"ok": True})
        subprocess.Popen(["sudo", "/usr/sbin/shutdown", "-h", "+0"])
        return resp
    return jsonify({"ok": False})


@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])[-12:]

    def stream():
        try:
            payload = json.dumps({
                "model": MODEL,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                "stream": True,
            }).encode("utf-8")
            req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                for line in resp:
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    tok = chunk.get("message", {}).get("content", "")
                    if tok:
                        yield tok
                    if chunk.get("done"):
                        break
        except Exception:
            yield "Sorry - couldn't reach the local AI. Is Ollama running?"

    return Response(stream(), mimetype="text/plain")


@app.get("/")
def index():
    return send_from_directory(DIRECTORY, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(DIRECTORY, filename)


if __name__ == "__main__":
    print(f"Dashboard (Flask) serving on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
