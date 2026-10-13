"""Scores your LinkedIn profile out of 100 against a fixed rubric, then lists fixes in the order
that buys the most points. You paste the text in; Cadence never reads your profile itself.
"""
from __future__ import annotations

import re

BUZZ = re.compile(r"\b(passionate|guru|ninja|rockstar|rock star|visionary|enthusiast|wizard|evangelist|thought leader)\b", re.I)
GENERIC_ABOUT = re.compile(r"^\s*(i am|i'm) (a )?(passionate|hard[- ]working|results[- ]driven|dedicated)", re.I)


def audit(p: dict) -> dict:
    """p keys: headline, about, experience, skills (int), featured (bool), banner (bool),
    photo (bool), contact (bool), recent_post (bool)."""
    headline = (p.get("headline") or "").strip()
    about = (p.get("about") or "").strip()
    exp = (p.get("experience") or "").strip()
    items = [
        ("Headline says what you do", 10, bool(re.search(r"\b(engineer|developer|devops|sre|architect|designer|analyst|manager|founder|consultant|lead)\b", headline, re.I)),
         "Name the role in the headline, e.g. 'DevOps engineer, Kubernetes and CI/CD'."),
        ("Headline has a specific detail", 8, bool(re.search(r"\d|\||·|kubernetes|terraform|aws|azure|gcp|python|go|rust|postgres", headline, re.I)),
         "Add one concrete tool or number after a separator."),
        ("Headline avoids buzzwords", 6, bool(headline) and not BUZZ.search(headline),
         "Remove 'passionate', 'guru', 'ninja' and similar."),
        ("Headline length is readable", 4, 30 <= len(headline) <= 220,
         "Keep the headline between 30 and 220 characters."),
        ("About opens with what you do", 10, bool(about) and not GENERIC_ABOUT.search(about),
         "Open the About section with one plain sentence about the work, not a personality claim."),
        ("About contains a real number", 8, bool(re.search(r"\d", about)),
         "Add one number from your work: scale, time saved, users, incidents."),
        ("About is long enough to say something", 8, len(about) >= 300,
         "Aim for 300 to 1,000 characters: what you do, what you are good at, what you want next."),
        ("Experience bullets have results", 12, len(re.findall(r"\d", exp)) >= 3,
         "Put numbers in your experience bullets: 'cut deploy time from 2h to 9min'."),
        ("At least 5 skills listed", 6, int(p.get("skills") or 0) >= 5,
         "List at least five skills you can defend in an interview."),
        ("Featured section filled", 8, bool(p.get("featured")),
         "Pin your best post, a project or a write-up to Featured."),
        ("Banner and photo set", 10, bool(p.get("banner")) and bool(p.get("photo")),
         "Use a real, recent photo and a banner that says what you do."),
        ("Posted in the last 30 days", 10, bool(p.get("recent_post")),
         "Post or comment at least weekly. Dormant profiles are ranked lower."),
    ]
    score = sum(w for _, w, ok, _ in items if ok)
    fixes = [{"item": name, "points": w, "fix": fix} for name, w, ok, fix in items if not ok]
    fixes.sort(key=lambda f: -f["points"])
    return {"score": score, "passed": [name for name, _, ok, _ in items if ok], "fixes": fixes,
            "max": sum(w for _, w, _, _ in items)}


def rewrite(llm, cfg: dict, fields: dict) -> dict:
    """Rewrites the headline and the first two lines of About, the parts visible before 'see more'.
    Only uses facts already in the profile text: any number the model adds that is not in the
    input is flagged, not silently kept."""
    import json as _json
    from .humanize import clean as humanize_clean
    from .llm import parse_json

    facts = "\n".join(str(fields.get(k) or "") for k in ("headline", "about", "experience"))
    user = (f"Current headline: {fields.get('headline') or '(none)'}\n"
            f"Current About: {(fields.get('about') or '')[:1200]}\n"
            f"Experience bullets: {(fields.get('experience') or '')[:1200]}\n"
            "Rewrite the headline (under 220 characters, names who you help and the proof, not a job title) "
            "and the first two sentences of About (the point, in plain words, no 'passionate about'). "
            "Use only facts from the text above. Do not invent numbers.\n"
            'Return only JSON: {"headline": "...", "about_open": "..."}')
    obj = parse_json(llm.chat(f"You rewrite a LinkedIn profile as {cfg['name']}. Plain, specific, no buzzwords.",
                              user, 0.6)) or {}
    out = {}
    for k in ("headline", "about_open"):
        text, _ = humanize_clean(str(obj.get(k) or "").strip())
        out[k] = text
    known = set(re.findall(r"\d+(?:[.,]\d+)*", facts))
    out["flags"] = [f'mentions "{n}", which is not in your profile text'
                    for n in dict.fromkeys(re.findall(r"\d+(?:[.,]\d+)*", out["headline"] + " " + out["about_open"]))
                    if n not in known]
    if out["headline"] and len(out["headline"]) > 220:
        out["flags"].append(f"headline is {len(out['headline'])} characters; LinkedIn allows 220")
    return out
