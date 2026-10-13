"""The App: every action (generate, approve, skip, edit, regenerate) lives here, so Telegram and the dashboard behave identically."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

import json
import uuid

from . import engage, plan, quality, scheduler, studio, vision, writer
from .config import Settings
from .db import DB
from .linkedin import LinkedIn, LinkedInError
from .llm import LLMError, Ollama
from .telegram import Telegram
from .util import iso, log, utcnow


class App:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.st = Settings.load(self.root)
        self.db = DB(self.st.data_dir / "cadence.db")
        self.tg = Telegram(self.st, self.db)
        self.tg.app = self
        self.li = LinkedIn(self.st, self.db)
        self.gen_lock = threading.Lock()

    def settings(self) -> Settings:
        """Fresh read, so edits to voice.md, profile.md and config.json apply to the very next post."""
        return Settings.load(self.root)

    # ---------------- writing ----------------
    def generate_safe(self, manual: bool = False, mode: str | None = None, input_id: int | None = None):
        try:
            return self.generate(manual=manual, mode=mode, input_id=input_id)
        except (LLMError, RuntimeError) as e:
            log.error("could not write a post: %s", e)
            self.tg.notify(f"Could not write a post: {e}")
        except Exception as e:
            log.exception("generate failed")
            self.tg.notify(f"Something broke while writing: {e}")
        return None

    def generate(self, manual: bool = False, mode: str | None = None, input_id: int | None = None) -> int:
        with self.gen_lock:
            st = self.settings()
            llm = Ollama(st["ollamaUrl"], st["model"])
            self._expire_pending()
            g = writer.gather(st, self.db, mode, input_id=input_id)
            if g["mode"] == "news":
                g = writer.pick_story(llm, g)
            d = writer.write(st, llm, g)
            return self._store(st, d, manual)

    def regenerate(self, pid: int):
        post = self.db.get_post(pid)
        if post and post["mode"] == "carousel":
            self.tg.notify("A carousel cannot be regenerated. Skip it and make a new one from the Studio.")
            return None
        if not post or not self.db.claim(pid, "pending", "skipped"):
            return None
        self.tg.notify("Writing a different take.")
        try:
            with self.gen_lock:
                st = self.settings()
                llm = Ollama(st["ollamaUrl"], st["model"])
                g = dict(post["source"])
                g.setdefault("headlines", [])
                d = writer.write(st, llm, g, avoid=post["text"])
                return self._store(st, d, manual=True)
        except Exception as e:
            self.db.update_post(pid, status="pending")  # put the original back
            self.tg.notify(f"Could not write another take: {e}")
            return None

    def handle_photo(self, iid: int):
        """A photo arrived on Telegram: look at it, say what was seen, then (by default) write a draft around it."""
        st = self.settings()
        item = self.db.inbox_get(iid)
        if not item:
            return
        analysis = None
        try:
            with self.gen_lock:  # one model on the GPU at a time
                analysis = vision.analyze(st, item["photo_path"])
        except LLMError as e:
            self.tg.notify(f"I could not look at the photo: {e}")
        except Exception as e:
            log.exception("photo analysis failed")
            self.tg.notify(f"I could not look at the photo: {e}")
        if analysis:
            self.db.inbox_set_analysis(iid, json.dumps(analysis))
            self.tg.notify("What I see: " + vision.short(analysis))
        elif not item["text"].strip():
            self.db.inbox_use(iid)
            self.tg.notify("Without a caption or a working vision model I have nothing to write from. "
                           "Send the photo again with a caption, or fix the model and resend.")
            return
        if st["photoDraftsImmediately"]:
            self.tg.notify("Writing a post around it. This takes a minute or two.")
            self.generate_safe(manual=True, mode="input", input_id=iid)
        else:
            self.tg.notify("Saved. It gets priority over news in the next post, or send /draft to write it now.")

    def _expire_pending(self):
        for p in self.db.pending():
            self.db.update_post(p["id"], status="expired")
            self.tg.clear_buttons(p["tg_message_id"])

    def _store(self, st: Settings, d: dict, manual: bool) -> int:
        g = d["g"]
        needs_approval = (manual or not st["autoPost"] or bool(d["flags"])
                          or (g["mode"] == "news" and st["alwaysApproveNews"]))
        flags = list(d["flags"])
        if g["mode"] == "input" and g.get("photo_path") and not g.get("has_caption", True):
            flags.append("no caption, so I only know what is visible. Reply with the real story to improve it")
        source = {k: g.get(k, "") for k in ("mode", "context", "topic_key", "news_link", "news_title", "input_id", "photo_path",
                                            "tg_file_id", "photo_alt", "facts", "has_caption")}
        pid = self.db.add_post(
            mode=g["mode"], topic_key=g.get("topic_key", ""), text=d["text"], flags=flags,
            status="pending", source=source, news_link=g.get("news_link", ""),
            input_id=g.get("input_id", 0), photo_path=g.get("photo_path", ""))
        if needs_approval:
            post = self.db.get_post(pid)
            mid = self.tg.send_draft(post)
            if mid:
                self.db.update_post(pid, tg_message_id=mid)
            log.info("draft #%s waiting for approval", pid)
        else:
            self.approve(pid)
        return pid

    # ---------------- decisions ----------------
    def edit(self, pid: int, text: str):
        post = self.db.get_post(pid)
        if not post or post["status"] != "pending":
            return
        st = self.settings()
        text = quality.clean(text)
        flags = quality.check(text, st.cfg, quality.banned_patterns(st["banned"]))
        self.tg.clear_buttons(post["tg_message_id"])
        self.db.update_post(pid, text=text, flags=flags)
        mid = self.tg.send_draft(self.db.get_post(pid))
        self.db.update_post(pid, tg_message_id=mid)

    def approve(self, pid: int):
        if not (self.db.claim(pid, "pending", "publishing") or self.db.claim(pid, "failed", "publishing")):
            return False
        post = self.db.get_post(pid)
        try:
            src = post["source"]
            lid = self.li.post(post["text"], post["photo_path"], src.get("photo_alt", ""),
                               document=src.get("doc_path", ""), doc_title=src.get("doc_title", ""))
        except LinkedInError as e:
            self.db.update_post(pid, status="failed", error=str(e))
            mid = self.tg.send(f"Not posted: {e}", [[("Try again", f"a:{pid}"), ("Skip", f"s:{pid}")]])
            self.db.update_post(pid, tg_message_id=mid)
            return False
        except Exception as e:
            self.db.update_post(pid, status="failed", error=str(e))
            self.tg.notify(f"Not posted, unexpected error: {e}")
            return False
        self.db.update_post(pid, status="posted", linkedin_id=lid, posted_at=iso(), error="")
        self._consume(post)
        link = f"https://www.linkedin.com/feed/update/{lid}" if lid else "your profile"
        self.tg.notify(f"Posted to LinkedIn ({post['mode']}).\n{link}")
        return True

    def skip(self, pid: int):
        if not (self.db.claim(pid, "pending", "skipped") or self.db.claim(pid, "failed", "skipped")):
            return False
        post = self.db.get_post(pid)
        if post["news_link"]:
            self.db.mark_used("news", post["news_link"])
        if post["mode"] == "profile" and post["topic_key"]:
            self.db.mark_used("profile", post["topic_key"])
        if post["input_id"]:
            self.db.inbox_skip(post["input_id"])
        self.tg.notify("Skipped.")
        return True

    def _consume(self, post: dict):
        if post["input_id"]:
            self.db.inbox_use(post["input_id"])
        if post["news_link"]:
            self.db.mark_used("news", post["news_link"])
        if post["mode"] == "profile" and post["topic_key"]:
            self.db.mark_used("profile", post["topic_key"])

    # ---------------- status ----------------
    # ---------------- studio ----------------
    def llm(self, st=None) -> Ollama:
        st = st or self.settings()
        return Ollama(st["ollamaUrl"], st["model"], timeout=300)

    def check_text(self, text: str) -> str:
        return studio.check_report(self.settings(), text)

    def plan_text(self) -> str:
        st = self.settings()
        now = scheduler.local_now(st.cfg)
        planned = plan.week(st.cfg, len(self.db.inbox_waiting()), self.db.recent_modes(2), now)
        return "THIS WEEK (planned, you approve each one)\n" + plan.render(planned, st["timezone"].split("/")[-1])

    def repurpose(self, source: str) -> str:
        if len(source.strip()) < 200:
            return "Paste a longer source, at least a few paragraphs."
        st = self.settings()
        angles = engage.repurpose(self.llm(st), dict(st.cfg, _rules=st.rules), source.strip())
        if not angles:
            return "No standalone angles found. Try a longer or more specific source."
        for a in angles:
            self.db.add_inbox(f"From something I wrote: {a}")
        return f"Saved {len(angles)} angle(s) to your inbox. They are used in the next scheduled posts, before news.\n\n" + "\n".join(f"- {a}" for a in angles)

    def carousel_post(self, topic: str) -> int:
        st = self.settings()
        data = engage.carousel(self.llm(st), dict(st.cfg, _rules=st.rules), topic)
        if not data["slides"]:
            raise RuntimeError("The model returned no slides. Try a more specific topic.")
        path = st.data_dir / "media" / f"{uuid.uuid4().hex}.pdf"
        if not engage.carousel_pdf(data, str(path)):
            raise RuntimeError("PDF export needs the reportlab package: pip install reportlab")
        pid = self.db.add_post(mode="carousel", topic_key=topic[:80], text=data["intro"] or data["title"], flags=[],
                               status="pending", source={"doc_path": str(path), "doc_title": data["title"], "mode": "carousel"})
        mid = self.tg.send_draft(self.db.get_post(pid))
        self.db.update_post(pid, tg_message_id=mid)
        return pid

    def llm_health(self):
        st = self.settings()
        return Ollama(st["ollamaUrl"], st["model"]).health()

    def posts_today(self, now: datetime | None = None) -> int:
        now = now or scheduler.local_now(self.settings().cfg)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        to_utc = lambda d: iso(d.astimezone(utcnow().tzinfo))
        return self.db.posted_between(to_utc(start), to_utc(start + timedelta(days=1)))

    def next_run(self) -> datetime | None:
        cfg = self.settings().cfg
        return scheduler.next_slot(scheduler.local_now(cfg), cfg)

    def status_text(self) -> str:
        nxt = self.next_run()
        paused = self.db.kv_get("paused", False)
        last = self.db.list_posts(1, "posted")
        li = self.li.status()
        return "\n".join([
            f"Schedule: {'PAUSED' if paused else ('next ' + nxt.strftime('%a %d %b, %H:%M') if nxt else 'no days set')}",
            f"Waiting for your decision: {len(self.db.pending())}",
            f"In your inbox: {len(self.db.inbox_waiting())}",
            f"Last post: {last[0]['posted_at'][:10] + ' (' + last[0]['mode'] + ')' if last else 'none yet'}",
            f"LinkedIn: {li['note']}",
        ])

    def remind_token_expiry(self, now: datetime):
        s = self.li.status()
        key = now.strftime("%Y-%m-%d")
        if s.get("connected") and s["days_left"] <= 7 and self.db.kv_get("expiry_warned") != key:
            self.db.kv_set("expiry_warned", key)
            self.tg.notify(f"LinkedIn sign-in expires in {s['days_left']} days. Open the dashboard and press Connect LinkedIn to renew it.")
