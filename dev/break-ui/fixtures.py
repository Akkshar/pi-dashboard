"""Reminders fixtures for break-ui. Same shape as ~/dashboard/todos.json:
a list of {"id": int(ms), "text": str, "done": bool}."""

BASE = 1763900000000  # ms; ids are add-times in server.py


def _rows(items):
    return [{"id": BASE + i * 60000, "text": t, "done": d} for i, (t, d) in enumerate(items)]


# What the card was built against (phrases from test_jarvis.py).
DEMO = _rows([
    ("Buy milk", False),
    ("Email the professor", False),
    ("Refill printer paper", True),
    ("Laundry", False),
])

# The server keeps only text[:200] (server.py:247), so the long paste is cut
# exactly where the real server would cut it, mid-word.
_PASTE = ("Submit DA2 for CSE3004 on VTOP before 11:59 PM Friday - Reg.No_Name_DA2.pdf "
          "only (not .docx), include the signed declaration page, and email Prof. S. "
          "Venkataraman a copy because the portal timed out last time")[:200]

WORST = _rows([
    # Telegram /todo with a pasted link: one unbreakable token
    ("https://example.test/vtop/academics/assignments/CSE3004-ELA-FALL2026-DA2?tab=upload&semester=FALL2026", False),
    # The shortest real reminder
    ("Gym", False),
    # Tamil script (tall glyphs, needs a Tamil font on the Pi)
    ("அம்மாவுக்கு போன் பண்ணு", False),
    # Exactly the server limit, cut mid-word
    (_PASTE, False),
    # Web-lab notes typed as-is: must render literally
    ("Revise <template> & <slot> + &amp; entities for web lab", False),
    # Telegram /todo sent as a multi-line message (newlines survive .strip())
    ("Buy:\n- detergent\n- maggi\n- AAA batteries", False),
    # Jarvis voice add: lowercase, no punctuation, long, already done
    ("email the professor about the lab attendance shortage before friday", True),
    # Camera file name: long unbreakable token mid-sentence
    ("Print IMG_20261118_183022_HDR_lab-record-pages_edited_edited.HEIC at the xerox shop", False),
    # ZWJ emoji sequence + repeated emoji
    ("🧑🏽‍💻 fix jev-gate hook before demo 🔥🔥", False),
    # Identical text twice, one done
    ("Buy milk", True),
    ("Buy milk", False),
])

EMPTY = []

ONE = _rows([("Buy milk", False)])

# The server cap: save_todos keeps todos[-100:] (server.py:142). Done items are
# never auto-cleared, so they pile up. Oldest item is a long-lived open reminder;
# the newest 10 are open and sit at the bottom.
_done_pool = ["Buy milk", "Laundry", "Recharge phone", "Email the professor", "Return library book",
              "Pay mess fee", "Refill printer paper", "Lab record signature", "Call home", "Gym"]
HUNDRED = _rows(
    [("Renew passport (expires Mar 2027)", False)]
    + [(_done_pool[i % len(_done_pool)], True) for i in range(89)]
    + [(f"Open item {i + 1}: " + _done_pool[i % len(_done_pool)].lower(), False) for i in range(10)]
)

# Every one of the 100 slots open: an add must be refused, never drop one.
FULL = _rows([(f"Open reminder {i + 1}", False) for i in range(100)])

FIXTURES = {"demo": DEMO, "worst": WORST, "empty": EMPTY, "one": ONE, "100": HUNDRED, "full": FULL}
