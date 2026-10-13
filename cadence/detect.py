"""Five-check 'how human does this read' panel.

Every check runs locally on the text, scores 0-100, and higher always means more human-like.
These are heuristics modelled on what writing checkers tend to key on. They are NOT a detector
API and cannot promise any verdict. Use them to catch obviously machine-sounding drafts, then
trust your own reading.
"""
from __future__ import annotations

import re
import statistics

from .humanize import INVISIBLE, flagged_phrases

CONCRETE = re.compile(
    r"\d|\$|₹|%|`[^`]+`|\b[a-z]+[A-Z][a-zA-Z]*\b|\b\w+\.\w+\b"
    r"|\b(?i:am|pm|ms|gb|mb|kg|km|hours?|minutes?|days?|weeks?|years?)\b")
CAP_NAME = re.compile(r"(?<=[a-z,] )[A-Z][a-z]+")
TRIAD = re.compile(r"\b\w+, \w+,? (?:and|or) \w+\b", re.I)
SHOUT = re.compile(r"\b[A-Z]{4,}\b")
PREAMBLE = re.compile(r"^(in this (?:post|article|video)|today i (?:want|wanted) to|let me tell you)", re.I | re.M)
ENGAGEMENT_BAIT = re.compile(r"(?:\bthoughts\?|\bagree\?|\bagree or disagree\b|\bcomment below\b|\blet me know in the comments\b|\bdrop your thoughts\b|\blike if you\b|\brepost if\b)", re.I)
EMOJI_BULLET = re.compile(r"^\s*[•‣◦⁃∙\U0001F300-\U0001FAFF☀-➿]\s", re.M)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if re.search(r"\w", s)]


def structural_tells(text: str) -> list[str]:
    tells = []
    if len(TRIAD.findall(text)) >= 2:
        tells.append("several rule-of-three lists")
    if len(SHOUT.findall(text)) >= 3:
        tells.append("three or more shouted words")
    if len(re.findall(r"#\w+", text)) >= 4:
        tells.append("hashtag wall")
    if EMOJI_BULLET.search(text):
        tells.append("emoji bullet list")
    if PREAMBLE.search(text):
        tells.append("video or post preamble")
    if re.search(r"(?:^|[.!]\s+)\w{1,12}\?(?=\s|$)", text, re.M):
        tells.append("one-word rhetorical question")
    if ENGAGEMENT_BAIT.search(text):
        tells.append("engagement bait")
    if re.search(r"\bnot (?:just|only) [^.\n]{1,60}[,;]? ?(?:it's|it is|but)\b", text, re.I):
        tells.append('"not just X, it\'s Y"')
    return tells


def score(text: str, extra_flags: list[str] | None = None) -> dict:
    words = text.split()
    n = max(len(words), 1)
    sents = sentences(text)
    checks: dict[str, int] = {}
    notes: dict[str, str] = {}

    # BURSTINESS: humans vary sentence length. Coefficient of variation of sentence lengths.
    lens = [len(s.split()) for s in sents]
    if len(lens) >= 3 and statistics.mean(lens) > 0:
        cv = statistics.pstdev(lens) / statistics.mean(lens)
        checks["burstiness"] = int(min(100, cv / 0.6 * 100))
        notes["burstiness"] = f"sentence length varies by {cv:.2f} (0.6 or more reads natural)"
    else:
        checks["burstiness"] = 50
        notes["burstiness"] = "too few sentences to judge"

    # SPECIFICITY: concrete markers (numbers, times, units, tool names, names) per 100 words.
    markers = len(CONCRETE.findall(text)) + len(CAP_NAME.findall(text))
    rate = markers * 100 / n
    checks["specificity"] = int(min(100, rate * 15))
    notes["specificity"] = f"{rate:.1f} concrete details per 100 words"

    # SLOP: stock phrases per 100 words.
    hits = flagged_phrases(text, extra_flags)
    slop_rate = len(hits) * 100 / n
    checks["slop"] = int(max(0, 100 - slop_rate * 12))
    notes["slop"] = f"{len(hits)} stock phrase(s)" if hits else "no stock phrases"

    # FINGERPRINT: characters and punctuation habits that give generated text away.
    em = text.count("—")
    invisible = len(INVISIBLE.findall(text))
    curly = len(re.findall("[“”‘’]", text))
    penalty = em * 20 + invisible * 25 + min(curly, 4) * 5
    checks["fingerprint"] = int(max(0, 100 - penalty))
    notes["fingerprint"] = f"{em} em dash, {invisible} invisible char(s), {curly} curly quote(s)"

    # VOICE: structural tells (lists, triads, preambles, shouting).
    tells = structural_tells(text)
    checks["voice"] = int(max(0, 100 - 25 * len(tells)))
    notes["voice"] = ", ".join(tells) if tells else "no structural tells"

    # The mean says how the draft reads overall; the weakest single check says where a detector would fire.
    # Weighting the weakest 40% stops one bad check being averaged away.
    human = round(0.6 * (sum(checks.values()) / len(checks)) + 0.4 * min(checks.values()))
    return {
        "checks": checks,
        "notes": notes,
        "human": human,
        "phrases": hits,
        "tells": tells,
        "weakest": min(checks, key=checks.get),
    }
