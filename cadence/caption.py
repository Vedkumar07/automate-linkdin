"""Shows the post the way a scrolling stranger sees it, then lints it.

LinkedIn cuts a post behind '...see more'. The visible part is set by config 'previewChars'
(about 180 by default; it varies by device). Most posts that fail, fail in that window.
"""
from __future__ import annotations

import re

from . import hooks


def visible(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    cut = cut[: cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip() + " ..."


def preview_box(text: str, limit: int, width: int = 58) -> str:
    shown = visible(text, limit)
    lines: list[str] = []
    for para in shown.split("\n"):
        while len(para) > width:
            cut = para[:width].rfind(" ")
            cut = cut if cut > 0 else width
            lines.append(para[:cut])
            para = para[cut:].lstrip()
        lines.append(para)
    bar = "+" + "-" * (width + 2) + "+"
    body = [f"| {l.ljust(width)} |" for l in lines] or [f"| {'':<{width}} |"]
    tail = "... see more" if len(text) > limit else "(whole post visible)"
    return "\n".join(["WHAT THE FEED SHOWS", bar, *body, bar, f"  {tail}"])


def lint(text: str, cfg: dict, keywords: list[str] | None = None) -> list[tuple[str, str, str]]:
    """Returns (PASS|WARN, check name, detail)."""
    out: list[tuple[str, str, str]] = []
    n = len(text)
    out.append(("PASS" if n <= 3000 else "WARN", "LENGTH", f"{n} / 3000 characters"))
    limit = int(cfg.get("previewChars", 180))
    fl = hooks.first_line(text)
    out.append(("PASS" if len(fl) <= limit else "WARN", "FIRST LINE",
                f"{len(fl)} characters, visible limit about {limit}"))
    h = hooks.score(text)
    out.append(("PASS" if h["score"] >= int(cfg.get("hookMin", 55)) else "WARN", "HOOK",
                f"{h['grade']} ({h['score']}), weakest {h['weakest']}" + (f". {h['dealbreaker']}" if h["dealbreaker"] else "")))
    tags = re.findall(r"(?:^|\s)#\w+", text)
    out.append(("PASS" if len(tags) <= int(cfg.get("maxHashtags", 2)) else "WARN", "HASHTAGS",
                f"{len(tags)} (max {cfg.get('maxHashtags', 2)})"))
    asks = len(re.findall(r"\?|\bcomment\b|\bdm me\b|\bsend\b", text, re.I))
    out.append(("PASS" if asks <= 1 else "WARN", "ONE ASK", f"{asks} call(s) to respond"))
    if keywords:
        found = [k for k in keywords if k.lower() in text.lower()]
        out.append(("PASS" if len(found) == len(keywords) else "WARN", "SEARCH TERMS",
                    f"{len(found)}/{len(keywords)} present" + (f". Missing: {', '.join(k for k in keywords if k not in found)}" if len(found) < len(keywords) else "")))
    return out
