#!/usr/bin/env python3
"""Generate a short AI news briefing from the dashboard's RSS feeds.

Run by cron at 6:00; writes digest.json, served by the dashboard at /digest.
Reuses FEEDS / fetch_headlines / MODEL / OLLAMA from server.py.
"""
import json
import os
import sys
import time
import urllib.request

from server import MODEL, OLLAMA, fetch_headlines

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digest.json")
BULLETS = 8

PROMPT = (
    "You are the news editor for a personal daily brief in India. The reader gets ALL their news "
    "from this brief, so cover everything that matters. Below are the raw headlines from several feeds. "
    "Write exactly {n} bullet points covering the main distinct stories of the day: lead with the "
    "biggest story, cover India, world, tech and business, plain factual language, one sentence of "
    "at most 18 words per bullet. Ignore clickbait, celebrity gossip and duplicate stories.\n\n"
    'Reply as JSON: {{"bullets": ["...", "..."]}}\n\nHEADLINES:\n{headlines}'
)


def main():
    headlines = fetch_headlines()
    if len(headlines) < 5:
        print("not enough headlines (offline?) - keeping old digest")
        return 1
    listing = "\n".join(f"- {h['title']} ({h['source']})" for h in headlines)
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT.format(n=BULLETS, headlines=listing)}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.3},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        reply = json.load(resp)["message"]["content"]
    try:
        bullets = [b.strip() for b in json.loads(reply)["bullets"] if isinstance(b, str) and b.strip()]
    except Exception:
        bullets = [l.strip("-*• ").strip() for l in reply.splitlines() if l.strip()]
    bullets = bullets[:BULLETS]
    if not bullets:
        print("model returned nothing usable - keeping old digest")
        return 1
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"generated": int(time.time()), "bullets": bullets}, f)
    os.replace(tmp, OUT)
    print(f"digest written: {len(bullets)} bullets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
