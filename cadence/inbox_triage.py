"""Sorts LinkedIn messages you paste into leads, recruiters, questions, peers and spam, then drafts
replies for the ones worth answering. You paste the messages; Cadence never reads your inbox.

Each message gets a 'tell': the word or phrase that put it in its category, so you can check the call.
"""
from __future__ import annotations

import re

from .humanize import clean as humanize_clean
from .llm import Ollama, parse_json

RULES = [
    ("spam", re.compile(r"\b(guarantee[ds]?|limited spots|\d+% off|click (?:the )?link|passive income|crypto|forex|whatsapp me|free trial|dm me for|i can double your)\b", re.I)),
    ("lead", re.compile(r"\b(budget|proposal|quote|pricing|price|work (?:with|together)|consult(?:ing|ant)?|hire us|can you build|rfp|scope of work|engagement)\b", re.I)),
    ("recruiter", re.compile(r"\b(opportunity|we are hiring|we're hiring|open role|your profile|recruit(?:er|ing)?|job description|contract role|comp(?:ensation)? range)\b", re.I)),
    ("question", re.compile(r"\?|\b(could you|can you|would you|help me|advice|question about)\b", re.I)),
    ("peer", re.compile(r"\b(great|nice|loved|saw your|congrats|your post|your talk|connect)\b", re.I)),
]
ORDER = ["lead", "recruiter", "question", "peer", "other", "spam"]
REPLY_GUIDE = {
    "lead": "Answer the request directly, say what you would need to know to scope it, and offer one next step. Do not push a sale.",
    "recruiter": "Be polite and specific: name what you are looking for and one thing you would need to see about the role. Do not accept or reject yet.",
    "question": "Answer the question directly in two sentences. If you do not know, say so.",
    "peer": "Thank them with one specific detail from their message, and say one thing you are working on. Keep it short.",
}


def classify(message: str) -> dict:
    for name, pat in RULES:
        m = pat.search(message)
        if m:
            return {"category": name, "tell": m.group(0).lower()}
    return {"category": "other", "tell": "no signal"}


def triage(messages: list[str]) -> list[dict]:
    out = []
    for raw in messages:
        msg = raw.strip()
        if not msg:
            continue
        out.append({"text": msg, **classify(msg)})
    out.sort(key=lambda d: ORDER.index(d["category"]))
    return out


def draft_replies(llm: Ollama, cfg: dict, items: list[dict], limit: int = 8) -> None:
    """Adds a 'reply' to each item that deserves one. Spam and 'other' get none."""
    for it in items[:limit]:
        guide = REPLY_GUIDE.get(it["category"])
        if not guide:
            continue
        obj = parse_json(llm.chat(
            f"You reply to LinkedIn messages as {cfg['name']}. Plain, direct, no emoji, no sign-offs.",
            f'Their message: """{it["text"][:1500]}"""\n{guide}\nReturn only JSON: {{"reply": "..."}}', 0.6)) or {}
        text, _ = humanize_clean(str(obj.get("reply") or ""))
        it["reply"] = text.strip()
