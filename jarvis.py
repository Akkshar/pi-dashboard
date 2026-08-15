#!/usr/bin/env python3
"""Jarvis - the Pi's brain. Phase 0: intent router + Ollama fallback.

Pure stdlib, importable module. pibot routes free text here; the dashboard
widget and (later) voice can call the same ask().

    from jarvis import ask
    reply = ask("what's due this week")   # -> Reply(text=..., route="deadlines")

Fast paths hit the existing local services (dashboard :8080, studyhub :8100,
Open-Meteo) and answer from templates in well under 2s. Anything the router
doesn't recognise goes to Ollama with a live-context block injected into the
system prompt (no tool-calling: one round trip, works with any model).

CLI for testing over SSH:  python3 jarvis.py "how's the pi"
"""
import collections
import datetime
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- config ----
OLLAMA = "http://127.0.0.1:11434"
MODEL = "llama3.2:3b"       # swap: "gemma4:e2b-it-qat" (nicer prose, slower, 4.3GB)
KEEP_ALIVE = "30m"          # keep the model warm between questions
DASH = "http://127.0.0.1:8080"
STUDY = "http://127.0.0.1:8100"
LAT, LON = 12.97, 79.16     # Vellore
HTTP_TIMEOUT = 6            # local services + weather
OLLAMA_TIMEOUT = 120        # cold load + possible queue behind the digest job
HISTORY_MAX = 16            # messages kept per conversation (8 exchanges)
HISTORY_TTL = 1800          # seconds before an exchange stops being "recent"
MAX_TOKENS = 300            # bound LLM reply length (phone-sized answers)

# Claude escalation ("ask claude ...") - runs Claude Code headless on the Pi,
# authenticated with the user's subscription via a long-lived OAuth token.
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
CLAUDE_CFG = os.path.expanduser("~/.jarvis_claude")   # TOKEN=sk-ant-oat01-...
CLAUDE_WORKDIR = os.path.expanduser("~/.claude-scratch")  # empty on purpose
CLAUDE_TIMEOUT = 180

SYSTEM_PROMPT = (
    "You are Jarvis, the personal AI of a student in Vellore, India, running "
    "locally on his Raspberry Pi 5. Address him as \"sir\". Voice: dry, "
    "understated wit in the style of a British butler - at most one light "
    "remark, never at the expense of clarity. Be concise: replies are read on "
    "a phone, usually under 80 words. Plain text only - no markdown, no "
    "asterisks, no emoji. Use the CURRENT CONTEXT block for facts about his "
    "tasks, system and day; if the answer isn't there and you don't know it, "
    "say so plainly. Never invent deadlines, numbers or news."
)

Reply = collections.namedtuple("Reply", "text route")


class ToolDown(Exception):
    def __init__(self, name):
        super().__init__(name)
        self.name = name


TOOL_DOWN_MSG = {
    "dashboard": "The dashboard service isn't answering, sir. `sudo systemctl status dashboard` when you have a moment.",
    "studyhub": "StudyHub isn't answering, sir, so deadlines and timers are out of reach. The rest of me works.",
    "weather": ("I can't reach the weather service, sir - the internet appears to be "
                "out (past midnight it's usually the hostel curfew). "
                "Everything local still works."),
}


# ---- tiny http helpers ----
def _get(url, tool):
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            return json.load(r)
    except Exception:
        raise ToolDown(tool)


def _post(url, obj, tool):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError:
        raise            # 4xx is an API answer, not an outage - caller handles
    except Exception:
        raise ToolDown(tool)


# ---- formatting helpers ----
def _rel_date(iso):
    d = (datetime.date.fromisoformat(iso) - datetime.date.today()).days
    if d < 0:
        return f"OVERDUE {-d}d"
    if d == 0:
        return "TODAY"
    if d == 1:
        return "tomorrow"
    return f"in {d}d"


def _fmt_dur(secs):
    secs = int(secs)
    h, m = secs // 3600, (secs % 3600) // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _pick(*variants):
    return random.choice(variants)


# ---- intent handlers (signature: handler(text, match) -> str) ----
def h_greeting(text, m):
    t = text.lower()
    if "thank" in t or t.strip(" !.") in ("ty", "thanks"):
        return _pick("Naturally, sir.", "All part of the service, sir.",
                     "You're welcome, sir.")
    if "night" in t or t.strip(" !.") == "gn":
        return _pick("Good night, sir. I'll keep the lights on. Dimly.",
                     "Sleep well, sir.")
    if "morning" in t or t.strip(" !.") == "gm":
        return _pick("Good morning, sir. The Pi survived the night.",
                     "Morning, sir. All systems accounted for.")
    return _pick("At your service, sir.", "Sir.", "Present and operational, sir.",
                 "Listening, sir.")


def h_todo_add(text, m):
    item = (m.group(1) or "").strip(" .!,")
    if not item:
        return "Add what, precisely, sir?"
    todos = _post(DASH + "/todos", {"action": "add", "text": item},
                  "dashboard")["todos"]
    n = sum(1 for t in todos if not t.get("done"))
    return _pick(f"Noted, sir: '{item}'. The list stands at {n}.",
                 f"Added '{item}'. That's {n} outstanding, sir.",
                 f"'{item}' - on the list, sir. {n} items now await your attention.")


def h_deadlines(text, m):
    tasks = _get(STUDY + "/api/tasks", "studyhub")
    today = datetime.date.today()
    rows = []
    for t in tasks:
        if t.get("done"):
            continue
        days = (datetime.date.fromisoformat(t["due"]) - today).days
        nice = datetime.date.fromisoformat(t["due"]).strftime("%d %b")
        label = f"{t.get('course', '')} {t['title']}".strip()
        rows.append((days, f"{_rel_date(t['due'])}: {label} ({nice})"))
    if not rows:
        return _pick("Nothing due, sir. Suspicious, but I'll allow it.",
                     "The deadline board is clear, sir. Enjoy it while it lasts.")
    rows.sort(key=lambda x: x[0])
    lines = [r for _, r in rows[:8]]
    extra = len(rows) - 8
    if extra > 0:
        lines.append(f"...and {extra} more further out.")
    head = _pick("The docket, sir:", "As it stands, sir:", "Your obligations, sir:")
    return head + "\n" + "\n".join(lines)


def h_todo_list(text, m):
    todos = _get(DASH + "/todos", "dashboard")["todos"]
    open_ = [t for t in todos if not t.get("done")]
    done = len(todos) - len(open_)
    if not open_:
        return _pick("The list is empty, sir. A rare state of grace.",
                     "Nothing on the list, sir.")
    lines = [f"{i}. {t['text']}" for i, t in enumerate(open_, 1)]
    tail = f"\n({done} already done, sir.)" if done else ""
    return "On the list, sir:\n" + "\n".join(lines) + tail


def h_sys_status(text, m):
    s = _get(DASH + "/stats", "dashboard")
    temp = s.get("cpu_temp")
    remark = ""
    if isinstance(temp, (int, float)) and temp >= 70:
        remark = " Running warm, sir - I'd call it character-building."
    return (f"All systems nominal, sir. CPU {s.get('cpu_pct')}% at {temp} C, "
            f"fan {s.get('fan_rpm') or '?'} rpm, memory {s['mem']['pct']}%, "
            f"disk {s['disk']['pct']}% used.{remark}")


_WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
        45: "foggy", 48: "foggy", 51: "drizzling", 53: "drizzling",
        55: "drizzling", 61: "light rain", 63: "rain", 65: "heavy rain",
        80: "rain showers", 81: "rain showers", 82: "heavy showers",
        95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms"}


def h_weather(text, m):
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({
               "latitude": LAT, "longitude": LON,
               "current": "temperature_2m,apparent_temperature,weather_code",
               "daily": "temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max,weather_code",
               "timezone": "auto", "forecast_days": 2}))
    w = _get(url, "weather")
    cur, day = w["current"], w["daily"]
    sky = _WMO.get(cur["weather_code"], "unremarkable skies")
    line1 = (f"{round(cur['temperature_2m'])} C and {sky}, sir "
             f"(feels like {round(cur['apparent_temperature'])}).")
    line2 = (f"Today {round(day['temperature_2m_min'][0])}-"
             f"{round(day['temperature_2m_max'][0])} C, "
             f"{day['precipitation_probability_max'][0]}% chance of rain; "
             f"tomorrow {round(day['temperature_2m_min'][1])}-"
             f"{round(day['temperature_2m_max'][1])} C, "
             f"{day['precipitation_probability_max'][1]}%.")
    if (day["precipitation_probability_max"][0] or 0) >= 60:
        line2 += " I'd take the umbrella, sir."
    return line1 + "\n" + line2


def h_news(text, m):
    d = _get(DASH + "/digest", "dashboard")
    bullets = d.get("bullets") or []
    if not bullets:
        return "No briefing has been generated yet, sir."
    head = "The briefing, sir:"
    gen = d.get("generated")
    if gen:
        age = (time.time() - gen) / 3600
        if age > 6:
            head = f"Slightly aged news, sir ({int(age)}h old):"
    return head + "\n" + "\n".join("- " + b for b in bullets)


def h_timer_start(text, m):
    sub = re.search(r"\b(?:for|on)\s+([\w][\w &+-]{1,30}?)\s*$", text, re.I)
    subject = sub.group(1).strip() if sub else "General"
    _post(STUDY + "/api/timer/start", {"subject": subject}, "studyhub")
    note = "" if sub else " (No subject named, so it's logged as General.)"
    return f"Timer running for {subject}, sir. Do focus.{note}"


def h_timer_stop(text, m):
    status = _get(STUDY + "/api/timer", "studyhub").get("running")
    if not status:
        return "No timer is running, sir."
    try:
        _post(STUDY + "/api/timer/stop", {}, "studyhub")
    except urllib.error.HTTPError:
        return "No timer is running, sir."
    elapsed = status.get("elapsed", 0)
    if elapsed < 60:
        return "Under a minute, sir - not worth the ink. Discarded."
    return f"Logged {_fmt_dur(elapsed)} of {status.get('subject')}, sir. Well spent, I trust."


def h_study_stats(text, m):
    st = _get(STUDY + "/api/stats", "studyhub")
    today_iso = datetime.date.today().isoformat()
    today = st.get("days", {}).get(today_iso, 0)
    total = st.get("total", 0)
    if not total:
        return "No study logged in the past week, sir. I shall say no more."
    subs = sorted(st.get("subjects", {}).items(), key=lambda kv: -kv[1])[:3]
    top = ", ".join(f"{k} {_fmt_dur(v)}" for k, v in subs)
    return (f"Today {_fmt_dur(today)}, this week {_fmt_dur(total)}, sir. "
            f"Mostly: {top}.")


def h_timer_status(text, m):
    r = _get(STUDY + "/api/timer", "studyhub").get("running")
    if not r:
        return "No timer running, sir."
    return f"{r['subject']}, {_fmt_dur(r.get('elapsed', 0))} on the clock, sir."


def h_screen(text, m):
    t = text.lower()
    num = re.search(r"\b(\d{1,3})\s*%?", t)
    if num:
        pct = max(0, min(100, int(num.group(1))))
    elif "dim" in t or "dark" in t or "night" in t:
        pct = 15
    else:
        pct = 100
    got = _get(DASH + f"/backlight?set={pct}", "dashboard").get("pct")
    return f"Backlight to {got}%, sir."


def h_net(text, m):
    s = _get(DASH + "/net", "dashboard")
    name = "the iPhone hotspot" if s.get("net") == "hotspot" else (s.get("conn") or "an unknown network")
    up = s.get("online")
    verdict = "online" if up else "OFFLINE" if up is not None else "status unknown"
    return f"On {name}, sir - internet {verdict}."


def _claude_token():
    try:
        with open(CLAUDE_CFG) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == "TOKEN" and v:
                    return v
    except OSError:
        pass
    return None


def h_claude(text, m):
    q = m.group(1).strip()
    if not q:
        return "Ask Claude what, sir?"
    if not os.path.exists(CLAUDE_BIN):
        return ("Claude isn't installed on the Pi yet, sir - "
                "the escalation line is dead.")
    token = _claude_token()
    if not token:
        return ("No Claude credentials, sir. Run `claude setup-token` on the "
                "Pi and put TOKEN=<result> in ~/.jarvis_claude.")
    persona = ("You are Jarvis, relaying an answer to your user over Telegram. "
               "Address him as 'sir', dry understated wit at most once. Plain "
               "text only - no markdown, no emoji. Be concise but complete; "
               "this is a phone screen. You are the escalation path above a "
               "small local model, so give a genuinely good answer.\n\n"
               + build_context())
    env = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=token)
    os.makedirs(CLAUDE_WORKDIR, exist_ok=True)
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "-p", q, "--append-system-prompt", persona],
            cwd=CLAUDE_WORKDIR, env=env, capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ("Claude is taking his time, sir - I gave up after three "
                "minutes. The local reflexes still work.")
    except OSError as e:
        return f"Couldn't launch Claude, sir ({e.__class__.__name__})."
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        if "usage limit" in err.lower() or "rate limit" in err.lower():
            return "Claude says you've hit your usage limit, sir. Even genius has quotas."
        return f"Claude declined, sir: {err[-250:] or 'no error output'}"
    out = r.stdout.strip()
    return out or "Claude returned an empty answer, sir. Anticlimactic."


# ---- routing table: (name, [patterns], handler), checked in order ----
def _rx(*pats):
    return [re.compile(p, re.I) for p in pats]


INTENTS = [
    ("greeting", _rx(r"^(?:hi|hello|hey|yo|sup|hiya|thanks?|thank you|ty|"
                     r"good (?:morning|afternoon|evening|night)|gm|gn)\b[!. ]*$"),
     h_greeting),
    ("todo_add", _rx(r"\bremind me to (.+)$",
                     r"\badd (?:a )?(?:todo|reminder)\b[:\s]*(.*)$",
                     r"\badd (.+?) to (?:my |the )?(?:to-?do|reminder|shopping)s?(?: list)?\b"),
     h_todo_add),
    ("deadlines", _rx(r"\b(?:deadlines?|due|assignments?|exams?|submissions?|"
                      r"cat-?\d?|fat)\b"),
     h_deadlines),
    ("todo_list", _rx(r"\b(?:to-?dos?|reminders?)\b"), h_todo_list),
    ("timer_start", _rx(r"\b(?:start|begin)\b.*\b(?:timer|study(?:ing)?|study session)\b",
                        r"\b(?:start|begin)\b.*\btimer\b"),
     h_timer_start),
    ("timer_stop", _rx(r"\b(?:stop|end|finish|done with)\b.*\b(?:timer|studying)\b"),
     h_timer_stop),
    ("study_stats", _rx(r"\bhow (?:long|much)\b.*\b(?:stud|revis)\w*",
                        r"\bstudy (?:time|stats|hours|totals?)\b"),
     h_study_stats),
    ("timer_status", _rx(r"\btimer\b"), h_timer_status),
    ("sys_status", _rx(r"\bhow(?:'s| is| are) the pi\b",
                       r"\b(?:cpu|ram|memory|disk|fan|uptime)\b",
                       r"\b(?:system|pi) (?:status|stats|health|temp\w*)\b"),
     h_sys_status),
    ("weather", _rx(r"\b(?:weather|forecast|rain\w*|umbrella|sunny|humid\w*|"
                    r"temperature)\b",
                    r"\b(?:hot|cold)\b.*\b(?:outside|today|out there)\b"),
     h_weather),
    ("news", _rx(r"\b(?:news|headlines?|digest)\b", r"\bbrief me\b"), h_news),
    ("screen", _rx(r"\b(?:dim|brighten|brightness|backlight)\b",
                   r"\bscreen\b.*\b\d{1,3}\b"),
     h_screen),
    ("net", _rx(r"\b(?:wi-?fi|internet|network|connection|hotspot)\b",
                r"\b(?:are we|is the pi) online\b", r"\bonline\b"),
     h_net),
]


# Openers that mean "compose/explain something" - even if the sentence
# mentions exams or the weather, it's an LLM job, not a data lookup.
_GENERATIVE = re.compile(
    r"^(?:write|compose|draft|make up|tell me a|explain|why|who|translate|"
    r"summari[sz]e|generate|imagine|describe)\b", re.I)

# Explicit escalation to Claude - checked before everything else so
# "ask claude why ..." never falls into the generative guard or a fast path.
_CLAUDE = re.compile(r"^(?:ask|hey)?\s*claude[,:]?\s+(.+)$", re.I | re.S)


def route(text):
    """Pure pattern match, no I/O: (name, handler, match) or None."""
    t = text.strip()
    m = _CLAUDE.match(t)
    if m:
        return "claude", h_claude, m
    if _GENERATIVE.search(t):
        return None
    for name, pats, handler in INTENTS:
        if name == "greeting" and len(t) > 25:
            continue
        for p in pats:
            m = p.search(t)
            if m:
                return name, handler, m
    return None


# ---- conversation memory ----
_history = {}                # chat -> deque[(ts, role, content)]
_lock = threading.Lock()


def _remember(chat, user_text, reply_text):
    with _lock:
        h = _history.setdefault(chat, collections.deque(maxlen=HISTORY_MAX))
        now = time.time()
        h.append((now, "user", user_text))
        h.append((now, "assistant", reply_text))


def _recent(chat):
    with _lock:
        h = _history.get(chat, ())
        cutoff = time.time() - HISTORY_TTL
        return [{"role": r, "content": c} for ts, r, c in h if ts > cutoff]


def reset_history(chat=None):
    with _lock:
        if chat is None:
            _history.clear()
        else:
            _history.pop(chat, None)


# ---- LLM fallback ----
def build_context():
    lines = ["CURRENT CONTEXT (auto-gathered, trust these facts):",
             "Now: " + datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M") + " IST"]
    try:
        tasks = [t for t in _get(STUDY + "/api/tasks", "studyhub")
                 if not t.get("done")]
        tasks.sort(key=lambda t: t["due"])
        if tasks:
            lines.append("Deadlines: " + "; ".join(
                f"{t.get('course', '')} {t['title']} {_rel_date(t['due'])} ({t['due']})".strip()
                for t in tasks[:5]))
    except Exception:
        pass
    try:
        todos = [t["text"] for t in _get(DASH + "/todos", "dashboard")["todos"]
                 if not t.get("done")]
        if todos:
            lines.append(f"Todos open ({len(todos)}): " + "; ".join(todos[:6]))
    except Exception:
        pass
    try:
        s = _get(DASH + "/stats", "dashboard")
        lines.append(f"Pi: CPU {s.get('cpu_temp')} C at {s.get('cpu_pct')}%, "
                     f"mem {s['mem']['pct']}%")
    except Exception:
        pass
    return "\n".join(lines)


def _ollama_chat(messages):
    payload = json.dumps({"model": MODEL, "messages": messages, "stream": False,
                          "keep_alive": KEEP_ALIVE,
                          "options": {"num_predict": MAX_TOKENS}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as r:
        return json.load(r)["message"]["content"].strip()


def llm_fallback(text, chat):
    system = SYSTEM_PROMPT + "\n\n" + build_context()
    messages = ([{"role": "system", "content": system}]
                + _recent(chat)
                + [{"role": "user", "content": text}])
    out = _ollama_chat(messages)
    _remember(chat, text, out)
    return out


# ---- public entry point ----
def ask(text, notify=None, chat="main"):
    """Answer free text. Never raises; returns Reply(text, route)."""
    t = (text or "").strip()
    if not t:
        return Reply("Sir?", "empty")
    hit = route(t)
    if hit:
        name, handler, m = hit
        try:
            out = handler(t, m)
        except ToolDown as e:
            return Reply(TOOL_DOWN_MSG.get(
                e.name, f"The {e.name} service isn't answering, sir."),
                name + "_error")
        except Exception as e:
            return Reply(f"My {name} reflex misfired, sir "
                         f"({e.__class__.__name__}). Do try rephrasing.",
                         name + "_error")
        _remember(chat, t, out)
        return Reply(out, name)
    if notify:
        try:
            notify("llm")
        except Exception:
            pass
    try:
        return Reply(llm_fallback(t, chat), "llm")
    except Exception as e:
        if isinstance(e, (TimeoutError, urllib.error.URLError)) and "timed out" in str(e).lower():
            msg = ("My cognitive matrix is otherwise occupied, sir - likely "
                   "chewing on the news digest. The reflexes (status, todos, "
                   "deadlines, weather) remain at your disposal.")
        else:
            msg = ("My cognitive matrix is offline, sir - Ollama isn't "
                   "answering. The reflexes still work: status, todos, "
                   "deadlines, weather.")
        return Reply(msg, "llm_error")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('usage: jarvis.py "your question"')
        raise SystemExit(1)
    t0 = time.time()
    r = ask(" ".join(sys.argv[1:]))
    print(f"[{r.route}] ({time.time() - t0:.1f}s)")
    print(r.text)
