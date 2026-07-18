"""End-to-end tests for the Raspberry Pi dashboard, run from Windows against the Pi.

Covers: API endpoints, live widgets, popovers, reminders CRUD via UI,
dim toggle (reversible), power-button confirm/auto-cancel (never confirms!),
plus geometry checks: no overlaps, everything on screen.
"""
import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Point this at the dashboard, e.g. set DASHBOARD_URL=http://<pi-ip>:8080
BASE = os.environ.get("DASHBOARD_URL", "http://192.168.1.7:8080")
results = []


def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    print(("PASS" if ok else "FAIL") + " - " + name + ((" [" + extra + "]") if extra else ""))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read())


# ---------- API smoke tests ----------
try:
    st = get("/stats")
    check("API /stats returns data", st.get("cpu_temp") is not None and st["mem"]["pct"] is not None,
          f"temp={st.get('cpu_temp')}C fan={st.get('fan_rpm')}rpm mem={st['mem']['pct']}%")
    check("API fan RPM present", isinstance(st.get("fan_rpm"), int), str(st.get("fan_rpm")))
except Exception as e:
    check("API /stats returns data", False, str(e))

try:
    news = get("/news")["headlines"]
    check("API /news responds", True, f"{len(news)} headlines (0 = Pi has no internet window right now)")
except Exception as e:
    check("API /news responds", False, str(e))

try:
    sp = get("/speed")["history"]
    latest = sp[-1] if sp else {}
    check("API /speed has history", len(sp) >= 1,
          f"{len(sp)} tests, latest down={latest.get('down')} up={latest.get('up')} Mbps ping={latest.get('ping')}ms")
except Exception as e:
    check("API /speed has history", False, str(e))

try:
    td = get("/todos")
    check("API /todos works", "todos" in td, f"{len(td['todos'])} items")
except Exception as e:
    check("API /todos works", False, str(e))

try:
    dg = get("/digest")
    check("API /digest works", "bullets" in dg, f"{len(dg.get('bullets') or [])} bullets")
except Exception as e:
    check("API /digest works", False, str(e))

try:
    bl = get("/backlight")
    check("API /backlight works", bl.get("pct") is not None, f"{bl.get('pct')}%")
except Exception as e:
    check("API /backlight works", False, str(e))

# ---------- UI tests ----------
with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 800, "height": 440})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE, wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(1000)

    t1 = page.text_content("#clock")
    page.wait_for_timeout(1600)
    t2 = page.text_content("#clock")
    check("clock is ticking", t1 != t2 and ":" in (t1 or ""), f"{(t2 or '').strip()}")

    w = (page.text_content("#weatherNow") or "").strip()
    check("weather loaded", "°" in w, w)

    page.click("#weatherCard")
    page.wait_for_timeout(400)
    fb = page.locator("#forecast").bounding_box()
    check("forecast popover opens", fb is not None)
    if fb:
        check("forecast stays on screen", fb["x"] >= 0 and fb["x"] + fb["width"] <= 800,
              f"spans x {fb['x']:.0f}-{fb['x']+fb['width']:.0f}")
        top = page.evaluate(
            "(b)=>{const e=document.elementFromPoint(b.x+b.width/2, b.y+b.height/2);"
            " return e ? (e.closest('#forecast') ? 'forecast' : (e.id || e.className || e.tagName)) : 'none';}", fb)
        check("forecast renders on top (not under reminders)", top == "forecast", str(top))
    page.click("#weatherCard")
    page.wait_for_timeout(200)

    n = page.locator("#briefList li").count()
    check("daily brief rendered", 1 <= n <= 8, f"{n} bullets")
    bt = (page.text_content("#briefTime") or "").strip()
    check("brief timestamp shown", bt.startswith("·"), bt)

    cpu = (page.text_content("#cpuPct") or "").strip()
    check("CPU stat shown", cpu.endswith("%"), cpu)
    fan = (page.text_content("#fanRpm") or "").strip()
    check("fan RPM shown in widget", fan.isdigit(), fan + " rpm")
    net = (page.text_content("#netNow") or "").strip()
    check("net speeds shown", "↓" in net, net)

    page.click("#netRow")
    page.wait_for_timeout(400)
    sb = page.locator("#speedPop").bounding_box()
    nb = page.locator("#newsCard").bounding_box()
    check("speed chart popover opens", sb is not None)
    if sb and nb:
        check("speed chart clear of news card", sb["x"] + sb["width"] <= nb["x"] + 1,
              f"chart ends {sb['x']+sb['width']:.0f}, news starts {nb['x']:.0f}")
    if sb:
        top2 = page.evaluate(
            "(b)=>{const e=document.elementFromPoint(b.x+b.width/2, b.y+b.height/2);"
            " return e ? (e.closest('#speedPop') ? 'speedPop' : (e.id || e.className || e.tagName)) : 'none';}", sb)
        check("speed chart renders on top (not under reminders)", top2 == "speedPop", str(top2))
    page.click("#netRow")
    page.wait_for_timeout(200)

    marker = f"pw-test-{int(time.time())}"
    page.fill("#remInput", marker)
    page.click("#remAdd")
    page.wait_for_timeout(800)
    row = page.locator("#remList li", has_text=marker)
    check("reminder added via UI", row.count() == 1)
    if row.count():
        row.locator(".del").click()
        page.wait_for_timeout(800)
        check("reminder deleted via UI", page.locator("#remList li", has_text=marker).count() == 0)

    bl0 = get("/backlight").get("pct") or 0
    l0 = (page.text_content("#dimLabel") or "").strip()
    page.click("#dimBtn")
    page.wait_for_timeout(900)
    l1 = (page.text_content("#dimLabel") or "").strip()
    check("dim button toggles", l0 != l1, f"{l0} -> {l1}")
    page.click("#dimBtn")
    page.wait_for_timeout(900)
    bl2 = get("/backlight").get("pct") or 0
    check("backlight restored to initial", abs(bl2 - bl0) <= 5, f"{bl0}% -> {bl2}%")

    page.click("#powerBtn")
    page.wait_for_timeout(300)
    check("power button asks confirmation", "Sure" in (page.text_content("#pwLabel") or ""))
    page.wait_for_timeout(4600)
    check("power auto-cancels without shutdown", (page.text_content("#pwLabel") or "").strip() == "Off")

    ids = ["#clockWidget", "#weatherCard", "#sysCard", "#remCard", "#newsCard", "#powerBtn", "#dimBtn"]
    boxes = {i: page.locator(i).bounding_box() for i in ids}

    def olap(a, b):
        return (a and b and a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
                and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])

    bad = [f"{ids[i]} x {ids[j]}" for i in range(len(ids)) for j in range(i + 1, len(ids))
           if olap(boxes[ids[i]], boxes[ids[j]])]
    check("no widgets overlap", not bad, "; ".join(bad))
    out = [k for k, b in boxes.items()
           if b and (b["x"] < 0 or b["y"] < 0 or b["x"] + b["width"] > 800 or b["y"] + b["height"] > 440)]
    check("everything fits on screen", not out, ", ".join(out))
    check("no JS console errors", not errors, " | ".join(errors[:3]))
    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} PASSED =====")
sys.exit(1 if fails else 0)
