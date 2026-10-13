"""Post-mortem and swipe-file ranking from numbers YOU paste or export.

Cadence never scrapes LinkedIn or logs in as anyone. For your own posts, export or copy the numbers
from LinkedIn analytics. For other people's posts (the 'viral' tool), copy the numbers you can see
yourself into a CSV. Columns (header names are case-insensitive):

  text (or post)          required
  impressions (or views)  optional, used as reach when present
  reactions, comments, reposts   optional
  account                 optional, groups rows so each post is compared with its own account's median
  date                    optional

Ranking uses the multiple over each account's own median, not raw counts: 400,000 views is a quiet
Tuesday for a huge account and a breakout for a small one.
"""
from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict

from . import hooks

NUM = ("impressions", "views", "reactions", "comments", "reposts")


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    rows = []
    for raw in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        body = r.get("text") or r.get("post") or ""
        if not body:
            continue
        row = {"text": body, "account": r.get("account") or "me", "date": r.get("date", "")}
        for k in NUM:
            try:
                row[k] = int(float(r.get(k) or 0))
            except ValueError:
                row[k] = 0
        row["reach"] = row["impressions"] or row["views"]
        rows.append(row)
    return rows


def ranked(rows: list[dict]) -> list[dict]:
    by_account: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_account[r["account"]].append(r["reach"] or r["reactions"])
    medians = {a: (statistics.median(v) or 1) for a, v in by_account.items()}
    out = []
    for r in rows:
        value = r["reach"] or r["reactions"]
        mult = value / medians[r["account"]]
        out.append({**r,
                    "multiple": round(mult, 2),
                    "reposts_per_1k": round(r["reposts"] * 1000 / r["reach"], 2) if r["reach"] else 0.0,
                    "engagement_rate": round(100 * (r["reactions"] + r["comments"] + r["reposts"]) / r["reach"], 2) if r["reach"] else 0.0,
                    "formula": hooks.classify(r["text"]),
                    "hook": hooks.first_line(r["text"])})
    return sorted(out, key=lambda d: -d["multiple"])


def what_separates(items: list[dict]) -> list[str]:
    """Compares the top third with the bottom third on simple, checkable features."""
    if len(items) < 6:
        return ["Need at least 6 posts to compare top and bottom thirds."]
    k = max(1, len(items) // 3)
    top, bottom = items[:k], items[-k:]

    def feats(group: list[dict]) -> dict:
        n = len(group)
        return {
            "words": statistics.mean(len(g["text"].split()) for g in group),
            "questions": 100 * sum("?" in g["text"] for g in group) / n,
            "numbers": 100 * sum(any(c.isdigit() for c in g["text"]) for g in group) / n,
            "first person": 100 * sum(bool(hooks.FIRST.search(hooks.first_line(g["text"]))) for g in group) / n,
            "hook length": statistics.mean(len(hooks.first_line(g["text"])) for g in group),
        }
    t, b = feats(top), feats(bottom)
    lines = [f"Top third vs bottom third ({k} posts each):"]
    stop = stop_doing(t, b)
    labels = {"words": "words per post", "questions": "% with a question", "numbers": "% with a number",
              "first person": "% opening in the first person", "hook length": "characters in the opening line"}
    for key, label in labels.items():
        lines.append(f"  {label:<34} top {t[key]:>6.1f}   bottom {b[key]:>6.1f}")
    lines.append("")
    lines.append("Stop doing (common in your bottom third, rare in your top third):")
    lines += [f"  - {s}" for s in stop] if stop else ["  - nothing stands out yet"]
    return lines


def stop_doing(t: dict, b: dict) -> list[str]:
    """Habits that show up far more in the weakest posts than in the strongest ones."""
    out = []
    if b["questions"] - t["questions"] >= 20:
        out.append(f"ending with a question ({b['questions']:.0f}% of weak posts vs {t['questions']:.0f}% of strong)")
    if b["first person"] - t["first person"] >= 20:
        out.append("opening the first line with 'I' or 'we'")
    if b["words"] - t["words"] >= 40:
        out.append(f"writing long posts ({b['words']:.0f} words vs {t['words']:.0f})")
    if b["hook length"] - t["hook length"] >= 25:
        out.append(f"long opening lines ({b['hook length']:.0f} characters vs {t['hook length']:.0f})")
    if t["numbers"] - b["numbers"] >= 20:
        out.append("leaving out numbers (strong posts have them, weak posts do not)")
    return out


def report(rows: list[dict], title: str) -> str:
    items = ranked(rows)
    out = [f"{title}  ·  {len(items)} posts", "=" * 72]
    for it in items[:10]:
        out.append(f"  {it['multiple']:>6.2f}x  {it['formula'] or 'no formula':<18} {it['account']:<10} "
                   f"reach {it['reach']:>8}  engagement {it['engagement_rate']:>5}%  reposts/1k {it['reposts_per_1k']:>5}")
        out.append(f"           \"{it['hook'][:90]}\"")
    out.append("")
    out += what_separates(items)
    return "\n".join(out)
