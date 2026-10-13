"""Decides what the next post is about, then writes it and runs the quality gate."""
from __future__ import annotations

import random

import json
import re

from . import detect, hooks, humanize, news, quality, vision
from .config import Settings
from .db import DB
from .llm import Ollama, parse_json

MODES = ("input", "profile", "news", "thought")
THOUGHT_ANGLES = [
    "something that annoyed me this week",
    "a small thing I changed my mind about",
    "an unpopular opinion I hold",
    "a mistake I keep repeating",
    "something everyone pretends to enjoy",
    "a thing I used to believe that turned out wrong",
]


def choose_mode(st: Settings, db: DB, rng=random) -> str:
    """Your own input always wins. Otherwise a weighted pick that never repeats the same mode three times running."""
    if db.inbox_waiting():
        return "input"
    weights = dict(st["mix"])
    if not st.profile_lines:
        weights.pop("profile", None)
    if not st["feeds"]:
        weights.pop("news", None)
    recent = db.recent_modes(2)
    if len(recent) == 2 and recent[0] == recent[1] and recent[0] in weights and len(weights) > 1:
        weights.pop(recent[0])
    entries = [(k, w) for k, w in weights.items() if w > 0]
    if not entries:
        return "thought"
    r = rng.random() * sum(w for _, w in entries)
    for k, w in entries:
        r -= w
        if r <= 0:
            return k
    return entries[0][0]


def gather(st: Settings, db: DB, mode: str | None = None, rng=random, input_id: int | None = None) -> dict:
    mode = "input" if input_id else (mode or choose_mode(st, db, rng))
    g = {"mode": mode, "context": "", "topic_key": "", "news_link": "", "news_title": "", "input_id": 0,
         "photo_path": "", "headlines": [], "tg_file_id": "", "photo_alt": "", "facts": "", "has_caption": True}
    if mode == "news":
        g["headlines"] = news.fetch_headlines(st["feeds"], db.used_keys("news"), rng=rng)
        if len(g["headlines"]) < 3:
            mode = g["mode"] = "thought"
    if mode == "input":
        waiting = db.inbox_waiting()
        item = db.inbox_get(input_id) if input_id else (waiting[0] if waiting else None)
        if not item:
            mode = g["mode"] = "thought"
        else:
            g.update(input_id=item["id"], photo_path=item["photo_path"], tg_file_id=item.get("tg_file_id", ""),
                     topic_key=(item["text"] or "photo")[:80], has_caption=bool(item["text"].strip()))
            analysis = None
            try:
                analysis = json.loads(item.get("analysis") or "null")
            except ValueError:
                pass
            facts = [item["text"]]
            if item["text"].strip():
                g["context"] = (f'Something I want to post about, in my own words: "{item["text"]}"\n'
                                "Stay true to what I said. Do not add details, numbers or events I did not mention.")
            if analysis:
                g["photo_alt"] = analysis.get("alt", "")
                facts.append(vision.describe(analysis))
                g["context"] += (
                    ("\n" if g["context"] else "")
                    + "I am attaching a photo with this post. A vision model looked at it (it can be wrong; my words win if they disagree):\n"
                    + vision.describe(analysis) + "\n"
                    "Write as if the reader is looking at the photo. Do not narrate it like alt text; use one or two concrete details from it. "
                    "Do not invent the project, company, people, numbers or events behind it beyond what I wrote or what is visible."
                    + ("" if item["text"].strip() else
                       " I gave no caption, so I have not told you the story. Write about what the picture itself invites, "
                       "a small honest observation or reaction, and do not claim specific events."))
            elif item["photo_path"]:
                g["context"] += "\nI am attaching a photo with this post, so write as if the reader is looking at it. Do not describe the photo."
            g["facts"] = "\n".join(f for f in facts if f)
    if mode == "profile":
        pool = st.profile_lines
        used = db.used_keys("profile")
        fresh = [l for l in pool if l not in used]
        if not fresh:
            db.reset_used("profile")
            fresh = pool
        line = rng.choice(fresh)
        g["topic_key"] = line
        g["context"] = (
            f'A real thing from my own background: "{line}"\n'
            "Write about the lived experience behind it: what actually happened, what went wrong or surprised me, "
            "what I would tell someone now. Do not turn it into a resume bullet or a brag. "
            "Do not invent specifics that are not in that line.")
    if mode == "thought":
        interests = st["interests"] or ["work", "money", "learning things the hard way"]
        interest = rng.choice(interests)
        g["topic_key"] = interest
        g["context"] = (
            f"Write a short personal reflection related to: {interest}.\nAngle: {rng.choice(THOUGHT_ANGLES)}.\n"
            "Keep it general enough that I could plausibly have lived it. "
            "Do not invent specific named people, companies or statistics.")
    return g


def pick_story(llm: Ollama, g: dict) -> dict:
    """A low-temperature 'editor' step picks the headline and my angle on it."""
    heads = g["headlines"]
    listing = "\n".join(f"{i + 1}. {h['title']} ({h['source']})" for i, h in enumerate(heads))
    raw = llm.chat(
        "You are an editor choosing what one curious, opinionated person would most want to react to. "
        "Prefer stories with a human angle, absurdity, or real consequences for ordinary people. "
        "Skip tragedies, deaths, disasters, wars, elections and party politics.",
        f"Headlines:\n{listing}\n\nReturn only JSON: "
        '{"pick": <number>, "angle": "<one sentence: my honest first-person reaction>"}', 0.3)
    r = parse_json(raw) or {}
    try:
        idx = int(r.get("pick") or 1)
    except (TypeError, ValueError):
        idx = 1
    chosen = heads[min(max(idx, 1), len(heads)) - 1]
    g.update(
        news_link=chosen["link"], news_title=chosen["title"], topic_key=chosen["title"][:80],
        context=(f'A headline I just read: "{chosen["title"]}" ({chosen["source"]}).\n'
                 f'My honest reaction: {r.get("angle") or "mixed feelings"}\n'
                 "React the way I would to a friend. Do not summarize the article. "
                 "Do not state anything about the story beyond the headline itself."))
    return g


def system_prompt(st: Settings) -> str:
    parts = [
        f"You are {st['name']}, a curious person who works in tech. You have opinions on everything: tech, news, work, "
        "life, money, cricket, whatever. Only bring up tech when the context actually calls for it. "
        "You write LinkedIn posts that sound like a real person, not a content team.",
        "STYLE GUIDE:\n" + st.rules,
        "NEVER USE these words or phrases: " + ", ".join(st["banned"]) + ".",
    ]
    if st.samples:
        parts.append("Here are things I have actually written. Match the rhythm and attitude. Never copy their sentences:\n\n"
                     + "\n\n---\n\n".join(st.samples))
    return "\n\n".join(parts)


def write(st: Settings, llm: Ollama, g: dict, avoid: str = "") -> dict:
    """Writes a draft, checks it, and rewrites with the failures as feedback. Returns the best attempt."""
    if g["mode"] == "news" and not g.get("news_link"):
        g = pick_story(llm, g)
    patterns = quality.banned_patterns(st["banned"])
    lo, hi = st["length"]["minWords"], st["length"]["maxWords"]
    system = system_prompt(st)
    best, feedback = None, ""
    if avoid:
        feedback = f"\nThe previous version was rejected by me. Take a different angle and different opening. Do not reuse its sentences:\n{avoid[:600]}"
    for i in range(st["maxTries"]):
        user = f'{g["context"]}\n\nWrite one LinkedIn post of {lo}-{hi} words.{feedback}\nReturn only JSON: {{"post": "..."}}'
        obj = parse_json(llm.chat(system, user, min(st["temperature"] + i * 0.05, 1.0)))
        post = humanize.clean(quality.clean((obj or {}).get("post") or (obj or {}).get("linkedin") or (obj or {}).get("text") or ""))[0]
        if not post:
            feedback = '\nYour previous reply was not valid JSON with a "post" field.'
            continue
        issues = quality.check(post, st.cfg, patterns, facts=g.get("facts") or None) + machine_issues(post, st)
        if best is None or len(issues) < len(best["flags"]):
            best = {"text": post, "flags": issues}
        if not issues:
            break
        feedback = (f"\nYour previous draft was rejected for: {'; '.join(issues[:5])}. "
                    "Rewrite it from scratch, fix all of those, and keep it sounding like me.")
    if best is None:
        raise RuntimeError("The model did not return a usable post. Is Ollama running and the model pulled?")
    if st["hookRewrite"] and not best["flags"]:
        try:
            improved = improve_opener(llm, st, best["text"])
        except Exception:  # a failed opener pass must never lose the draft
            improved = best["text"]
        if improved != best["text"]:
            best = {"text": improved, "flags": quality.check(improved, st.cfg, patterns, facts=g.get("facts") or None) + machine_issues(improved, st)}
    return {**best, "g": g}


def machine_issues(post: str, st: Settings) -> list[str]:
    """Problems the rule checks alone would miss: lexicon phrases and a low human score."""
    out = [f'uses stock phrase "{p}"' for p in humanize.flagged_phrases(post)]
    det = detect.score(post)
    if det["human"] < int(st["humanScoreMin"]):
        out.append(f"reads machine-written (human score {det['human']}, weakest {det['weakest']}: {det['notes'][det['weakest']]})")
    return out


def improve_opener(llm: Ollama, st: Settings, post: str) -> str:
    """If the first line is a weak hook, ask for three alternative openers and keep the best one that
    clearly beats it. The rest of the post is untouched."""
    first = hooks.first_line(post)
    if not first or hooks.score(post)["score"] >= int(st["hookMin"]):
        return post
    rest = post[len(first):]
    if not post.startswith(first):
        return post
    obj = parse_json(llm.chat(
        "You rewrite the first sentence of a LinkedIn post. Same facts, no greeting, start with the concrete thing.",
        f'Post:\n"""{post}"""\nWrite three alternative FIRST sentences. Each under 14 words.\n'
        'Return only JSON: {"openers": ["...", "...", "..."]}', 0.8)) or {}
    best_score, best_line = hooks.score(first)["score"], first
    for o in obj.get("openers") or []:
        cand = str(o).strip()
        if cand and hooks.score(cand)["score"] > best_score + 10 and not re.search(r"[\n]", cand):
            best_score, best_line = hooks.score(cand)["score"], cand
    return best_line + rest
