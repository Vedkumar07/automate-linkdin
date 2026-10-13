"""Telegram bot: your phone is the inbox and the approval screen. Only the owner's chat is ever answered."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from .config import Settings
from .db import DB
from .util import HttpError, http, log

HELP = (
    "Cadence\n\n"
    "Send me anything you want posted about: what you are working on, an opinion, a story. "
    "Send a photo (a caption helps) and I will look at it and write a post around it, with the photo attached. "
    "Your messages get priority over news.\n\n"
    "/draft  write a post now\n"
    "/status  what is queued and when the next post is\n"
    "/inbox  what is waiting\n"
    "/clear  empty the inbox\n"
    "/pause and /resume  stop or restart the schedule\n"
    "/plan  the coming week\n"
    "/check <draft>  score a draft: human-sounding, hook, preview\n\n"
    "To edit a draft, reply to it with the new text."
)


class Telegram:
    def __init__(self, st: Settings, db: DB):
        self.st, self.db = st, db
        self.token = st.secret("TELEGRAM_BOT_TOKEN")
        self.base = (st.secret("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")
        self.owner = str(st["ownerChatId"] or "")
        self.app = None  # set by the App so handlers can call back

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _call(self, method: str, payload: dict | None = None, timeout: float = 30):
        r = http("POST", f"{self.base}/bot{self.token}/{method}", json_body=payload or {}, timeout=timeout, retries=1).json()
        if not r.get("ok"):
            raise HttpError(0, str(r), method)
        return r["result"]

    # ---- sending ----
    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
        if not (self.enabled and self.owner):
            return 0
        payload: dict = {"chat_id": self.owner, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        try:
            return int(self._call("sendMessage", payload)["message_id"])
        except HttpError as e:
            log.warning("telegram send failed: %s", e)
            return 0

    def notify(self, text: str):
        self.send(text)

    def clear_buttons(self, message_id: int):
        if message_id and self.enabled:
            try:
                self._call("editMessageReplyMarkup", {"chat_id": self.owner, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
            except HttpError:
                pass

    def send_draft(self, post: dict) -> int:
        label = {"input": "from your input", "profile": "from your background", "news": "news take",
                 "thought": "reflection"}.get(post["mode"], post["mode"])
        head = f"Draft #{post['id']} ({label}{', with your photo' if post['photo_path'] else ''})\n\n"
        tail = ""
        if post["flags"]:
            tail += "\n\nQuality flags: " + "; ".join(post["flags"])
        if post["news_link"]:
            tail += f"\n\nSource: {post['news_link']}"
        tail += "\n\nReply to this message with new text to edit it."
        fid = post["source"].get("tg_file_id")
        if fid:  # show the picture the post will go out with
            try:
                self._call("sendPhoto", {"chat_id": self.owner, "photo": fid, "caption": f"Draft #{post['id']} will go out with this photo"})
            except HttpError as e:
                log.warning("could not preview photo: %s", e)
        return self.send(head + post["text"] + tail, [
            [("Post it", f"a:{post['id']}"), ("Skip", f"s:{post['id']}")],
            [("Different take", f"r:{post['id']}")]])

    def download_photo(self, file_id: str) -> str:
        info = self._call("getFile", {"file_id": file_id})
        data = http("GET", f"{self.base}/file/bot{self.token}/{info['file_path']}", timeout=60).body
        ext = Path(info["file_path"]).suffix or ".jpg"
        out = self.st.data_dir / "media" / f"{uuid.uuid4().hex}{ext}"
        out.write_bytes(data)
        return str(out)

    # ---- receiving ----
    def poll_forever(self, stop: threading.Event):
        if not self.enabled:
            log.info("telegram disabled (no TELEGRAM_BOT_TOKEN)")
            return
        offset = int(self.db.kv_get("tg_offset", 0))
        backoff = 2
        while not stop.is_set():
            try:
                updates = self._call("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]}, timeout=40)
                backoff = 2
            except Exception as e:  # network blips must never kill the bot
                log.warning("telegram poll error: %s", e)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                self.db.kv_set("tg_offset", offset)
                try:
                    self.handle(u)
                except Exception:
                    log.exception("error handling telegram update")

    def handle(self, u: dict):
        cq = u.get("callback_query")
        msg = u.get("message")
        chat = str((cq["message"]["chat"]["id"] if cq else (msg or {}).get("chat", {}).get("id", "")))
        if not self.owner:
            # first contact: tell the person their id so they can put it in .env
            if msg:
                self.owner = chat
                self.send(f"Your chat id is {chat}. Add TELEGRAM_OWNER_CHAT_ID={chat} to .env and restart Cadence so only you can use this bot.")
                self.owner = ""
            return
        if chat != self.owner:
            return
        if cq:
            self._on_button(cq)
        elif msg:
            self._on_message(msg)

    def _on_button(self, cq: dict):
        kind, _, pid = cq["data"].partition(":")
        mid = cq["message"]["message_id"]
        try:
            self._call("answerCallbackQuery", {"callback_query_id": cq["id"]})
        except HttpError:
            pass
        post = self.db.get_post(int(pid)) if pid.isdigit() else None
        ok = post and (post["status"] == "pending" or (kind == "a" and post["status"] == "failed"))
        self.clear_buttons(mid)
        if not ok:
            self.send("That draft is no longer waiting.")
            return
        fn = {"a": self.app.approve, "s": self.app.skip, "r": self.app.regenerate}[kind]
        threading.Thread(target=fn, args=(post["id"],), daemon=True).start()  # keep polling while the model or LinkedIn works

    def _on_message(self, m: dict):
        text = (m.get("caption") or m.get("text") or "").strip()
        photos = m.get("photo") or []
        if text.lower().startswith("/check"):
            body = text[6:].strip()
            self.send(self.app.check_text(body) if body else "Send /check followed by the draft text.")
            return
        if text.startswith("/"):
            return self._command(text.split()[0].lower().split("@")[0])
        reply_to = (m.get("reply_to_message") or {}).get("message_id")
        if reply_to and text and not photos:
            for p in self.db.pending():
                if p["tg_message_id"] == reply_to:
                    self.app.edit(p["id"], text)
                    return
        doc = m.get("document") or {}
        file_id = photos[-1]["file_id"] if photos else (doc.get("file_id") if str(doc.get("mime_type", "")).startswith("image/") else "")
        if not text and not file_id:
            self.send("Send text, or a photo (a caption helps: say what it is and what you want to say).")
            return
        path = ""
        if file_id:
            try:
                path = self.download_photo(file_id)
            except Exception as e:
                log.warning("photo download failed: %s", e)
                if not text:
                    self.send("I could not download that photo. Try sending it again.")
                    return
                self.send("Saved your words, but I could not download the photo. Try sending it again.")
        iid = self.db.add_inbox(text, path, file_id if path else "")
        if path:
            self.send("Got the photo. Looking at it now.")
            threading.Thread(target=self.app.handle_photo, args=(iid,), daemon=True).start()
            return
        n = len(self.db.inbox_waiting())
        self.send(f"Saved. {n} waiting. It gets priority over news in the next post.")

    def _command(self, cmd: str):
        a = self.app
        if cmd in ("/start", "/help"):
            self.send(HELP)
        elif cmd == "/status":
            self.send(a.status_text())
        elif cmd == "/inbox":
            items = self.db.inbox_waiting()
            self.send("Waiting:\n" + "\n".join(f"- {i['text'][:80]}" for i in items) if items else "Inbox is empty.")
        elif cmd == "/clear":
            self.send(f"Cleared {self.db.inbox_clear()} waiting item(s).")
        elif cmd == "/pause":
            self.db.kv_set("paused", True)
            self.send("Schedule paused. Nothing will be written until /resume.")
        elif cmd == "/resume":
            self.db.kv_set("paused", False)
            self.send("Schedule running again.")
        elif cmd == "/plan":
            self.send(a.plan_text())
        elif cmd == "/draft":
            self.send("Writing. This takes a minute on your laptop.")
            threading.Thread(target=a.generate_safe, kwargs={"manual": True}, daemon=True).start()
        else:
            self.send(HELP)
