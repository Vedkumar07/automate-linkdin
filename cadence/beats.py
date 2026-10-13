"""Times a short talking-head video script before you record it (for LinkedIn video posts).

Estimates speaking time at about 165 words per minute, stacks the lines into timecodes, and flags
the things that make a short video lose people: a slow hook, a long beat, a stretch with nothing
concrete, and no loop back to the start.
"""
from __future__ import annotations

import re

WPM = 165.0
CONCRETE = re.compile(r"\d|\$|₹|%|\b[A-Z][a-z]+\b")


def timeline(script: str, target: float = 30.0) -> tuple[list[dict], list[str]]:
    lines = [l.strip() for l in script.splitlines() if l.strip()]
    beats, t = [], 0.0
    for i, line in enumerate(lines):
        words = len(line.split())
        dur = round(words / WPM * 60, 1)
        role = "HOOK" if i == 0 else ("CTA" if i == len(lines) - 1 else "")
        beats.append({"at": t, "dur": dur, "text": line, "role": role})
        t = round(t + dur, 1)
    notes: list[str] = []
    if beats:
        if beats[0]["dur"] > 3.0:
            notes.append(f"Hook takes {beats[0]['dur']}s. Viewers decide in about 3s, so shorten the first line.")
        for b in beats[1:]:
            if b["dur"] > 4.0:
                notes.append(f"Beat at {fmt(b['at'])} runs {b['dur']}s. Split it or change what is on screen.")
        empty_run = 0
        for b in beats:
            empty_run = 0 if CONCRETE.search(b["text"]) else empty_run + 1
            if empty_run >= 3:
                notes.append(f"Three lines in a row with nothing concrete, around {fmt(b['at'])}. Add a number, name or example.")
                break
        first_words = set(re.findall(r"\w+", beats[0]["text"].lower()))
        last_words = set(re.findall(r"\w+", beats[-1]["text"].lower()))
        if len(beats) > 1 and not (first_words & last_words) - {"the", "a", "an", "and", "to", "of", "i", "it", "is", "that"}:
            notes.append("No loop: the last line does not echo the first. Looping replays are free reach.")
    total = t
    if total and abs(total - target) > 3:
        notes.append(f"{total:.1f}s against a {target:.0f}s target. Short is usually right; trim rather than pad.")
    return beats, notes


def fmt(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:04.1f}"


def render(script: str, target: float = 30.0) -> str:
    beats, notes = timeline(script, target)
    if not beats:
        return "Nothing to time."
    total = beats[-1]["at"] + beats[-1]["dur"]
    rows = [f"BEAT SHEET  ·  {sum(len(b['text'].split()) for b in beats)} words  ·  ~{total:.1f}s at {WPM:.0f} wpm  ·  target {target:.0f}s",
            "=" * 72]
    for b in beats:
        rows.append(f"  {fmt(b['at']):>6} {b['dur']:>5.1f}s  {b['role']:<5} {b['text']}")
    rows.append("-" * 72)
    rows += [f"  - {n}" for n in notes] or ["  No problems found."]
    return "\n".join(rows)
