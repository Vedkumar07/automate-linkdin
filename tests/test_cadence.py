"""Offline tests. A fake server plays Ollama, Telegram, LinkedIn and two RSS feeds, so nothing touches the network."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cadence import news, quality, scheduler, web, writer  # noqa: E402
from cadence.config import Settings  # noqa: E402
from cadence.db import DB  # noqa: E402
from cadence.service import App  # noqa: E402

SENTS = [
    "I broke the build at 2am last Tuesday and spent an hour blaming the cache.",
    "It was a trailing space in an environment variable, which is the kind of bug that makes you question your whole career choice.",
    "Fixed in four seconds.",
    "Finding it took the other fifty nine minutes, two coffees and a very honest conversation with myself about logging.",
    "My teammate hit the same thing last year and I laughed at him.",
    "Nobody is laughing now, least of all me.",
    "The tool I trusted printed the variable without the space and made me feel stupid.",
    "Logs lie by omission.",
    "Anyone else keep a graveyard of bugs like this one?",
]
GOOD = " ".join(SENTS)
RSS = '<?xml version="1.0"?><rss><channel>{}</channel></rss>'
ATOM = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{}</feed>'


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Fake:
    """One server pretending to be everything."""

    def __init__(self):
        self.chat, self.calls, self.tg_sent = [], [], []
        self.li_status = 201
        self.missing = set()
        self.mid = 100
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _reply(self, obj=None, status=200, raw=None, headers=None):
                data = raw if raw is not None else json.dumps(obj if obj is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                p = self.path
                outer.calls.append(("GET", p))
                if p == "/api/tags":
                    return self._reply({"models": [{"name": "qwen2.5:7b"}]})
                if p == "/v2/userinfo":
                    return self._reply({"sub": "abc123", "name": "Ved Kumar"})
                if p == "/feed1.xml":
                    return self._reply(raw=RSS.format("".join(
                        f"<item><title>Story one {i} &amp; more</title><link>https://a.test/{i}</link></item>" for i in range(5))).encode())
                if p == "/feed2.xml":
                    return self._reply(raw=ATOM.format("".join(
                        f'<entry><title>Atom story {i}</title><link href="https://b.test/{i}"/></entry>' for i in range(5))).encode())
                if p.startswith("/file/bot"):
                    return self._reply(raw=b"\xff\xd8fakejpeg")
                self._reply({}, 404)

            def do_PUT(self):
                outer.calls.append(("PUT", self.path, len(self._body())))
                self._reply({})

            def do_POST(self):
                body = self._body()
                p = self.path
                if p == "/api/chat":
                    req = json.loads(body)
                    outer.calls.append(("chat", req["messages"][-1]["content"], req["model"], len(req["messages"][-1].get("images", []))))
                    if req["model"] in outer.missing:
                        return self._reply({"error": "model not found"}, 404)
                    if not outer.chat:
                        return self._reply({"error": "queue empty"}, 500)
                    item = outer.chat.pop(0)
                    return self._reply({"message": {"content": item if isinstance(item, str) else json.dumps(item)}})
                if p.startswith("/bot"):
                    method = p.rsplit("/", 1)[1]
                    payload = json.loads(body or b"{}")
                    outer.calls.append(("tg", method, payload))
                    if method == "sendMessage":
                        outer.mid += 1
                        outer.tg_sent.append(payload)
                        return self._reply({"ok": True, "result": {"message_id": outer.mid}})
                    if method == "getFile":
                        return self._reply({"ok": True, "result": {"file_path": "photos/x.jpg"}})
                    if method == "getMe":
                        return self._reply({"ok": True, "result": {"username": "cadence_test_bot"}})
                    return self._reply({"ok": True, "result": True})
                if p == "/oauth/v2/accessToken":
                    outer.calls.append(("token", body.decode()))
                    return self._reply({"access_token": "tok123", "expires_in": 5184000})
                if p.startswith("/rest/documents"):
                    outer.calls.append(("docinit", p))
                    return self._reply({"value": {"uploadUrl": f"http://127.0.0.1:{outer.port}/upload", "document": "urn:li:document:55"}})
                if p.startswith("/rest/images"):
                    return self._reply({"value": {"uploadUrl": f"http://127.0.0.1:{outer.port}/upload", "image": "urn:li:image:9"}})
                if p == "/rest/posts":
                    outer.calls.append(("post", json.loads(body), dict(self.headers)))
                    if outer.li_status == 201:
                        return self._reply({}, 201, headers={"x-restli-id": "urn:li:share:777"})
                    return self._reply({"message": "nope"}, outer.li_status)
                self._reply({}, 404)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}"

    def close(self):
        if not getattr(self, "closed", False):
            self.closed = True
            self.srv.shutdown()
            self.srv.server_close()

    def posts(self):
        return [c for c in self.calls if c[0] == "post"]


def wait_for(fn, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.st = Settings.load(ROOT)
        self.pat = quality.banned_patterns(self.st["banned"])

    def test_good_post_passes(self):
        self.assertEqual(quality.check(GOOD, self.st.cfg, self.pat), [])

    def test_flags_the_usual_ai_tells(self):
        bad = "Excited to share a game-changer \U0001F680 — it's not just a tool, it's a revolution. #a #b #c\n- one\n- two"
        issues = " | ".join(quality.check(bad, self.st.cfg, self.pat))
        for needle in ("too short", "game-changer", "emoji", "hashtags", "greeting", "not just", "list"):
            self.assertIn(needle, issues)

    def test_flags_flat_rhythm_and_generic_closer(self):
        flat = " ".join(["This is a sentence that has about ten words in it."] * 12)
        self.assertIn("same length", " | ".join(quality.check(flat, self.st.cfg, self.pat)))
        closer = GOOD.rsplit(".", 1)[0] + ". What do you think?"
        self.assertIn("generic engagement closer", " | ".join(quality.check(closer, self.st.cfg, self.pat)))

    def test_clean_removes_em_dash_and_wrapper(self):
        self.assertEqual(quality.clean('Here is your post:\n\n"Hello — world"'), "Hello, world")

    def test_little_text_escapes_markup(self):
        self.assertEqual(quality.little_text("a (b) [c] {d} @e #tag # x"), "a \\(b\\) \\[c\\] \\{d\\} \\@e #tag \\# x")


class ScheduleTests(unittest.TestCase):
    cfg = {"schedule": {"days": [1, 3, 5], "hour": 9, "minute": 0}, "catchUpHours": 6, "timezone": "Asia/Kolkata"}
    tz = ZoneInfo("Asia/Kolkata")

    def at(self, d, h, m=0):
        return datetime(2026, 10, d, h, m, tzinfo=self.tz)  # 12 Oct 2026 is a Monday

    def test_due_after_slot_only_once(self):
        slot = self.at(12, 9).isoformat()
        self.assertIsNone(scheduler.due_slot(self.at(12, 8, 59), self.cfg, None))
        self.assertEqual(scheduler.due_slot(self.at(12, 9, 1), self.cfg, None).isoformat(), slot)
        self.assertIsNone(scheduler.due_slot(self.at(12, 9, 1), self.cfg, slot))

    def test_catch_up_window(self):
        self.assertIsNotNone(scheduler.due_slot(self.at(12, 14, 30), self.cfg, None))
        self.assertIsNone(scheduler.due_slot(self.at(12, 15, 30), self.cfg, None))

    def test_not_due_on_other_days(self):
        self.assertIsNone(scheduler.due_slot(self.at(13, 10), self.cfg, None))  # Tuesday

    def test_next_slot_and_sunday_zero(self):
        self.assertEqual(scheduler.next_slot(self.at(12, 10), self.cfg), self.at(14, 9))
        sun = dict(self.cfg, schedule={"days": [0], "hour": 8, "minute": 30})
        self.assertEqual(scheduler.next_slot(self.at(12, 10), sun), self.at(18, 8, 30))


class NewsTests(unittest.TestCase):
    def test_parses_rss_and_atom(self):
        r = news.parse_feed(RSS.format("<item><title><![CDATA[A &amp; B]]></title><link>https://x.test/1</link></item>").encode(), "S")
        self.assertEqual(r[0]["title"], "A & B")
        a = news.parse_feed(ATOM.format('<entry><title>T</title><link href="https://y.test/2"/></entry>').encode(), "S")
        self.assertEqual(a[0]["link"], "https://y.test/2")
        self.assertEqual(news.parse_feed(b"not xml", "S"), [])


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.tmp = Path(tempfile.mkdtemp())
        for f in ("voice.md", "profile.md", "stories.md"):
            shutil.copy(ROOT / "templates" / f, self.tmp / f)
        (self.tmp / "voice.md").write_text("# Voice\n## Rules\nWrite plainly.\n## My real writing samples\n" + GOOD + "\n")
        (self.tmp / "profile.md").write_text("- I moved a team's deploys from manual SSH to a pipeline and broke production on the first Friday.\n")
        cfg = json.loads((ROOT / "templates" / "config.json").read_text())
        cfg.update(ollamaUrl=self.fake.url, ownerChatId="42", autoPost=False,
                   feeds=[{"name": "One", "url": self.fake.url + "/feed1.xml"}, {"name": "Two", "url": self.fake.url + "/feed2.xml"}],
                   web={"host": "127.0.0.1", "port": free_port()})
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        (self.tmp / ".env").write_text("TELEGRAM_BOT_TOKEN=t0k\nLINKEDIN_CLIENT_ID=cid\nLINKEDIN_CLIENT_SECRET=sec\n")
        os.environ.update(TELEGRAM_API_BASE=self.fake.url, LINKEDIN_API_BASE=self.fake.url, LINKEDIN_AUTH_BASE=self.fake.url)
        self.app = App(self.tmp)
        self.app.db.kv_set("linkedin_token", {"access_token": "tok", "sub": "abc123", "name": "Ved Kumar", "expires_at": "2099-01-01T00:00:00Z"})

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k in ("TELEGRAM_API_BASE", "LINKEDIN_API_BASE", "LINKEDIN_AUTH_BASE"):
            os.environ.pop(k, None)

    def button(self, kind, pid):
        self.app.tg.handle({"update_id": 1, "callback_query": {
            "id": "c", "data": f"{kind}:{pid}", "message": {"message_id": 5, "chat": {"id": 42}}}})


class PipelineTests(Base):
    def test_mode_priority_and_no_triple_repeat(self):
        st = self.app.settings()
        self.assertIn(writer.choose_mode(st, self.app.db), ("profile", "news", "thought"))
        self.app.db.add_inbox("I spent the week on a flaky test")
        self.assertEqual(writer.choose_mode(st, self.app.db), "input")

    def test_thought_post_end_to_end_with_approval(self):
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(manual=True, mode="thought")
        post = self.app.db.get_post(pid)
        self.assertEqual(post["status"], "pending")
        sent = self.fake.tg_sent[-1]
        self.assertIn("Draft #", sent["text"])
        self.assertEqual([b["callback_data"] for row in sent["reply_markup"]["inline_keyboard"] for b in row],
                         [f"a:{pid}", f"s:{pid}", f"r:{pid}"])
        self.button("a", pid)
        self.assertTrue(wait_for(lambda: self.app.db.get_post(pid)["status"] == "posted"))
        body = self.fake.posts()[0][1]
        self.assertEqual(body["author"], "urn:li:person:abc123")
        self.assertIn("trailing space", body["commentary"])
        self.assertEqual({k.lower(): v for k, v in self.fake.posts()[0][2].items()}["linkedin-version"], "202510")
        self.assertIn("Posted to LinkedIn", self.fake.tg_sent[-1]["text"])

    def test_double_tap_posts_once(self):
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(manual=True, mode="thought")
        threads = [threading.Thread(target=self.app.approve, args=(pid,)) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.fake.posts()), 1)

    def test_rewrite_loop_uses_failures_as_feedback(self):
        self.fake.chat += [{"post": "Excited to share a game-changer."}, {"post": GOOD}]
        pid = self.app.generate(manual=True, mode="thought")
        self.assertEqual(self.app.db.get_post(pid)["flags"], [])
        second_prompt = [c for c in self.fake.calls if c[0] == "chat"][1][1]
        self.assertIn("rejected for", second_prompt)
        self.assertIn("banned phrase", second_prompt)

    def test_still_flagged_after_retries_is_shown_with_flags(self):
        self.fake.chat += [{"post": "Excited to share a game-changer."}] * 3
        pid = self.app.generate(manual=True, mode="thought")
        self.assertTrue(self.app.db.get_post(pid)["flags"])
        self.assertIn("Quality flags", self.fake.tg_sent[-1]["text"])

    def test_news_story_is_picked_and_remembered(self):
        self.fake.chat += [{"pick": 2, "angle": "This is absurd."}, {"post": GOOD}]
        pid = self.app.generate(manual=True, mode="news")
        post = self.app.db.get_post(pid)
        self.assertTrue(post["news_link"].startswith("https://"))
        self.app.approve(pid)
        self.assertIn(post["news_link"], self.app.db.used_keys("news"))
        # the used headline is never offered again
        heads = news.fetch_headlines(self.app.st["feeds"], self.app.db.used_keys("news"), count=50)
        self.assertNotIn(post["news_link"], [h["link"] for h in heads])

    def test_inbox_with_photo_uploads_image_and_is_consumed(self):
        photo = self.tmp / "data" / "media" / "p.jpg"
        photo.write_bytes(b"jpegbytes")
        iid = self.app.db.add_inbox("Shipped the migration, here is the dashboard", str(photo))
        self.fake.chat.append({"post": GOOD_NODIGIT})
        pid = self.app.generate(manual=True)
        self.assertEqual(self.app.db.get_post(pid)["mode"], "input")
        self.app.approve(pid)
        self.assertEqual(self.fake.posts()[0][1]["content"]["media"]["id"], "urn:li:image:9")
        self.assertTrue(any(c[0] == "PUT" for c in self.fake.calls))
        self.assertEqual(self.app.db.inbox_waiting(), [])

    def test_skipping_input_twice_retires_it(self):
        iid = self.app.db.add_inbox("a half baked idea about caching")
        for _ in range(2):
            self.fake.chat.append({"post": GOOD_NODIGIT})
            pid = self.app.generate(manual=True)
            self.app.skip(pid)
        self.assertEqual(self.app.db.inbox_waiting(), [])

    def test_linkedin_failure_keeps_draft_and_offers_retry(self):
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(manual=True, mode="thought")
        self.fake.li_status = 401
        self.assertFalse(self.app.approve(pid))
        p = self.app.db.get_post(pid)
        self.assertEqual(p["status"], "failed")
        self.assertIn("Connect LinkedIn", p["error"])
        self.fake.li_status = 201
        self.app.db.kv_set("linkedin_token", {"access_token": "tok", "sub": "abc123", "name": "V", "expires_at": "2099-01-01T00:00:00Z"})
        self.button("a", pid)  # "Try again"
        self.assertTrue(wait_for(lambda: self.app.db.get_post(pid)["status"] == "posted"))

    def test_regenerate_gives_a_new_take_and_keeps_topic(self):
        self.fake.chat += [{"post": GOOD}, {"post": GOOD.replace("Tuesday", "Thursday")}]
        pid = self.app.generate(manual=True, mode="profile")
        self.button("r", pid)
        self.assertTrue(wait_for(lambda: any(p["id"] != pid for p in self.app.db.pending())))
        new = [p for p in self.app.db.pending() if p["id"] != pid][0]
        self.assertIn("Thursday", new["text"])
        self.assertEqual(new["topic_key"], self.app.db.get_post(pid)["topic_key"])
        self.assertIn("Do not reuse its sentences", [c for c in self.fake.calls if c[0] == "chat"][-1][1])

    def test_edit_by_replying_in_telegram_rechecks_quality(self):
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(manual=True, mode="thought")
        mid = self.app.db.get_post(pid)["tg_message_id"]
        self.app.tg.handle({"update_id": 2, "message": {"chat": {"id": 42}, "text": "Short and thrilled to share this.", "reply_to_message": {"message_id": mid}}})
        p = self.app.db.get_post(pid)
        self.assertEqual(p["text"], "Short and thrilled to share this.")
        self.assertTrue(p["flags"])

    def test_auto_post_clean_non_news_but_never_news_or_flagged(self):
        cfg = json.loads((self.tmp / "config.json").read_text())
        cfg["autoPost"] = True
        (self.tmp / "config.json").write_text(json.dumps(cfg))
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(mode="thought")
        self.assertEqual(self.app.db.get_post(pid)["status"], "posted")
        self.fake.chat += [{"pick": 1, "angle": "x"}, {"post": GOOD}]
        pid2 = self.app.generate(mode="news")
        self.assertEqual(self.app.db.get_post(pid2)["status"], "pending")
        self.fake.chat += [{"post": "thrilled."}] * 3
        pid3 = self.app.generate(mode="thought")
        self.assertEqual(self.app.db.get_post(pid3)["status"], "pending")

    def test_new_draft_expires_the_old_one(self):
        self.fake.chat += [{"post": GOOD}, {"post": GOOD}]
        a = self.app.generate(manual=True, mode="thought")
        b = self.app.generate(manual=True, mode="thought")
        self.assertEqual(self.app.db.get_post(a)["status"], "expired")
        self.assertEqual(self.app.db.get_post(b)["status"], "pending")

    def test_strangers_are_ignored_and_photo_without_caption_is_explained(self):
        before = len(self.fake.tg_sent)
        self.app.tg.handle({"update_id": 3, "message": {"chat": {"id": 999}, "text": "hello"}})
        self.assertEqual(len(self.fake.tg_sent), before)
        self.assertEqual(self.app.db.inbox_waiting(), [])
        self.app.tg.handle({"update_id": 4, "message": {"chat": {"id": 42}, "photo": [{"file_id": "f"}]}})
        self.assertTrue(wait_for(lambda: any("nothing to write from" in m["text"] for m in self.fake.tg_sent), 10))

    def test_inbox_message_with_photo_downloads_it(self):
        self.app.tg.handle({"update_id": 5, "message": {"chat": {"id": 42}, "caption": "my new desk setup", "photo": [{"file_id": "a"}, {"file_id": "b"}]}})
        item = self.app.db.inbox_waiting()[0]
        self.assertTrue(Path(item["photo_path"]).exists())

    def test_model_down_gives_a_plain_message(self):
        self.fake.close()
        r = self.app.generate_safe(manual=True, mode="thought")
        self.assertIsNone(r)


VISION = {"kind": "screenshot", "subject": "A Grafana dashboard with a red latency graph", "details": ["red line spiking", "dark theme"],
          "visible_text": "p99 latency 840 ms", "alt": "Grafana dashboard showing a latency spike"}
GOOD_NODIGIT = GOOD.replace("at 2am last Tuesday", "late last Tuesday")
GOOD_PHOTO = GOOD.replace("at 2am last Tuesday", "at 840 ms last Tuesday")  # only numbers the user gave may appear
PHOTO_UPDATE = {"update_id": 9, "message": {"chat": {"id": 42}, "caption": "the graph that ruined my afternoon", "photo": [{"file_id": "small"}, {"file_id": "big"}]}}


class PhotoTests(Base):
    def chats(self):
        return [c for c in self.fake.calls if c[0] == "chat"]

    def test_photo_is_analysed_then_drafted_and_posted_with_alt_text(self):
        self.fake.chat += [VISION, {"post": GOOD_PHOTO}]
        self.app.tg.handle(PHOTO_UPDATE)
        self.assertTrue(wait_for(lambda: len(self.app.db.pending()) == 1, 10))
        vision_call, writer_call = self.chats()[0], self.chats()[1]
        self.assertEqual((vision_call[2], vision_call[3]), ("gemma3:4b", 1))     # vision model, with the image
        self.assertEqual((writer_call[2], writer_call[3]), ("qwen2.5:7b", 0))    # writer model, text only
        self.assertIn("Grafana dashboard", writer_call[1])
        self.assertIn("the graph that ruined my afternoon", writer_call[1])
        texts = [m["text"] for m in self.fake.tg_sent]
        self.assertTrue(any(t.startswith("What I see: A Grafana") for t in texts))
        self.assertTrue(any(c[0] == "tg" and c[1] == "sendPhoto" and c[2]["photo"] == "big" for c in self.fake.calls))
        pid = self.app.db.pending()[0]["id"]
        self.assertEqual(self.app.db.get_post(pid)["source"]["tg_file_id"], "big")
        self.app.approve(pid)
        body = self.fake.posts()[0][1]
        self.assertEqual(body["content"]["media"]["altText"], "Grafana dashboard showing a latency spike")
        self.assertEqual(self.app.db.inbox_waiting(), [])

    def test_photo_without_caption_is_flagged_for_the_missing_story(self):
        upd = {"update_id": 10, "message": {"chat": {"id": 42}, "photo": [{"file_id": "x"}]}}
        self.fake.chat += [VISION, {"post": GOOD_PHOTO}]
        self.app.tg.handle(upd)
        self.assertTrue(wait_for(lambda: len(self.app.db.pending()) == 1, 10))
        self.assertTrue(any("no caption" in f for f in self.app.db.pending()[0]["flags"]))
        self.assertIn("I gave no caption", self.chats()[1][1])

    def test_image_sent_as_a_file_is_accepted(self):
        upd = {"update_id": 11, "message": {"chat": {"id": 42}, "caption": "my desk", "document": {"file_id": "d", "mime_type": "image/png"}}}
        self.fake.chat += [VISION, {"post": GOOD_PHOTO}]
        self.app.tg.handle(upd)
        self.assertTrue(wait_for(lambda: len(self.app.db.pending()) == 1, 10))

    def test_vision_model_missing_falls_back_to_the_caption(self):
        self.fake.missing = {"gemma3:4b"}
        self.fake.chat += [{"post": GOOD_NODIGIT}]
        self.app.tg.handle(PHOTO_UPDATE)
        self.assertTrue(wait_for(lambda: len(self.app.db.pending()) == 1, 10))
        self.assertTrue(any("ollama pull gemma3:4b" in m["text"] for m in self.fake.tg_sent))
        self.assertNotIn("What the photo shows", self.chats()[-1][1])

    def test_vision_model_missing_and_no_caption_stops_politely(self):
        self.fake.missing = {"gemma3:4b"}
        self.app.tg.handle({"update_id": 12, "message": {"chat": {"id": 42}, "photo": [{"file_id": "x"}]}})
        self.assertTrue(wait_for(lambda: any("nothing to write from" in m["text"] for m in self.fake.tg_sent), 10))
        self.assertEqual(self.app.db.pending(), [])
        self.assertEqual(self.app.db.inbox_waiting(), [])

    def test_invented_numbers_are_flagged_but_given_ones_are_not(self):
        facts = "the graph, p99 latency 840 ms"
        st = Settings.load(ROOT)
        pat = quality.banned_patterns(st["banned"])
        ok = quality.check(GOOD.replace("2am", "840 ms"), st.cfg, pat, facts=facts)
        self.assertEqual([i for i in ok if "not in what you gave" in i], [])
        bad = quality.check(GOOD.replace("2am", "37 percent"), st.cfg, pat, facts=facts)
        self.assertTrue(any('"37"' in i for i in bad))

    def test_invisible_characters_are_stripped(self):
        self.assertEqual(quality.clean("he\u200bllo\ufeff world"), "hello world")

    def test_new_banned_phrases_trigger_the_gate(self):
        st = Settings.load(ROOT)
        pat = quality.banned_patterns(st["banned"])
        self.assertTrue(any("realm" in i for i in quality.check("Deep in the realm of caching " + GOOD, st.cfg, [p for p in pat if "realm" in p[0]])))


class OAuthAndDashboardTests(Base):
    def start_dash(self):
        self.stop = threading.Event()
        threading.Thread(target=web.serve, args=(self.app, self.stop), daemon=True).start()
        self.base = f"http://127.0.0.1:{self.app.settings()['web']['port']}"
        self.assertTrue(wait_for(lambda: self.get("/")[0] == 200))
        self.addCleanup(self.stop.set)

    def get(self, path, headers=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        try:
            r = op.open(urllib.request.Request(self.base + path, headers=headers or {}), timeout=5)
            return r.status, r.read().decode(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), dict(e.headers)
        except OSError:
            return 0, "", {}

    def post(self, path, data, headers=None):
        from urllib.parse import urlencode
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        try:
            r = op.open(urllib.request.Request(self.base + path, urlencode(data).encode(), headers=headers or {}), timeout=5)
            return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)

    def test_dashboard_renders_and_blocks_foreign_hosts_and_missing_csrf(self):
        self.start_dash()
        s, body, h = self.get("/")
        self.assertIn("Cadence", body)
        self.assertIn("Needs your decision", body)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(self.get("/", {"Host": "evil.example.com"})[0], 403)
        self.assertEqual(self.post("/inbox", {"text": "x"})[0], 403)
        token = web.Handler.dash.token
        s, hd = self.post("/inbox", {"csrf": token, "text": "a real idea"})
        self.assertEqual(s, 303)
        self.assertEqual(self.app.db.inbox_waiting()[0]["text"], "a real idea")

    def test_dashboard_shows_draft_and_voice_page_saves_samples(self):
        self.start_dash()
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(manual=True, mode="thought")
        body = self.get("/")[1]
        self.assertIn("trailing space", body)
        self.assertIn(f"/decide/{pid}", body)
        token = web.Handler.dash.token
        self.post("/voice", {"csrf": token, "rules": "- Write plainly.", "samples": "First sample here.\n---\nSecond sample here."})
        self.assertEqual(len(Settings.load(self.tmp).samples), 2)
        self.assertIn("First sample", self.get("/voice")[1])

    def test_edit_and_post_from_dashboard(self):
        self.start_dash()
        self.fake.chat.append({"post": GOOD})
        pid = self.app.generate(manual=True, mode="thought")
        token = web.Handler.dash.token
        self.post(f"/decide/{pid}", {"csrf": token, "do": "post", "text": GOOD.replace("Tuesday", "Friday")})
        self.assertTrue(wait_for(lambda: self.app.db.get_post(pid)["status"] == "posted"))
        self.assertIn("Friday", self.fake.posts()[0][1]["commentary"])

    def test_oauth_flow_stores_token_and_checks_state(self):
        self.start_dash()
        self.app.db.kv_set("linkedin_token", None)
        s, _, h = self.get("/connect")
        self.assertEqual(s, 303)
        self.assertIn("/oauth/v2/authorization", h["Location"])
        self.assertIn("w_member_social", h["Location"])
        import urllib.parse as up
        state = up.parse_qs(up.urlparse(h["Location"]).query)["state"][0]
        s, _, h = self.get("/oauth/callback?code=abc&state=forged")
        self.assertIn("msg=", h["Location"])
        self.assertFalse(self.app.li.status()["connected"])
        s, _, h = self.get(f"/oauth/callback?code=abc&state={state}")
        self.assertIn("connected", up.unquote(h["Location"]).lower())
        st = self.app.li.status()
        self.assertTrue(st["connected"])
        self.assertEqual(st["name"], "Ved Kumar")
        tokcall = [c for c in self.fake.calls if c[0] == "token"][0][1]
        self.assertIn("grant_type=authorization_code", tokcall)

    def test_media_route_serves_photos_and_blocks_traversal(self):
        self.start_dash()
        (self.tmp / "data" / "media" / "a.jpg").write_bytes(b"jpgdata")
        (self.tmp / "secret.txt").write_text("nope")
        s, body, h = self.get("/media/a.jpg")
        self.assertEqual((s, h["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(self.get("/media/..%2f..%2fsecret.txt")[0], 404)
        self.assertEqual(self.get("/media/missing.jpg")[0], 404)

    def test_expiry_reminder_once_a_day(self):
        self.app.db.kv_set("linkedin_token", {"access_token": "t", "sub": "s", "name": "V",
                                              "expires_at": (datetime.utcnow().replace(microsecond=0)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        from datetime import timedelta
        exp = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.app.db.kv_set("linkedin_token", {"access_token": "t", "sub": "s", "name": "V", "expires_at": exp})
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        self.app.remind_token_expiry(now)
        self.app.remind_token_expiry(now)
        self.assertEqual(len([m for m in self.fake.tg_sent if "expires" in m["text"]]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
