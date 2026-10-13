"""Command line: run, doctor, draft, init."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__, scheduler, telegram, web, writer
from .llm import Ollama
from .service import App
from .util import log, setup_logging

ROOT = Path(os.environ.get("CADENCE_HOME") or Path(__file__).resolve().parent.parent)


def cmd_run(args):
    setup_logging()
    app = App(ROOT)
    st = app.st
    for w in st.warnings():
        log.warning(w)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threads = [
        threading.Thread(target=web.serve, args=(app, stop), name="web", daemon=True),
        threading.Thread(target=scheduler.run_forever, args=(app, stop), name="scheduler", daemon=True),
        threading.Thread(target=app.tg.poll_forever, args=(stop,), name="telegram", daemon=True),
    ]
    for t in threads:
        t.start()
    url = f"http://localhost:{st['web']['port']}"
    print(f"\n  Cadence {__version__} is running.\n  Dashboard: {url}\n  Press Ctrl+C to stop.\n")
    stop.wait()
    log.info("shutting down")


def cmd_doctor(args):
    app = App(ROOT)
    st = app.st
    rows = []
    ok, note, names = Ollama(st["ollamaUrl"], st["model"]).health()
    rows.append(("Ollama + model", ok, note))
    vm = st["visionModel"] if ":" in st["visionModel"] else st["visionModel"] + ":latest"
    rows.append(("Vision model (photos)", vm in names, "ready" if vm in names else f"not pulled: ollama pull {st['visionModel']}"))
    rows.append(("Voice samples", bool(st.samples), f"{len(st.samples)} saved" if st.samples else "none yet, posts will sound generic (open the Voice page)"))
    rows.append(("Background lines", bool(st.profile_lines), f"{len(st.profile_lines)} usable" if st.profile_lines else "none, background posts disabled"))
    rows.append(("Telegram token", app.tg.enabled, "set" if app.tg.enabled else "missing TELEGRAM_BOT_TOKEN in .env"))
    if app.tg.enabled:
        try:
            me = app.tg._call("getMe")
            rows.append(("Telegram bot", True, f"@{me.get('username')}"))
        except Exception as e:
            rows.append(("Telegram bot", False, f"token rejected: {e}"))
        rows.append(("Telegram owner id", bool(app.tg.owner), app.tg.owner or "not set; message the bot once and it replies with your id"))
    rows.append(("LinkedIn app keys", app.li.configured(), "set" if app.li.configured() else "missing client id/secret in .env"))
    s = app.li.status()
    rows.append(("LinkedIn sign-in", s["connected"], s["note"] if s["connected"] else s["note"] + " (open the dashboard, press Connect LinkedIn)"))
    nxt = app.next_run()
    try:
        import reportlab  # noqa: F401
        rows.append(("Carousel PDFs", True, "reportlab installed"))
    except ImportError:
        rows.append(("Carousel PDFs", False, "optional: pip install reportlab (carousels need it)"))
    rows.append(("Schedule", nxt is not None, nxt.strftime("next %a %d %b %H:%M") if nxt else "no days configured"))
    for name, good, detail in rows:
        print(f"  {'OK  ' if good else 'FIX '} {name:<20} {detail}")
    print(f"\n  LinkedIn redirect URL to register: {app.li.redirect_uri}")
    return 0 if all(g for _, g, _ in rows) else 1


def cmd_draft(args):
    setup_logging("WARNING")
    app = App(ROOT)
    st = app.settings()
    llm = Ollama(st["ollamaUrl"], st["model"])
    if args.dry:
        g = writer.gather(st, app.db, args.mode)
        if g["mode"] == "news":
            g = writer.pick_story(llm, g)
        d = writer.write(st, llm, g)
        print(f"[{g['mode']}] {g.get('news_title') or g.get('topic_key')}\n\n{d['text']}")
        if d["flags"]:
            print("\nFlags: " + "; ".join(d["flags"]))
        return 0
    pid = app.generate(manual=True, mode=args.mode)
    print(f"Draft #{pid} is waiting for your approval (dashboard or Telegram).")
    return 0


def cmd_init(args):
    env_path = ROOT / ".env"
    print("Cadence setup. Press Enter to keep a value shown in [brackets].\n")
    cur = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip()
    ask = lambda label, key: (input(f"{label} [{cur.get(key, '')[:6] + '...' if cur.get(key) else ''}]: ").strip() or cur.get(key, ""))
    cur["TELEGRAM_BOT_TOKEN"] = ask("Telegram bot token (from @BotFather)", "TELEGRAM_BOT_TOKEN")
    cur["TELEGRAM_OWNER_CHAT_ID"] = ask("Your Telegram chat id (leave empty, then message the bot to learn it)", "TELEGRAM_OWNER_CHAT_ID")
    cur["LINKEDIN_CLIENT_ID"] = ask("LinkedIn app Client ID", "LINKEDIN_CLIENT_ID")
    cur["LINKEDIN_CLIENT_SECRET"] = ask("LinkedIn app Client Secret", "LINKEDIN_CLIENT_SECRET")
    env_path.write_text("".join(f"{k}={v}\n" for k, v in cur.items() if v), encoding="utf-8")
    env_path.chmod(0o600)
    port = App(ROOT).st["web"]["port"]
    print(f"\nSaved to {env_path} (readable only by you).")
    print(f"Add this redirect URL in the LinkedIn developer app (Auth tab): http://localhost:{port}/oauth/callback")
    print("Then run: python3 -m cadence run")


def cmd_tools(args):
    from pathlib import Path as P
    from . import analytics, beats, studio
    from .humanize import clean as _clean
    st = App(ROOT).settings()
    text = P(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
    if args.tool == "check":
        out = studio.check_report(st, text)
    elif args.tool == "hooks":
        out = studio.hooks_report(text)
    elif args.tool == "humanize":
        cleaned, changes = _clean(text)
        out = cleaned + ("\n\nChanges: " + "; ".join(changes) if changes else "\n\n(no changes)")
    elif args.tool == "beats":
        out = beats.render(text, args.target)
    elif args.tool == "audit":
        out = studio.audit_report(text)
    elif args.tool == "swipe":
        out = studio.swipe_report(text)
    else:
        out = "unknown tool"
    print(out)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="cadence", description="A LinkedIn writing and posting assistant that runs on your own machine.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start everything (dashboard, schedule, Telegram)").set_defaults(fn=cmd_run)
    sub.add_parser("doctor", help="check that everything is set up").set_defaults(fn=cmd_doctor)
    sub.add_parser("init", help="enter your keys").set_defaults(fn=cmd_init)
    d = sub.add_parser("draft", help="write a post now")
    d.add_argument("--dry", action="store_true", help="print it, do not save or send")
    d.add_argument("--mode", choices=writer.MODES, default=None)
    d.set_defaults(fn=cmd_draft)
    t = sub.add_parser("tools", help="run a writing or analysis tool on a file (use - for stdin)")
    t.add_argument("tool", choices=["check", "hooks", "humanize", "beats", "audit", "swipe"])
    t.add_argument("file")
    t.add_argument("--target", type=float, default=30.0, help="beats: target seconds")
    t.set_defaults(fn=cmd_tools)
    args = p.parse_args(argv)
    sys.exit(args.fn(args) or 0)
