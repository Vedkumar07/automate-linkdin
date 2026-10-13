"""Local dashboard on http://localhost:8787. Bound to 127.0.0.1 only; every change needs a per-session token."""
from __future__ import annotations

import html
import re
import secrets
import threading
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo
from .util import parse_iso
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import plan, quality, studio
from .config import parse_voice
from .linkedin import LinkedInError
from .util import log

E = html.escape

CSS = """
:root{--bg:#f6f4ef;--panel:#fffdf9;--ink:#1d1b17;--mute:#6f6a5f;--line:#e4dfd3;--accent:#0f5c4d;--accent-ink:#fff;--warn:#9a5b00;--warn-bg:#fff3d9;--bad:#a3281d;--ok:#1f7a4d}
@media (prefers-color-scheme:dark){:root{--bg:#14130f;--panel:#1c1b16;--ink:#efece4;--mute:#a39e90;--line:#2e2c24;--accent:#4fb89f;--accent-ink:#08201a;--warn:#f0b455;--warn-bg:#2c2412;--bad:#ef7b6e;--ok:#5ccf93}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:0 20px 80px}
header{display:flex;align-items:center;gap:28px;padding:26px 0 18px;border-bottom:1px solid var(--line);margin-bottom:26px;flex-wrap:wrap}
.brand{font:600 22px/1 ui-serif,Georgia,serif;letter-spacing:-.01em}.brand i{font-style:normal;color:var(--accent)}
nav{display:flex;gap:4px}nav a{color:var(--mute);text-decoration:none;padding:6px 12px;border-radius:8px;font-size:15px}nav a.on,nav a:hover{color:var(--ink);background:var(--panel);box-shadow:0 0 0 1px var(--line)}
h2{font:600 13px/1 ui-sans-serif,system-ui;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);margin:34px 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.k{font-size:12px;color:var(--mute);margin-bottom:4px}.v{font-weight:600}.v small{display:block;font-weight:400;color:var(--mute);font-size:13px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;background:var(--mute)}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.draft{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px}
.draft .meta{font-size:13px;color:var(--mute);margin-bottom:10px;display:flex;gap:10px;flex-wrap:wrap}
.tag{background:var(--bg);border:1px solid var(--line);border-radius:999px;padding:1px 10px}
textarea{width:100%;font:17px/1.6 ui-serif,Georgia,serif;background:transparent;color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:12px 14px;resize:vertical}
textarea:focus{outline:2px solid var(--accent);outline-offset:1px}textarea.mono{font:14px/1.5 ui-monospace,Menlo,monospace}
.flags{background:var(--warn-bg);color:var(--warn);border-radius:10px;padding:8px 12px;font-size:14px;margin:10px 0 0}
.row{display:flex;gap:10px;margin-top:12px;flex-wrap:wrap;align-items:center}
button,.btn{font:600 15px/1 inherit;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:11px 16px;cursor:pointer;text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}button:hover,.btn:hover{filter:brightness(.97)}
.spacer{flex:1}select{font:inherit;padding:10px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--ink)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
td,th{padding:11px 14px;border-bottom:1px solid var(--line);text-align:left;font-size:14px;vertical-align:top}tr:last-child td{border:0}th{color:var(--mute);font-weight:500}
.st-posted{color:var(--ok)}.st-failed{color:var(--bad)}.st-skipped,.st-expired{color:var(--mute)}
.banner{background:var(--accent);color:var(--accent-ink);border-radius:12px;padding:12px 16px;margin-bottom:18px;font-weight:600}
.note{background:var(--warn-bg);color:var(--warn);border-radius:12px;padding:12px 16px;margin-bottom:12px;font-size:14px}
.empty{color:var(--mute);padding:18px;border:1px dashed var(--line);border-radius:14px}
.thumb{max-width:260px;max-height:200px;border-radius:10px;border:1px solid var(--line);display:block;margin:0 0 12px}pre.out{white-space:pre-wrap;word-break:break-word;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;font:13px/1.55 ui-monospace,Menlo,monospace;overflow-x:auto}
details.tool{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:12px}
details.tool summary{font-weight:600;cursor:pointer;font-size:16px}details.tool form{margin-top:10px}
input.inp,select{width:100%;max-width:520px;font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:transparent;color:var(--ink);margin:6px 0}
textarea{margin:6px 0}.lbl{display:block;font-size:13px;color:var(--mute);margin-top:8px}.chk{display:block;margin:6px 0}
p.help{color:var(--mute);font-size:14px;margin:0 0 12px}.inbox li{margin:6px 0}
@media(max-width:560px){.wrap{padding:0 14px 60px}}
"""


def page(title: str, active: str, body: str, token: str, refresh: bool = False, msg: str = "") -> bytes:
    nav = "".join(f'<a class="{"on" if k == active else ""}" href="{h}">{n}</a>'
                  for k, h, n in (("today", "/", "Today"), ("studio", "/studio", "Studio"), ("voice", "/voice", "Voice"),
                                  ("bg", "/background", "Background")))
    flash = f'<div class="banner">{E(msg)}</div>' if msg else ""
    meta = '<meta http-equiv="refresh" content="4">' if refresh else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Cadence - {E(title)}</title>{meta}<style>{CSS}</style></head><body><div class="wrap">'
            f'<header><div class="brand">Cadence<i>.</i></div><nav>{nav}</nav></header>{flash}{body}</div></body></html>').replace("__CSRF__", token).encode()


def hidden(token: str) -> str:
    return f'<input type="hidden" name="csrf" value="{token}">'


class Dashboard:
    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(18)
        self.states: set[str] = set()

    # ---------------- views ----------------
    def today(self, msg: str) -> bytes:
        a = self.app
        st = a.settings()
        li = a.li.status()
        nxt = a.next_run()
        paused = a.db.kv_get("paused", False)
        ok, note, _ = a.llm_health()
        busy = a.gen_lock.locked() or any(p["status"] == "publishing" for p in a.db.list_posts(5))
        chips = [
            ("Next automatic draft", "ok" if nxt and not paused else "bad",
             "Paused" if paused else (nxt.strftime("%a %d %b") if nxt else "Not scheduled"),
             "" if paused or not nxt else nxt.strftime("%H:%M") + f" {st['timezone'].split('/')[-1]}"),
            ("LinkedIn", "ok" if li["connected"] else "bad", "Connected" if li["connected"] else "Not connected", li["note"]),
            ("Writer", "ok" if ok else "bad", st["model"], note),
            ("Phone", "ok" if a.tg.enabled and a.tg.owner else "bad", "Telegram" if a.tg.enabled else "Off",
             "approvals and inbox" if a.tg.enabled and a.tg.owner else "set TELEGRAM_OWNER_CHAT_ID" if a.tg.enabled else "no bot token"),
        ]
        cards = "".join(f'<div class="card"><div class="k">{k}</div><div class="v"><span class="dot {c}"></span>{E(v)}<small>{E(s)}</small></div></div>'
                        for k, c, v, s in chips)
        out = []
        if busy:
            out.append('<div class="banner">Working on it. This page refreshes by itself.</div>')
        for w in st.warnings():
            out.append(f'<div class="note">{E(w)}</div>')
        if not li["connected"] and a.li.configured():
            out.append('<div class="row"><a class="btn primary" href="/connect">Connect LinkedIn</a></div>')
        out.append(f'<div class="cards">{cards}</div>')
        out.append(f'<h2>This week</h2><pre class="out">{E(a.plan_text())}</pre>')

        # drafts waiting
        pend = a.db.pending() + [p for p in a.db.list_posts(5, "failed")]
        out.append("<h2>Needs your decision</h2>")
        if not pend:
            out.append('<div class="empty">Nothing waiting. The next draft appears here and on your phone.</div>')
        for p in pend:
            flags = f'<div class="flags">{E("; ".join(p["flags"]))}</div>' if p["flags"] else ""
            err = f'<div class="flags">{E(p["error"])}</div>' if p["status"] == "failed" else ""
            src = f' <a href="{E(p["news_link"])}" target="_blank" rel="noopener noreferrer">source</a>' if p["news_link"] else ""
            wc = len(p["text"].split())
            docl = (f'<p class="help"><a href="/media/{E(Path(p["source"]["doc_path"]).name)}" target="_blank">Open the carousel PDF</a></p>'
                    if p["source"].get("doc_path") else "")
            out.append(f'''<form class="draft" method="post" action="/decide/{p["id"]}">{hidden(self.token)}
{docl}<div class="meta"><span class="tag">{E(p["mode"])}</span><span>#{p["id"]} &middot; {wc} words{src}</span></div>
{('<img class="thumb" alt="" src="/media/' + E(Path(p["photo_path"]).name) + '">') if p["photo_path"] else ""}<textarea name="text" rows="11">{E(p["text"])}</textarea>{flags}{err}
<div class="row"><button class="primary" name="do" value="post">Post it</button>
<button name="do" value="save">Save edit</button><button name="do" value="regen">Different take</button>
<span class="spacer"></span><button name="do" value="skip">Skip</button></div>
<div class="row"><label>Publish at ({E(st['timezone'])}) <input type="datetime-local" name="scheduled_at"></label>
<button name="do" value="schedule">Schedule post</button></div>
<p class="help">Scheduling approves this draft for automatic publication. Keep Cadence running.</p></form>''')

        out.append('<h2>Scheduled posts</h2>')
        scheduled = a.db.scheduled()
        if not scheduled:
            out.append('<div class="empty">No posts scheduled for publication.</div>')
        if paused and scheduled:
            out.append('<div class="note">Publishing is paused. Resume to publish due posts.</div>')
        for p in scheduled:
            when = parse_iso(p['scheduled_at']).astimezone(ZoneInfo(st['timezone']))
            out.append(f'''<form class="draft" method="post" action="/decide/{p['id']}">{hidden(self.token)}
<div class="meta">#{p['id']} &middot; Scheduled for {E(when.strftime('%a %d %b %Y, %H:%M'))} {E(st['timezone'])}</div>
{('<img class="thumb" alt="Scheduled post attachment" src="/media/' + E(Path(p['photo_path']).name) + '">') if p['photo_path'] else ''}
{('<p class="help"><a href="/media/' + E(Path(p['source']['doc_path']).name) + '" target="_blank">Open the carousel PDF</a></p>') if p['source'].get('doc_path') else ''}
<pre class="out">{E(p['text'])}</pre>
<div class="row"><label>New publishing time <input type="datetime-local" name="scheduled_at" value="{when.strftime('%Y-%m-%dT%H:%M')}"></label>
<button name="do" value="schedule">Reschedule</button><button name="do" value="cancel">Cancel schedule / edit draft</button></div></form>''')

        modes = "".join(f'<option value="{m}">{l}</option>' for m, l in
                        (("", "Let Cadence choose"), ("news", "News take"), ("profile", "From my background"),
                         ("thought", "A reflection"), ("input", "From my inbox")))
        out.append(f'''<h2>Write now</h2><form method="post" action="/generate">{hidden(self.token)}
<div class="row" style="margin-top:0"><select name="mode">{modes}</select><button class="primary">Write a draft</button></div></form>
<form method="post" action="/{"resume" if paused else "pause"}" class="row">{hidden(self.token)}
<button>{"Resume schedule" if paused else "Pause schedule"}</button></form>''')

        inbox = a.db.inbox_waiting()
        items = "".join(f'<li>{E(i["text"][:140])}{" <span class=tag>photo</span>" if i["photo_path"] else ""}</li>' for i in inbox)
        out.append(f'''<h2>Inbox</h2><p class="help">What you are working on, an opinion, a story. Anything here is used before news.
Photos go through Telegram.</p><form method="post" action="/inbox">{hidden(self.token)}
<textarea name="text" rows="3" placeholder="This week I spent three days on..."></textarea>
<div class="row"><button>Add to inbox</button></div></form><ul class="inbox">{items}</ul>''')

        rows = "".join(
            f'<tr><td>{E((p["posted_at"] or p["created_at"] or "")[:10])}</td><td>{E(p["mode"])}</td>'
            f'<td>{E(p["text"][:110])}...</td><td class="st-{p["status"]}">{p["status"]}</td></tr>' for p in a.db.list_posts(15))
        out.append(f'<h2>History</h2>' + (f'<table><tr><th>Date</th><th>Type</th><th>Post</th><th></th></tr>{rows}</table>'
                                          if rows else '<div class="empty">No posts yet.</div>'))
        return page("Today", "today", "".join(out), self.token, refresh=busy, msg=msg)

    def studio_page(self, msg: str = "", result: str = "") -> bytes:
        body = (f'<h2>Result</h2><pre class="out">{E(result)}</pre>' if result else "") + studio.form_html(self.token)
        return page("Studio", "studio", body, self.token, msg=msg)

    def voice(self, msg: str) -> bytes:
        text = (self.app.root / "voice.md").read_text(encoding="utf-8") if (self.app.root / "voice.md").exists() else ""
        rules, samples = parse_voice(text)
        body = f'''<h2>Your voice</h2><p class="help">This is what makes posts sound like you. The model copies examples far better than it follows rules,
so paste 3 to 6 things you really wrote: old posts, a long message explaining a bug to a friend, an email. Separate each with a line containing only <code>---</code>.</p>
<form method="post" action="/voice">{hidden(self.token)}
<p class="help"><b>Writing samples</b> ({len(samples)} saved)</p><textarea class="" name="samples" rows="16" placeholder="Paste your own writing here...">{E(chr(10).join(["\n---\n".join(samples)]))}</textarea>
<p class="help" style="margin-top:18px"><b>Style rules</b></p><textarea class="mono" name="rules" rows="14">{E(rules)}</textarea>
<div class="row"><button class="primary">Save voice</button></div></form>'''
        return page("Voice", "voice", body, self.token, msg=msg)

    def background(self, msg: str) -> bytes:
        def rd(n):
            p = self.app.root / n
            return p.read_text(encoding="utf-8") if p.exists() else ""
        body = f'''<h2>Background</h2><p class="help">Real, specific things from your work and life. One per line, starting with "- ". Cadence picks one at random
and writes about the lived experience behind it. Small and slightly embarrassing beats impressive. Lines containing "(example)" are ignored.</p>
<form method="post" action="/background">{hidden(self.token)}
<p class="help"><b>Profile facts</b> (from your resumes)</p><textarea class="mono" name="profile" rows="12">{E(rd("profile.md"))}</textarea>
<p class="help" style="margin-top:18px"><b>Stories in your own words</b></p><textarea class="mono" name="stories" rows="12">{E(rd("stories.md"))}</textarea>
<div class="row"><button class="primary">Save background</button></div></form>'''
        return page("Background", "bg", body, self.token, msg=msg)

    # ---------------- actions ----------------
    def act(self, path: str, f: dict) -> str:
        a = self.app
        field = lambda k: (f.get(k) or [""])[0]
        if path == "/generate":
            threading.Thread(target=a.generate_safe, kwargs={"manual": True, "mode": field("mode") or None}, daemon=True).start()
            return "Writing a draft. This takes a minute or two on your laptop."
        if path.startswith("/decide/"):
            pid = int(path.rsplit("/", 1)[1])
            do, text = field("do"), field("text").strip()
            post = a.db.get_post(pid)
            if not post:
                return "That draft no longer exists."
            if do == "cancel":
                return "Schedule cancelled. The draft is waiting for approval." if a.cancel_schedule(pid) else "This post is no longer scheduled."
            if do == "schedule":
                # Validate and edit together before the worker can claim the draft.
                with a.db.lock:
                    label = a.schedule_post(pid, field("scheduled_at"))
                    if text and text != post["text"]:
                        if post["status"] not in ("pending", "failed"):
                            return f"Scheduled for {label}."
                        flags = quality.check(quality.clean(text), a.settings().cfg, quality.banned_patterns(a.settings()["banned"]))
                        a.db.update_post(pid, text=quality.clean(text), flags=flags)
                return f"Scheduled for {label}. Cadence must be running."
            if text and text != post["text"] and post["status"] == "pending":
                a.edit(pid, text)
                post = a.db.get_post(pid)
            if do == "post":
                threading.Thread(target=a.approve, args=(pid,), daemon=True).start()
                return "Posting to LinkedIn."
            if do == "skip":
                a.skip(pid)
                return "Skipped."
            if do == "regen":
                threading.Thread(target=a.regenerate, args=(pid,), daemon=True).start()
                return "Writing a different take."
            return "Saved."
        if path == "/inbox":
            t = field("text").strip()
            if t:
                a.db.add_inbox(t)
                return "Added. It will be used for the next post."
            return "Nothing to add."
        if path in ("/pause", "/resume"):
            a.db.kv_set("paused", path == "/pause")
            return "Schedule paused." if path == "/pause" else "Schedule running."
        if path == "/voice":
            samples = [s.strip() for s in re.split(r"^---\s*$", field("samples"), flags=re.M) if s.strip()]
            rules = field("rules").strip()
            (a.root / "voice.md").write_text(
                "# Voice\n\nEverything above the \"My real writing samples\" line is the style guide the model is given.\n\n## Rules\n\n"
                + rules + "\n\n## My real writing samples\n\n" + "\n\n---\n\n".join(samples) + "\n", encoding="utf-8")
            return f"Voice saved with {len(samples)} sample(s)."
        if path == "/background":
            for name, key in (("profile.md", "profile"), ("stories.md", "stories")):
                (a.root / name).write_text(field(key).strip() + "\n", encoding="utf-8")
            return "Background saved."
        return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "Cadence"
    dash: Dashboard

    def log_message(self, *a):  # keep the console quiet
        pass

    def _send(self, body: bytes, status=200, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str):
        self._send(b"", 303, headers={"Location": to})

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("localhost", "127.0.0.1", "[::1]")  # blocks DNS-rebinding

    def do_GET(self):
        if not self._host_ok():
            return self._send(b"forbidden", 403)
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        d, msg = self.dash, (q.get("msg") or [""])[0]
        try:
            if u.path == "/":
                return self._send(d.today(msg))
            if u.path == "/voice":
                return self._send(d.voice(msg))
            if u.path == "/studio":
                return self._send(d.studio_page(msg))
            if u.path == "/background":
                return self._send(d.background(msg))
            if u.path.startswith("/media/"):
                media = (d.app.st.data_dir / "media").resolve()
                f = (media / Path(urllib.parse.unquote(u.path[7:])).name).resolve()
                if f.parent != media or not f.is_file():
                    return self._send(b"not found", 404)
                ctype = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf"}.get(f.suffix.lower(), "image/jpeg")
                return self._send(f.read_bytes(), ctype=ctype)
            if u.path == "/connect":
                if not d.app.li.configured():
                    return self._redirect("/?msg=" + urllib.parse.quote("Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to .env first."))
                state = secrets.token_urlsafe(16)
                d.states.add(state)
                return self._redirect(d.app.li.auth_url(state))
            if u.path == "/oauth/callback":
                state, code = (q.get("state") or [""])[0], (q.get("code") or [""])[0]
                if state not in d.states or not code:
                    err = (q.get("error_description") or ["Sign-in was cancelled or the link expired."])[0]
                    return self._redirect("/?msg=" + urllib.parse.quote(err))
                d.states.discard(state)
                try:
                    me = d.app.li.exchange(code)
                except LinkedInError as e:
                    return self._redirect("/?msg=" + urllib.parse.quote(str(e)))
                return self._redirect("/?msg=" + urllib.parse.quote(f"LinkedIn connected as {me.get('name', 'you')}."))
            return self._send(b"not found", 404)
        except Exception:
            log.exception("dashboard error")
            return self._send(b"Something went wrong. See the terminal.", 500)

    def do_POST(self):
        if not self._host_ok():
            return self._send(b"forbidden", 403)
        n = min(int(self.headers.get("Content-Length") or 0), 400_000)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        if (form.get("csrf") or [""])[0] != self.dash.token:
            return self._send(b"Session expired. Go back and reload the page.", 403)
        path = urllib.parse.urlparse(self.path).path
        if path == "/studio":
            try:
                result = studio.run(self.dash.app, form)
            except Exception as e:
                log.exception("studio failed")
                result = f"That did not work: {e}"
            return self._send(self.dash.studio_page(result=result))
        try:
            msg = self.dash.act(path, form)
        except Exception as e:
            log.exception("action failed")
            msg = f"That did not work: {e}"
        back = {"/voice": "/voice", "/background": "/background"}.get(path, "/")
        self._redirect(f"{back}?msg=" + urllib.parse.quote(msg))


def serve(app, stop: threading.Event):
    w = app.settings()["web"]
    Handler.dash = Dashboard(app)
    srv = ThreadingHTTPServer((w["host"], int(w["port"])), Handler)
    srv.timeout = 1
    log.info("dashboard on http://localhost:%s", w["port"])
    while not stop.is_set():
        srv.handle_request()
    srv.server_close()
