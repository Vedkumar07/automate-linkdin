"""The Studio: every tool from the Instagram skill set, mapped to LinkedIn, in one place.

Each tool returns plain text. The dashboard shows it in a <pre> block and Telegram sends it as a message,
so there is one code path for both.

  Check a draft      humanize + five-check human score + hook grade + caption preview and lint   (/ig-caption, /ig-human)
  Hook ranking       rank your opening lines                                                    (/ig-reel hookscore)
  Comment            a comment for someone else's post, typed by what the post is               (/ig-comment)
  Replies            sort the thread under your post, draft replies in priority order           (/ig-reply)
  DM                 first message and at most two follow-ups                                   (/ig-dm)
  Repurpose          one source into standalone briefs, saved to your inbox                     (/ig-repurpose)
  Carousel           slide copy and a PDF, posted as a LinkedIn document                        (/ig-carousel)
  My analytics      CSV of your own posts, ranked by multiple over your median                  (/ig-audit)
  Swipe file         CSV of posts you copied from others, ranked per account                    (/ig-viral)
  Profile audit      out of 100, fixes in the order that buys the most points                   (/ig-profile)
  Beat sheet         timing for a short video script                                            (/ig-reel beats)
  Inbox triage       paste messages, sorted into leads, recruiters, questions, peers and spam       (LinkedIn skill: li-inbox)
  Plan my week       the planned slots plus up to ten people to engage with, by hand                (LinkedIn skill: li-plan)

Not carried over: /ig-story (Instagram stories have no LinkedIn equivalent), and anything that
scrapes a feed or automates posting, commenting or DMs. Those are against LinkedIn's terms.
"""
from __future__ import annotations

import html

from . import analytics, beats, caption, detect, engage, hooks, humanize, inbox_triage, plan, profile_audit, quality
from .llm import Ollama

E = html.escape


def check_report(st, text: str) -> str:
    cleaned, changes = humanize.clean(quality.clean(text))
    if not cleaned:
        return "Paste a draft first."
    det = detect.score(cleaned)
    hk = hooks.score(cleaned)
    issues = quality.check(cleaned, st.cfg, quality.banned_patterns(st["banned"]))
    verdict = "PASS" if det["human"] >= int(st["humanScoreMin"]) else "FLAGGED"
    out = ["CHECK", "=" * 64]
    if changes:
        out.append("Cleaned automatically: " + "; ".join(changes))
    out.append(f"Human score: {det['human']}/100  {verdict}   (weakest: {det['weakest']})")
    for k, v in det["checks"].items():
        out.append(f"  {k:<12} {v:>3}  {det['notes'][k]}")
    out += ["", f"Opening line: {hk['grade']} {hk['score']}/100",
            f"  \"{hk['line'][:140]}\"", f"  weakest: {hk['weakest']}"]
    if hk["formula"]:
        out.append(f"  resembles: {hk['formula']}")
    if hk["dealbreaker"]:
        out.append(f"  dealbreaker: {hk['dealbreaker']}")
    out += ["", caption.preview_box(cleaned, int(st["previewChars"])), ""]
    out += [f"  {s:<4} {n:<14} {d}" for s, n, d in caption.lint(cleaned, st.cfg)]
    if issues:
        out += ["", "Rule flags: " + "; ".join(issues)]
    return "\n".join(out)


def hooks_report(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return "Paste one opening line per line."
    out = ["HOOK RANKING", "=" * 64]
    for r in hooks.rank(lines):
        out.append(f"{r['score']:>5}  {r['grade']:<7} {r['text'][:110]}")
        out.append(f"        weakest: {r['weakest']}" + (f"  |  {r['dealbreaker']}" if r["dealbreaker"] else ""))
    return "\n".join(out)


def comment_report(st, llm: Ollama, post: str, context: str) -> str:
    if not post.strip():
        return "Paste the post you want to comment on."
    res = engage.write_comment(llm, dict(st.cfg, _rules=st.rules), post, context.strip())
    flags = f"\n\nFlags: {'; '.join(res['flags'])}" if res["flags"] else ""
    return f"Type: {res['kind']}\n\n{res['text']}{flags}\n\nCopy it and post it yourself."


def reply_report(st, llm: Ollama, my_post: str, comments: str) -> str:
    lines = [c for c in comments.splitlines() if c.strip()]
    if not lines:
        return "Paste the comments, one per line."
    cfg = dict(st.cfg, _rules=st.rules)
    out = ["REPLY ORDER", "=" * 64]
    drafted = 0
    for item in engage.sort_replies(lines):
        out.append(f"[{item['category']}] {item['text'][:120]}")
        if item["category"] != "noise" and drafted < 6:
            draft = engage.draft_reply(llm, cfg, my_post, item["text"], item["category"])
            out.append(f"   reply: {draft}")
            drafted += 1
        out.append("")
    out.append("Noise is skipped on purpose. Post the replies yourself, leads first.")
    return "\n".join(out)


def dm_report(st, llm: Ollama, kind: str, context: str, follow_ups: int) -> str:
    res = engage.draft_dm(llm, dict(st.cfg, _rules=st.rules), kind, context.strip(), follow_ups)
    if res["flags"] and not res["text"]:
        return "\n".join(res["flags"])
    return f"{res['text']}\n\nSend it yourself. Two follow-ups is the limit."


def beats_report(script: str, target: float) -> str:
    return beats.render(script, target)


def audit_report(csv_text: str) -> str:
    rows = analytics.parse_csv(csv_text)
    if not rows:
        return "No rows. The CSV needs a header with a 'text' column, plus impressions, reactions, comments, reposts if you have them."
    return analytics.report(rows, "MY POSTS")


def swipe_report(csv_text: str) -> str:
    rows = analytics.parse_csv(csv_text)
    if not rows:
        return "No rows. Add a 'text' column and an 'account' column, and copy only numbers you can see yourself."
    return analytics.report(rows, "SWIPE FILE")


def profile_report(fields: dict) -> str:
    res = profile_audit.audit(fields)
    out = [f"PROFILE  {res['score']} / {res['max']}", "=" * 64]
    for f in res["fixes"]:
        out.append(f"  +{f['points']:<3} {f['item']}")
        out.append(f"        {f['fix']}")
    if not res["fixes"]:
        out.append("  Nothing to fix on this rubric.")
    out += ["", "Passed: " + "; ".join(res["passed"]) if res["passed"] else ""]
    return "\n".join(out)


def inbox_report(st, llm: Ollama, text: str) -> str:
    msgs = [m for m in (text or "").split("\n---\n") if m.strip()]
    if not msgs:
        return "Paste one message per block, with a line containing only --- between them."
    items = inbox_triage.triage(msgs)
    inbox_triage.draft_replies(llm, dict(st.cfg, _rules=st.rules), items)
    out = [f"INBOX  {len(items)} message(s)", "=" * 64]
    current = None
    for it in items:
        if it["category"] != current:
            current = it["category"]
            out += ["", current.upper()]
        snippet = it["text"].replace("\n", " ")[:120]
        out.append(f"  \"{snippet}\"   (tell: {it['tell']})")
        if it.get("reply"):
            out.append(f"  reply: {it['reply']}")
    out += ["", "Cadence never replies for you. Paste the reply yourself."]
    return "\n".join(out)


def profile_rewrite_report(st, llm: Ollama, fields: dict) -> str:
    res = profile_audit.rewrite(llm, dict(st.cfg, _rules=st.rules), fields)
    out = ["", "REWRITTEN (check every fact against your own record)", "=" * 64,
           f"Headline: {res['headline'] or '(no change)'}",
           f"About, first two sentences: {res['about_open'] or '(no change)'}"]
    if res["flags"]:
        out += ["", "Check before you use: " + "; ".join(res["flags"])]
    return "\n".join(out)


def tools_help() -> str:
    return "Tools: check, hooks, beats, audit, swipe, profile, comment, reply, dm, repurpose, carousel."


# ---------------- HTML form ----------------

FORMS = [
    ("check", "Check a draft", "Cleans the text, scores how human it reads, grades the opening line, and shows what the feed will show before 'see more'.",
     [("text", "textarea", "Paste the draft")], "Check it"),
    ("hooks", "Rank opening lines", "One candidate opening line per line. Ranked by the five hook checks.",
     [("text", "textarea", "Option one\nOption two\nOption three")], "Rank them"),
    ("comment", "Comment on someone's post", "Paste their post. Cadence picks the comment type that fits and writes one you can post yourself.",
     [("text", "textarea", "Their post"), ("context", "input", "Optional: something real from your work to draw on")], "Write comment"),
    ("reply", "Reply to comments on my post", "Paste your post, then the comments one per line. Sorted leads first, noise skipped.",
     [("post", "textarea", "My post"), ("comments", "textarea", "One comment per line")], "Sort and draft"),
    ("dm", "Direct message", "First message, or a follow-up. The limit is two follow-ups, then stop.",
     [("kind", "select", ["invite", "first", "follow1", "follow2"]), ("context", "textarea", "Who they are and what they said (for the invite: why you are connecting)"),
      ("followups", "number", "Follow-ups already sent (0 to 2)")], "Draft it"),
    ("repurpose", "Repurpose a source", "Paste an article, transcript or newsletter of your own. Cadence finds standalone angles and saves each to your inbox.",
     [("text", "textarea", "Your source text")], "Find angles"),
    ("carousel", "Carousel (document post)", "Topic in, slide copy out, and a PDF that goes up as a LinkedIn document. Waits for your approval.",
     [("topic", "input", "What the carousel is about")], "Make carousel"),
    ("audit", "My post analytics", "CSV of your own posts with a 'text' column and any of impressions, reactions, comments, reposts. Ranks by multiple over your median.",
     [("csv", "textarea", "text,impressions,reactions,comments,reposts\n...")], "Analyse"),
    ("swipe", "Swipe file", "CSV of posts you looked at yourself. Include an 'account' column so each post is compared with its own author's median.",
     [("csv", "textarea", "account,text,impressions\n...")], "Rank"),
    ("profile", "Profile audit", "Paste your own profile text. Scored out of 100, fixes ordered by points.",
     [("headline", "input", "Headline"), ("about", "textarea", "About section"),
      ("experience", "textarea", "Experience bullets"), ("skills", "number", "How many skills listed"),
      ("featured", "check", "Featured section filled"), ("banner", "check", "Banner set"), ("photo", "check", "Real photo set"),
      ("recent_post", "check", "Posted or commented in the last 30 days"),
      ("rewrite", "check", "Also rewrite the headline and first two lines of About")], "Audit profile"),
    ("inbox", "Inbox triage", "Paste LinkedIn messages, one per block, separated by a line containing only ---. Each is sorted, with the word that decided it, and replies are drafted for the ones worth answering.",
     [("text", "textarea", "Message one\n---\nMessage two")], "Sort inbox"),
    ("plan", "Plan my week", "The posting slots for the next week, plus up to ten people you want to engage with. Paste their names or profile links, one per line.",
     [("targets", "textarea", "Asha Rao\nhttps://www.linkedin.com/in/...")], "Build the week"),
    ("beats", "Beat sheet for a short video", "Times a script before you record it. Flags slow hooks, long beats and missing loops.",
     [("text", "textarea", "One line per beat"), ("target", "number", "Target seconds (default 30)")], "Time it"),
]


def form_html(token: str) -> str:
    out = ['<h2>Studio</h2><p class="help">Tools that help you write and check. Nothing here posts, comments or messages anyone. You paste the result yourself.</p>']
    for key, title, help_, fields, button in FORMS:
        inputs = []
        for name, kind, arg in fields:
            if kind == "textarea":
                inputs.append(f'<label class="lbl">{E(arg)}</label><textarea name="{name}" rows="5" placeholder="{E(arg)}"></textarea>')
            elif kind == "input":
                inputs.append(f'<input class="inp" name="{name}" placeholder="{E(arg)}">')
            elif kind == "number":
                default = "30" if name == "target" else "0"
                inputs.append(f'<label class="lbl">{E(arg)}</label><input class="inp" type="number" name="{name}" value="{default}" min="0">')
            elif kind == "select":
                opts = "".join(f'<option value="{o}">{o}</option>' for o in arg)
                inputs.append(f'<select name="{name}">{opts}</select>')
            elif kind == "check":
                inputs.append(f'<label class="chk"><input type="checkbox" name="{name}"> {E(arg)}</label>')
        out.append(f'<details class="tool" {"open" if key == "check" else ""}><summary>{E(title)}</summary>'
                   f'<form method="post" action="/studio">'
                   f'<input type="hidden" name="csrf" value="{token}"><input type="hidden" name="tool" value="{key}">'
                   f'<p class="help">{E(help_)}</p>{"".join(inputs)}'
                   f'<div class="row"><button class="primary">{E(button)}</button></div></form></details>')
    return "".join(out)


def run(app, form: dict) -> str:
    """Runs one tool from a submitted form. Returns plain text."""
    g = lambda k: (form.get(k) or [""])[0]
    tool = g("tool")
    st = app.settings()
    llm = app.llm(st)
    if tool == "check":
        return check_report(st, g("text"))
    if tool == "hooks":
        return hooks_report(g("text"))
    if tool == "comment":
        return comment_report(st, llm, g("text"), g("context"))
    if tool == "reply":
        return reply_report(st, llm, g("post"), g("comments"))
    if tool == "dm":
        try:
            fu = int(g("followups") or 0)
        except ValueError:
            fu = 0
        return dm_report(st, llm, g("kind") or "first", g("context"), fu)
    if tool == "repurpose":
        return app.repurpose(g("text"))
    if tool == "carousel":
        if not g("topic").strip():
            return "Give the carousel a topic."
        pid = app.carousel_post(g("topic").strip())
        return f"Carousel draft #{pid} is waiting. Approve it on Telegram or on the dashboard."
    if tool == "audit":
        return audit_report(g("csv"))
    if tool == "swipe":
        return swipe_report(g("csv"))
    if tool == "profile":
        fields = {"headline": g("headline"), "about": g("about"), "experience": g("experience"),
                  "skills": g("skills") or 0, **{k: bool(form.get(k)) for k in ("featured", "banner", "photo", "recent_post")}}
        out = profile_report(fields)
        if form.get("rewrite") and (g("headline") or g("about")):
            out += "\n" + profile_rewrite_report(st, llm, fields)
        return out
    if tool == "inbox":
        return inbox_report(st, llm, g("text"))
    if tool == "plan":
        targets = [t for t in g("targets").splitlines()]
        items = plan.engagement_list(targets)
        return app.plan_text() + "\n\n" + plan.render_engagement(items)
    if tool == "beats":
        try:
            target = float(g("target") or 30)
        except ValueError:
            target = 30.0
        return beats_report(g("text"), target)
    return tools_help()
