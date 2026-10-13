"""The week ahead: which scheduled slots exist, and what each one is planned to be.

Your waiting inbox items go first, then the mix from config.json, with no mode repeated three times
running. It is a plan, not a commitment: the scheduler still asks you before anything posts.
"""
from __future__ import annotations

import random
from datetime import timedelta

from . import scheduler


def week(cfg, inbox_waiting: int, recent: list[str], start, rng=random) -> list[dict]:
    weights = dict(cfg["mix"])
    slots = []
    d = start
    for i in range(8):
        day = d + timedelta(days=i)
        if day.isoweekday() in scheduler._days(cfg):
            slots.append(scheduler.slot_for(day, cfg))
    planned: list[dict] = []
    history = list(recent)
    pending_inbox = inbox_waiting
    for slot in slots[:7]:
        if pending_inbox > 0:
            mode = "input"
            pending_inbox -= 1
        else:
            pool = {k: w for k, w in weights.items() if w > 0}
            if len(history) >= 2 and history[-1] == history[-2] and history[-1] in pool and len(pool) > 1:
                pool.pop(history[-1])
            total = sum(pool.values()) or 1
            r = rng.random() * total
            mode = next(iter(pool), "thought")
            for k, w in pool.items():
                r -= w
                if r <= 0:
                    mode = k
                    break
        history.append(mode)
        planned.append({"when": slot, "mode": mode})
    return planned


def render(planned: list[dict], tz_label: str) -> str:
    if not planned:
        return "No posting days are set in config.json."
    return "\n".join(f"  {p['when'].strftime('%a %d %b, %H:%M')} {tz_label}  {p['mode']}" for p in planned)


ENGAGE_KINDS = ["question", "opinion", "lesson", "story", "achievement", "news", "product", "vulnerable", "hiring"]


def engagement_list(targets: list[str], limit: int = 10) -> list[dict]:
    """Up to ten people you chose to engage with this week. Cadence does not look anyone up: you paste
    the names or profile links, and each one gets a suggested comment type to aim for on their latest post."""
    picked = [t.strip() for t in targets if t and t.strip()][:limit]
    return [{"n": i + 1, "who": who, "aim": ENGAGE_KINDS[i % len(ENGAGE_KINDS)]} for i, who in enumerate(picked)]


def render_engagement(items: list[dict]) -> str:
    if not items:
        return "Engagement: paste up to 10 names or profile links, one per line."
    lines = ["ENGAGE THIS WEEK (you comment yourself; Cadence never does)"]
    lines += [f"  {it['n']:>2}. {it['who']}  ->  comment on their latest post, type: {it['aim']}" for it in items]
    return "\n".join(lines)
