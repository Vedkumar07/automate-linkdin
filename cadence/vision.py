"""Looks at a photo with a small local vision model and returns facts the writer can use. The image never leaves your machine."""
from __future__ import annotations

import base64
from pathlib import Path

from .config import Settings
from .llm import LLMError, Ollama, parse_json

MAX_BYTES = 12_000_000

SYSTEM = (
    "You describe images for someone who cannot see them. Be literal and specific. "
    "Describe only what is actually visible. Never guess names, companies, locations, numbers or events. "
    "Never identify or name any person. If you are unsure about something, leave it out."
)
PROMPT = (
    "Look at the image and return only JSON with these keys:\n"
    '"kind": one of screenshot, code, terminal, chart, diagram, photo, document, other\n'
    '"subject": one plain sentence saying what this is\n'
    '"details": a list of up to 6 concrete things visible (objects, UI elements, colours, layout, expressions)\n'
    '"visible_text": any text you can read, copied exactly, up to 300 characters, or an empty string\n'
    '"alt": one sentence of alt text for a screen reader, under 140 characters'
)


def analyze(st: Settings, path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise LLMError("The photo file is missing.")
    if p.stat().st_size > MAX_BYTES:
        raise LLMError("The photo is too large to analyse (over 12 MB).")
    llm = Ollama(st["ollamaUrl"], st["visionModel"], timeout=300)
    raw = llm.chat(SYSTEM, PROMPT, 0.2, images=[base64.b64encode(p.read_bytes()).decode()], keep_alive="30s")
    obj = parse_json(raw)
    if not isinstance(obj, dict) or not (obj.get("subject") or obj.get("details")):
        raise LLMError("The vision model did not return a usable description.")
    details = obj.get("details") or []
    if isinstance(details, str):
        details = [details]
    return {
        "kind": str(obj.get("kind") or "photo")[:20],
        "subject": str(obj.get("subject") or "")[:300],
        "details": [str(d)[:160] for d in details][:6],
        "visible_text": str(obj.get("visible_text") or "")[:300],
        "alt": str(obj.get("alt") or obj.get("subject") or "")[:200],
    }


def describe(a: dict) -> str:
    """Text for the writer prompt."""
    lines = [f"Type: {a['kind']}. {a['subject']}"]
    if a["details"]:
        lines.append("Visible: " + "; ".join(a["details"]))
    if a["visible_text"]:
        lines.append(f'Text in the image: "{a["visible_text"]}"')
    return "\n".join(lines)


def short(a: dict) -> str:
    """One line for the Telegram reply."""
    s = a["subject"] or "; ".join(a["details"][:2])
    return s[:220]
