"""The quality gate. A draft that trips any rule is rewritten; if it still trips, it is never auto-posted."""
from __future__ import annotations

import re

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⬆↔-↪⌚-⏿️]")


def banned_patterns(banned: list[str]):
    out = []
    for b in banned:
        pat = (r"\b" if re.match(r"\w", b) else "") + re.escape(b) + (r"\b" if re.search(r"\w$", b) else "")
        out.append((b, re.compile(pat, re.I)))
    return out


ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def clean(p: str) -> str:
    p = ZERO_WIDTH.sub("", str(p or "")).replace("\r", "").strip()  # invisible characters are a tell
    p = re.sub(r"^(here'?s|here is)[^\n]*:\s*\n+", "", p, flags=re.I)
    p = re.sub(r'^["“”]+|["“”]+$', "", p)
    p = re.sub(r"\s*—\s*", ", ", p)          # em dash reads as machine-written
    p = re.sub(r"\s+–\s+", ", ", p)
    p = re.sub(r"[ \t]+\n", "\n", p)
    p = re.sub(r"\n{3,}", "\n\n", p)
    return p.strip()


def word_count(p: str) -> int:
    return len(p.split())


def sentences(p: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", p) if s.strip()]


def check(post: str, cfg: dict, patterns, facts: str | None = None) -> list[str]:
    issues: list[str] = []
    norm = post.replace("‘", "'").replace("’", "'")
    n = word_count(norm)
    lo, hi = cfg["length"]["minWords"], cfg["length"]["maxWords"]
    if n < lo:
        issues.append(f"too short ({n} words, need {lo}-{hi})")
    if n > hi:
        issues.append(f"too long ({n} words, need {lo}-{hi})")
    for b, rx in patterns:
        if rx.search(norm):
            issues.append(f'uses banned phrase "{b}"')
    if not cfg.get("allowEmoji") and EMOJI.search(post):
        issues.append("has an emoji")
    tags = len(re.findall(r"(^|\s)#\w+", post))
    if tags > cfg["maxHashtags"]:
        issues.append(f"has {tags} hashtags, max {cfg['maxHashtags']}")
    if re.match(r"^\s*(hi|hello|hey|greetings|good (morning|evening)|excited|thrilled|happy to|proud to)\b", norm, re.I):
        issues.append("opens with a greeting or announcement")
    if re.search(r"\b(isn't|is not|wasn't|aren't|not) (just|only|merely) [^.\n]{1,60}[,;]? ?(it's|it is|but|it was)\b", norm, re.I) \
            or re.search(r"\bmore than just\b", norm, re.I):
        issues.append('uses a "not just X, it\'s Y" construction')
    if len(re.findall(r"^\s*([-*•]|\d+[.)])\s", post, re.M)) >= 2:
        issues.append("uses a list; write plain paragraphs")
    if re.search(r"\b(thoughts|agree|what do you think|let me know)\??\s*$", norm.strip(), re.I):
        issues.append("ends with a generic engagement closer")
    sents = sentences(re.sub(r"(^|\s)#\w+", "", norm))
    if sents and min(len(s.split()) for s in sents) > 8:
        issues.append("every sentence is long; add a short one")
    if len(sents) >= 4:
        lens = [len(s.split()) for s in sents]
        if max(lens) - min(lens) < 6:
            issues.append("sentences are all the same length; vary the rhythm")
    starts = [s.split()[0].lower() for s in sents if s.split()]
    if len(starts) >= 5 and max(starts.count(w) for w in set(starts)) >= 4:
        issues.append("too many sentences start with the same word")
    if facts is not None:  # the post must not invent figures that were not in what the user gave us
        known = set(re.findall(r"\d+(?:[.,]\d+)*", facts))
        for num in dict.fromkeys(re.findall(r"\d+(?:[.,]\d+)*", norm)):
            if num not in known:
                issues.append(f'mentions "{num}", which is not in what you gave me')
                break
    return issues


def little_text(p: str) -> str:
    """LinkedIn's Posts API treats these characters as markup; an unescaped '(' can truncate the post."""
    p = re.sub(r"[\\|{}@\[\]()<>*_~]", lambda m: "\\" + m.group(0), p)
    return re.sub(r"#(?![A-Za-z0-9_])", r"\\#", p)
