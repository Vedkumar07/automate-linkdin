"""Cleans a draft before anything else looks at it.

1. Removes invisible characters that creep into generated text and survive copy-paste.
2. Fixes typography that reads as machine-made (em dashes, curly quotes, ellipses).
3. Swaps a short list of stock words for plain ones (lexicon.json 'replace').
Phrases in lexicon.json 'flag' are never edited here; they come back as flags for a rewrite.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

INVISIBLE = re.compile(
    "[­͏؜ᅟᅠ឴឵᠋-᠎​-‏‪-‮"
    "⁠-⁯ㅤ︀-️﻿ﾠ\U000e0000-\U000e007f]")
SPACES = re.compile("[  -   　]")
EM_DASH = re.compile(r"\s*—\s*")
EN_DASH = re.compile(r"\s+–\s+")
ORPHAN = re.compile(r",\s*([.,!?;:])")
ELLIPSIS = "…"
CURLY_DOUBLE = re.compile("[“”]")
CURLY_SINGLE = re.compile("[‘’]")


@lru_cache(maxsize=1)
def lexicon() -> dict:
    path = Path(__file__).with_name("lexicon.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"replace": data.get("replace", {}), "flag": data.get("flag", [])}


def _swap(text: str, old: str, new: str) -> str:
    def rep(m: re.Match) -> str:
        word = m.group(0)
        if word[:1].isupper():
            return new[:1].upper() + new[1:]
        return new
    return re.sub(r"(?<![\w-])" + re.escape(old) + r"(?![\w-])", rep, text, flags=re.I)


def clean(text: str) -> tuple[str, list[str]]:
    """Returns (clean_text, list of changes made). Changes are short labels for the report."""
    changes: list[str] = []
    before = text
    text = INVISIBLE.sub("", text)
    if text != before:
        changes.append("removed invisible characters")
    text2 = SPACES.sub(" ", text)
    if text2 != text:
        changes.append("normalised unusual spaces")
    text = text2
    for pat, rep, label in ((EM_DASH, ", ", "em dash to comma"), (EN_DASH, " - ", "en dash to hyphen")):
        new = pat.sub(rep, text)
        if new != text:
            changes.append(label)
        text = new
    new = ORPHAN.sub(r"\1", text)
    if new != text:
        changes.append("fixed punctuation left by a dash")
    text = new
    new = CURLY_DOUBLE.sub('"', CURLY_SINGLE.sub("'", text)).replace(ELLIPSIS, "...")
    if new != text:
        changes.append("straightened quotes")
    text = new
    for old, new_word in lexicon()["replace"].items():
        swapped = _swap(text, old, new_word)
        if swapped != text:
            changes.append(f'"{old}" to "{new_word}"')
        text = swapped
    return text, changes


def flagged_phrases(text: str, extra: list[str] | None = None) -> list[str]:
    norm = text.replace("’", "'").lower()
    hits = []
    for phrase in list(lexicon()["flag"]) + list(extra or []):
        if re.search(r"(?<![\w])" + re.escape(phrase.lower()) + r"(?![\w])", norm):
            hits.append(phrase)
    return hits
