#!/usr/bin/env python3
"""Dashboard server: /news, /stats, /speed, /backlight, /chat."""
import glob
import json
import os
import subprocess
import time
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import feedparser

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
PORT = 8080
DIM_MIN = 1

MODEL = "llama3.2:3b"
OLLAMA = "http://127.0.0.1:11434"
SYSTEM_PROMPT = ("You are a helpful assistant running locally on a Raspberry Pi 5. "
                 "Keep answers short and to the point - a few sentences unless asked for more.")

SPEEDLOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speedlog.jsonl")
TODOFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "todos.json")
DIGESTFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digest.json")

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
_cache = {"data": None, "ts": 0.0}
_bl = {"dir": None}


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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def _send_json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/news":
            now = time.time()
            ttl = CACHE_TTL if _cache["data"] else 45
            if _cache["data"] is None or now - _cache["ts"] > ttl:
                _cache["data"] = fetch_headlines()
                _cache["ts"] = now
            self._send_json({"headlines": _cache["data"]})
        elif path == "/stats":
            self._send_json({"cpu_temp": cpu_temp(), "cpu_pct": cpu_percent(),
                             "fan_rpm": fan_rpm(),
                             "mem": mem_info(), "disk": disk_info()})
        elif path == "/speed":
            self._send_json({"history": speed_history()})
        elif path == "/todos":
            self._send_json({"todos": load_todos()})
        elif path == "/digest":
            try:
                with open(DIGESTFILE) as f:
                    self._send_json(json.load(f))
            except Exception:
                self._send_json({"generated": None, "bullets": []})
        elif path == "/power":
            # kiosk-button only: hostel LAN has no client isolation, so never
            # let another device on the network reach the shutdown endpoint
            if self.client_address[0] != "127.0.0.1":
                self.send_error(403)
                return
            q = parse_qs(urlparse(self.path).query)
            if q.get("do", [""])[0] == "off":
                self._send_json({"ok": True})
                subprocess.Popen(["sudo", "/usr/sbin/shutdown", "-h", "+0"])
            else:
                self._send_json({"ok": False})
        elif path == "/backlight":
            q = parse_qs(urlparse(self.path).query)
            pct = set_brightness(q["set"][0]) if "set" in q else get_brightness()
            self._send_json({"pct": pct})
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/todos":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                data = {}
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
            self._send_json({"todos": todos})
            return
        if path != "/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length else {}
            messages = data.get("messages", [])[-12:]
        except Exception:
            messages = []
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
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
                        self.wfile.write(tok.encode("utf-8"))
                        self.wfile.flush()
                    if chunk.get("done"):
                        break
        except BrokenPipeError:
            pass
        except Exception:
            try:
                self.wfile.write("Sorry - couldn't reach the local AI. Is Ollama running?".encode("utf-8"))
            except Exception:
                pass

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Dashboard serving on http://0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
