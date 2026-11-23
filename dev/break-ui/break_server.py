"""Dev-only harness for break-ui on the Pi dashboard's Reminders card.
Never deployed as a service; stdlib only, so it runs on the laptop without Flask.

Serves the repo's index.html (only the external weather URL is pointed at a
local stub), swaps the /todos fixture at the data boundary, and stubs the
other endpoints so the page renders as it does on the Pi. The /todos cap logic
(trim_todos, MAX_TODOS, TODO_TEXT_MAX) is lifted from server.py's source, so the
mock can't drift from it.

  python -I dev/break-ui/break_server.py . [port]      (from the repo root)
  open http://127.0.0.1:<port>/__break
"""
import ast
import copy
import json
import os
import sys
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import FIXTURES  # noqa: E402

ROOT = os.path.abspath(sys.argv[1])
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8791
STATE = {k: copy.deepcopy(v) for k, v in FIXTURES.items()}


def _server_todo_logic():
    """Compile just the /todos cap pieces of server.py (no Flask import needed)."""
    tree = ast.parse(open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name == "trim_todos")
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("MAX_TODOS", "TODO_TEXT_MAX")
                                                  for t in n.targets))]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "server.py", "exec"), ns)
    return ns["trim_todos"], ns["MAX_TODOS"], ns["TODO_TEXT_MAX"]


trim_todos, MAX_TODOS, TODO_TEXT_MAX = _server_todo_logic()

STATES = [("demo", "Demo data"), ("worst", "Worst case"), ("empty", "Empty"), ("one", "One"), ("100", "100 rows"), ("full", "100 open")]
SIZES = {"kiosk": (800, 450), "pc": (1280, 720)}

STUBS = {
    "/stats": {"cpu_pct": 7, "cpu_temp": 52, "fan_rpm": 0,
               "mem": {"pct": 31, "used": 2.4, "total": 7.9}, "disk": {"pct": 44, "used": 25, "total": 57}},
    "/digest": {"generated": time.time() - 3600, "bullets": [
        "Placeholder brief line one for layout.", "Placeholder brief line two for layout.",
        "Placeholder brief line three for layout."]},
    "/speed": {"history": [{"down": 42.1, "up": 18.3, "ping": 23, "ts": time.time() - 3600 * i} for i in range(6, 0, -1)]},
    "/powerlog": {"outages": []},
    "/net": {"net": "rvit", "online": True},
    "/backlight": {"pct": 100},
    "/__weather": {"current": {"temperature_2m": 27.4, "weather_code": 2},
                   "daily": {"time": ["2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"],
                             "weather_code": [2, 61, 3, 1],
                             "temperature_2m_max": [30, 28, 29, 31], "temperature_2m_min": [22, 21, 22, 23]}},
}


def harness(q):
    data = q.get("data", ["worst"])[0]
    size = q.get("size", ["kiosk"])[0]
    if data not in FIXTURES:
        data = "worst"
    if size not in SIZES:
        size = "kiosk"
    w, h = SIZES[size]

    def seg(items, key, cur, other):
        return "".join(
            f'<a class="s{" on" if k == cur else ""}" href="/__break?{key}={k}&{other}">{label}</a>'
            for k, label in items)
    data_seg = seg(STATES, "data", data, f"size={size}")
    size_seg = seg([(k, f"{k.upper() if k == 'pc' else 'Kiosk'} {v[0]}×{v[1]}") for k, v in SIZES.items()],
                   "size", size, f"data={data}")
    return f"""<!doctype html><meta charset="utf-8"><title>break-ui · Reminders</title>
<style>
 body{{margin:0;background:#e9e9ec;font:13px system-ui,sans-serif;color:#222;display:flex;flex-direction:column;align-items:center;gap:12px;padding:16px 0 80px}}
 iframe{{border:1px solid #999;background:#000}}
 .bar{{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);display:flex;gap:10px;align-items:center}}
 .track{{display:flex;background:#d0d0d5;border-radius:999px;padding:3px}}
 .s{{padding:5px 12px;border-radius:999px;color:#333;text-decoration:none;white-space:nowrap}}
 .s.on{{background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.2)}}
 small a{{color:#555}}
</style>
<small>Dev-only break-ui harness · fixture swapped at <code>/todos</code> ·
<a href="/__reset?back=data%3D{data}%26size%3D{size}">reset fixture edits</a></small>
<iframe src="/index.html?data={data}" width="{w}" height="{h}"></iframe>
<div class="bar"><div class="track">{data_seg}</div><div class="track">{size_seg}</div></div>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("json") else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fixture_key(self):
        ref = self.headers.get("Referer", "")
        k = parse_qs(urlparse(ref).query).get("data", ["demo"])[0]
        return k if k in STATE else "demo"

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/__break":
            return self._send(200, harness(q), "text/html")
        if u.path == "/__reset":
            for k, v in FIXTURES.items():
                STATE[k] = copy.deepcopy(v)
            self.send_response(302)
            self.send_header("Location", "/__break?" + q.get("back", ["data=worst"])[0])
            self.end_headers()
            return
        if u.path == "/todos":
            return self._send(200, {"todos": STATE[self._fixture_key()]})
        if u.path in STUBS:
            return self._send(200, STUBS[u.path])
        if u.path in ("/", "/index.html"):
            html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
            html = html.replace("https://api.open-meteo.com/v1/forecast", "/__weather")
            return self._send(200, html, "text/html")
        if u.path == "/wallpaper.jpg":
            return self._send(200, open(os.path.join(ROOT, "wallpaper.jpg"), "rb").read(), "image/jpeg")
        return self._send(404, {"error": "not stubbed"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if u.path != "/todos":
            return self._send(200, {})
        key = self._fixture_key()
        todos = STATE[key]
        # Same control flow as server.py todos_post.
        act = data.get("action")
        out = {}
        if act == "add" and data.get("text", "").strip():
            if sum(1 for t in todos if not t.get("done")) >= MAX_TODOS:
                return self._send(200, {"todos": todos, "error": f"list full ({MAX_TODOS} open)"})
            item = {"id": int(time.time() * 1000), "text": data["text"].strip()[:TODO_TEXT_MAX], "done": False}
            todos.append(item)
            out["added"] = item
        elif act == "toggle":
            for t in todos:
                if t["id"] == data.get("id"):
                    t["done"] = not t["done"]
        elif act == "delete":
            todos = [t for t in todos if t["id"] != data.get("id")]
        todos = trim_todos(todos)
        STATE[key] = todos
        return self._send(200, {"todos": todos, **out})


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}/__break", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
