#!/usr/bin/env python3
"""Routing tests for jarvis.py. Default mode is pure pattern matching -
no network, safe to run anywhere. `--live` runs a small set of real ask()
calls against the local services (run on the Pi only).

    python3 test_jarvis.py          # offline routing suite
    python3 test_jarvis.py --live   # + real end-to-end calls with timings
"""
import sys
import time

import jarvis

# (utterance, expected route) - None means "should fall through to the LLM"
CASES = [
    # deadlines
    ("what's due this week", "deadlines"),
    ("when is the OS exam", "deadlines"),
    ("any upcoming deadlines?", "deadlines"),
    ("do I have any submissions", "deadlines"),
    # todos: add vs list
    ("add buy milk to my todos", "todo_add"),
    ("remind me to email the professor", "todo_add"),
    ("add a todo: refill printer paper", "todo_add"),
    ("show my todos", "todo_list"),
    ("what's on my reminders", "todo_list"),
    # system vs weather collision
    ("how's the pi doing", "sys_status"),
    ("cpu temp?", "sys_status"),
    ("how much ram is free", "sys_status"),
    ("what's the temperature outside", "weather"),
    ("will it rain today", "weather"),
    ("do I need an umbrella", "weather"),
    ("is it hot outside", "weather"),
    # news
    ("any news?", "news"),
    ("brief me", "news"),
    # study timer
    ("start a timer for physics", "timer_start"),
    ("stop the timer", "timer_stop"),
    ("how long did I study today", "study_stats"),
    ("is the timer running", "timer_status"),
    # screen / net
    ("dim the screen", "screen"),
    ("set brightness to 40", "screen"),
    ("are we online?", "net"),
    ("is the wifi ok", "net"),
    # greetings
    ("hey", "greeting"),
    ("good morning", "greeting"),
    ("thanks", "greeting"),
    # must fall through to the LLM
    ("write me a haiku about exams", None),
    ("what should I focus on today", None),
    ("what about tomorrow?", None),
    ("explain why the sky is blue", None),
]


def offline():
    fails = 0
    for text, want in CASES:
        hit = jarvis.route(text)
        got = hit[0] if hit else None
        ok = got == want
        fails += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {text!r:45} -> {got} "
              f"{'' if ok else f'(wanted {want})'}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    return fails


def live():
    subset = ["how's the pi doing", "what's due this week", "show my todos",
              "will it rain today", "what should I focus on today"]
    for text in subset:
        t0 = time.time()
        r = jarvis.ask(text)
        print(f"\n--- {text!r} [{r.route}] {time.time() - t0:.1f}s")
        print(r.text)


if __name__ == "__main__":
    rc = offline()
    if "--live" in sys.argv:
        live()
    sys.exit(1 if rc else 0)
