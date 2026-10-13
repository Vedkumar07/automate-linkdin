"""Writing for OTHER people's posts and conversations: comments, replies under your post, a DM,
a repurposing pass and carousel slides.

Everything here returns text for you to read and paste yourself. Cadence does not comment, message
or follow anyone. Commenting through the API is restricted to approved partners, and automated
engagement gets accounts restricted, so the design stays copy-and-post.
"""
from __future__ import annotations

import re

from .humanize import clean as humanize_clean
from .llm import Ollama, parse_json

COMMENT_TYPES = {
    "achievement": "congratulate with one specific fact about what they did, then one question about how",
    "opinion": "agree or disagree with one specific reason from your own work",
    "lesson": "add one real example from your work that matches their lesson",
    "question": "answer their question in one concrete sentence, then offer one detail",
    "news": "react with your honest first reaction and one consequence for people like us",
    "story": "say what the story reminded you of, in one specific sentence",
    "hiring": "say what you would bring to a role like this, in one specific sentence. Do not beg or ask for the job",
    "vulnerable": "answer with one similar moment from your own work, in one sentence. Do not give advice",
    "product": "say what you would test first and why, in one concrete sentence. Be honest about one doubt",
}
INVITE_LIMIT = 200
OPENERS_BANNED = re.compile(r"^(great post|great read|thanks for sharing|love this|so true|well said|this is so|totally agree|amazing|congrats!?$)", re.I)


def classify_post(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(we'?re hiring|we are hiring|open role|job opening|hiring for)\b", t):
        return "hiring"
    if re.search(r"\b(introducing|now available|just released|open.?sourced)\b", t):
        return "product"
    if re.search(r"\b(laid off|rejected|burn(?:ed|t)? out|burnout|struggled|failed|i was wrong|scared|fired me)\b", t):
        return "vulnerable"
    if re.search(r"\b(promoted|launched|shipped|hired|joined|award|milestone|celebrat|excited to)\b", t):
        return "achievement"
    if re.search(r"\b(unpopular|i think|i believe|hot take|disagree|wrong)\b", t):
        return "opinion"
    if re.search(r"\b(lesson|learned|mistake|taught|realised|realized)\b", t):
        return "lesson"
    if "?" in text:
        return "question"
    if re.search(r"\b(news|announced|report|study|released)\b", t):
        return "news"
    return "story"


def write_comment(llm: Ollama, cfg: dict, post_text: str, my_context: str = "") -> dict:
    kind = classify_post(post_text)
    lo, hi = 8, 45
    rules = (f"Comment type: {kind}. Instruction: {COMMENT_TYPES[kind]}.\n"
             f"Keep it {lo} to {hi} words. Plain sentences. No emoji. No compliments about the post itself. "
             "Never start with 'Great post', 'Thanks for sharing', 'Love this' or 'So true'. "
             "Do not invent facts about the author or their work.")
    system = f"You write LinkedIn comments as {cfg['name']}. Style guide:\n{cfg.get('_rules', '')}".strip()
    user = (f'Their post:\n"""{post_text[:3000]}"""\n\n' + (f"Something real from my work: {my_context}\n" if my_context else "")
            + rules + '\nReturn only JSON: {"comment": "..."}')
    obj = parse_json(llm.chat(system, user, 0.7)) or {}
    text, _ = humanize_clean(str(obj.get("comment") or ""))
    text = text.strip().strip('"')
    issues = []
    n = len(text.split())
    if not text:
        issues.append("empty")
    if n < lo or n > hi:
        issues.append(f"{n} words, need {lo}-{hi}")
    if OPENERS_BANNED.search(text):
        issues.append("generic opener")
    return {"kind": kind, "text": text, "flags": issues}


CATEGORY_ORDER = ["lead", "question", "substance", "support", "noise"]


def sort_replies(comments: list[str]) -> list[dict]:
    """Sorts comments under your post by what needs an answer first."""
    out = []
    for c in comments:
        t = c.strip()
        low = t.lower()
        if re.search(r"\b(send|dm me|interested|how much|price|pricing|can you share|link|details|hire|contact)\b", low):
            cat = "lead"
        elif "?" in t:
            cat = "question"
        elif len(t.split()) >= 12 or re.search(r"\b(but|however|disagree|actually|I tried|we tried)\b", low):
            cat = "substance"
        elif re.fullmatch(r"[\W\d_]*", t) or re.fullmatch(r"(first|following|nice|great|congrats|thanks!?|👏+|🔥+)\W*", low):
            cat = "noise"
        else:
            cat = "support"
        out.append({"text": t, "category": cat})
    out.sort(key=lambda d: CATEGORY_ORDER.index(d["category"]))
    return out


def draft_reply(llm: Ollama, cfg: dict, my_post: str, comment: str, category: str) -> str:
    instr = {
        "lead": "Answer the request directly and offer the next step in one sentence. Do not push a sale.",
        "question": "Answer the question specifically in two sentences.",
        "substance": "Engage with their specific point, agree or push back with one reason.",
        "support": "Thank them with one specific detail from the post. Keep it short.",
    }.get(category, "Keep it very short.")
    obj = parse_json(llm.chat(
        f"You reply on LinkedIn as {cfg['name']}. Plain, direct, no emoji, no sign-offs.",
        f'My post: """{my_post[:1500]}"""\nTheir comment: "{comment}"\n{instr}\nReturn only JSON: {{"reply": "..."}}',
        0.6)) or {}
    text, _ = humanize_clean(str(obj.get("reply") or ""))
    return text.strip()


DM_KINDS = {
    "invite": "a connection request note to someone you do not know yet. Hard limit: 200 characters including spaces. Say why you are connecting in one specific sentence. No pitch, no 'I'd love to pick your brain'.",
    "first": "a short first message after they engaged with your post. Mention one specific thing from their comment. Under 45 words. No pitch.",
    "follow1": "a gentle follow-up one week later. Reference the first message in one line. Under 30 words.",
    "follow2": "a final, polite close-the-loop message. Say it is fine if the timing is wrong. Under 25 words.",
}


def draft_dm(llm: Ollama, cfg: dict, kind: str, context: str, follow_ups_sent: int = 0) -> dict:
    if kind not in DM_KINDS:
        raise ValueError("kind must be first, follow1 or follow2")
    if kind.startswith("follow") and follow_ups_sent >= 2:
        return {"text": "", "flags": ["Two follow-ups is the limit. Stop here."]}
    obj = parse_json(llm.chat(
        f"You write LinkedIn direct messages as {cfg['name']}. Warm, specific, never salesy.",
        f"Context: {context[:1500]}\nWrite {DM_KINDS[kind]}\nReturn only JSON: {{\"dm\": \"...\"}}", 0.6)) or {}
    text, _ = humanize_clean(str(obj.get("dm") or ""))
    text = text.strip()
    flags = [] if text else ["empty"]
    if kind == "invite" and len(text) > INVITE_LIMIT:
        flags.append(f"Over {INVITE_LIMIT} characters ({len(text)}). LinkedIn will cut it off. Shorten it before you send.")
    return {"text": text, "flags": flags}


def repurpose(llm: Ollama, cfg: dict, source: str, n: int = 5) -> list[str]:
    """Splits one source (article, transcript, newsletter) into standalone angles. Each angle is
    saved to the inbox, so the normal writer turns it into a post on its own schedule."""
    obj = parse_json(llm.chat(
        "You find separate, standalone post ideas inside a longer piece of my own writing.",
        f'Source:\n"""{source[:6000]}"""\n\nFind up to {n} different angles. Each must make sense without the others, '
        'and contain only facts that appear in the source.\nReturn only JSON: {"angles": ["one-sentence brief", ...]}', 0.5)) or {}
    angles = [str(a).strip() for a in (obj.get("angles") or []) if str(a).strip()]
    return angles[:n]


def carousel(llm: Ollama, cfg: dict, topic: str, slides: int = 7) -> dict:
    obj = parse_json(llm.chat(
        f"You write LinkedIn document carousels as {cfg['name']}. Each slide is short, concrete and plain.",
        f"Topic: {topic}\nWrite a carousel of {slides} slides: a cover, then content, then a last slide with one question.\n"
        'Return only JSON: {"title": "...", "intro": "post text under 120 words", '
        '"slides": [{"headline": "under 8 words", "body": "under 25 words"}]}', 0.7)) or {}
    out_slides = []
    for s in obj.get("slides") or []:
        if isinstance(s, dict) and s.get("headline"):
            out_slides.append({"headline": str(s["headline"])[:120], "body": str(s.get("body", ""))[:300]})
    return {"title": str(obj.get("title") or topic)[:100], "intro": str(obj.get("intro") or "")[:1200], "slides": out_slides[:12]}


def carousel_pdf(data: dict, path: str) -> bool:
    """Writes slides as a PDF (LinkedIn document posts take PDFs). Needs reportlab; returns False if missing."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        return False
    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4
    for i, s in enumerate(data["slides"] or [{"headline": data["title"], "body": ""}]):
        c.setFont("Helvetica-Bold", 30)
        c.drawString(60, h - 140, s["headline"][:60])
        c.setFont("Helvetica", 18)
        y = h - 200
        for line in _wrap(s["body"], 60):
            c.drawString(60, y, line)
            y -= 26
        c.setFont("Helvetica", 10)
        c.drawRightString(w - 60, 40, f"{i + 1} / {len(data['slides']) or 1}")
        c.showPage()
    c.save()
    return True


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 > width:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    if cur:
        lines.append(cur)
    return lines
