"""Tests for the Studio tools: humanizer, detector, hooks, captions, beats, analytics, profile audit,
plan, engage, carousel and repurposing. Run after test_cadence.py:  python3 tests/test_tools.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from cadence import analytics, beats, caption, detect, engage, hooks, humanize, plan, profile_audit, quality, studio, writer  # noqa: E402
from cadence.config import Settings  # noqa: E402
from cadence.llm import Ollama  # noqa: E402
from test_cadence import GOOD, Base, wait_for  # noqa: E402

AI_TEXT = ("In today's fast-paced world, it's worth noting that leverage is a game-changer. "
           "Excited to share a robust, seamless solution — it's not just a tool, it's a journey. "
           "Let's dive in! Here's the thing: our holistic paradigm unlocks next level value. "
           "Thoughts? Stop scrolling and follow for more.")


class HumanizerTests(unittest.TestCase):
    def test_removes_invisible_characters_and_reports_it(self):
        out, changes = humanize.clean("he​llo⁠ world again")
        self.assertEqual(out, "hello world again")
        self.assertIn("removed invisible characters", changes)

    def test_typography(self):
        out, changes = humanize.clean("It — honestly — works – mostly. “Yes” it’s …")
        self.assertNotIn("—", out)
        self.assertNotIn("“", out)
        self.assertIn("em dash to comma", changes)

    def test_stock_word_swaps_keep_capitals(self):
        out, changes = humanize.clean("Leverage it. We utilize tools, and it is seamless.")
        self.assertIn("Use it", out)
        self.assertIn("use tools", out)
        self.assertIn("smooth", out)

    def test_flags_phrases_without_editing_them(self):
        hits = humanize.flagged_phrases(AI_TEXT)
        self.assertIn("in today's fast-paced", hits)
        self.assertIn("let's dive in", hits)
        self.assertIn("stop scrolling", hits)
        self.assertIn("follow for more", hits)


class DetectorTests(unittest.TestCase):
    def test_natural_post_scores_human(self):
        self.assertGreaterEqual(detect.score(GOOD)["human"], 60)

    def test_ai_style_text_scores_clearly_lower(self):
        good = detect.score(GOOD)["human"]
        bad = detect.score(AI_TEXT)
        self.assertLess(bad["human"], good - 15)
        self.assertTrue(bad["tells"])
        self.assertIn(bad["weakest"], bad["checks"])

    def test_structural_tells(self):
        tells = detect.structural_tells("Speed, cost, and scale matter. Quality, trust, and focus too.\nSTOP, THINK, SHIP now.")
        self.assertIn("several rule-of-three lists", tells)
        self.assertIn("three or more shouted words", tells)

    def test_em_dash_hurts_fingerprint(self):
        clean = detect.score(GOOD)["checks"]["fingerprint"]
        dashed = detect.score(GOOD.replace("cache.", "cache — really."))["checks"]["fingerprint"]
        self.assertLess(dashed, clean)


class HookTests(unittest.TestCase):
    def test_greeting_is_a_dealbreaker(self):
        r = hooks.score("Hey everyone, today I want to talk about caching.")
        self.assertIsNotNone(r["dealbreaker"])
        self.assertLessEqual(r["score"], 30)

    def test_concrete_confession_scores_well(self):
        r = hooks.score("I broke production at 2am on Friday and it cost us a customer.")
        self.assertGreaterEqual(r["score"], 60)
        self.assertEqual(r["formula"], "Confession")

    def test_formula_classification(self):
        self.assertEqual(hooks.classify("Nobody tells you that on-call is the job."), "Nobody Tells You")
        self.assertEqual(hooks.classify("I used to think more alerts meant safer systems."), "The Reversal")
        self.assertEqual(hooks.classify("Plain sentence about nothing in particular."), "")

    def test_rank_orders_best_first(self):
        ranked = hooks.rank(["Hey all, a quick note.", "I lost $4,000 when our staging database went live by mistake."])
        self.assertGreater(ranked[0]["score"], ranked[-1]["score"])
        self.assertTrue(ranked[0]["text"].startswith("I lost"))


class CaptionTests(unittest.TestCase):
    cfg = {"previewChars": 60, "maxHashtags": 2, "hookMin": 55}

    def test_preview_cuts_on_a_word_and_says_see_more(self):
        box = caption.preview_box("word " * 40, 60)
        self.assertIn("... see more", box)
        self.assertIn("WHAT THE FEED SHOWS", box)

    def test_lint_flags_too_many_hashtags(self):
        results = dict((n, s) for s, n, _ in caption.lint("I broke it at 2am. Read this #a #b #c", self.cfg))
        self.assertEqual(results["HASHTAGS"], "WARN")
        self.assertEqual(results["LENGTH"], "PASS")

    def test_search_terms(self):
        rows = caption.lint("Docker volumes are great.", self.cfg, keywords=["docker", "kubernetes"])
        self.assertEqual([r for r in rows if r[1] == "SEARCH TERMS"][0][0], "WARN")


class BeatTests(unittest.TestCase):
    def test_empty_script(self):
        self.assertEqual(beats.render("   "), "Nothing to time.")

    def test_timeline_and_notes(self):
        script = "Stop. " + ("This is a long rambling sentence with many words that keeps going " * 4) + "\nShort line here.\nNice."
        b, notes = beats.timeline(script, target=30)
        self.assertEqual(b[0]["role"], "HOOK")
        self.assertTrue(any("Hook" in n or "runs" in n for n in notes) or any("Beat" in n for n in notes))
        out = beats.render(script)
        self.assertIn("BEAT SHEET", out)

    def test_no_loop_warning(self):
        _, notes = beats.timeline("I lost a week to caching.\nDocker is fine.\nPlanting trees is nice.\nGoodbye.")
        self.assertTrue(any("No loop" in n for n in notes))


class AnalyticsTests(unittest.TestCase):
    CSV = """Text,Impressions,Reactions,Comments,Reposts,Account
"I lost a week to a flaky test. Here is what fixed it.",9000,300,40,25,me
"Quick tip about Docker volumes.",2000,20,2,1,me
"We removed our monorepo and deploys got calmer, 3 numbers to prove it.",12000,500,80,60,me
"Plain update with nothing in it at all here.",1000,5,0,0,me
"Unpopular opinion: most standups should be five minutes.",7000,250,30,20,me
"Our migration was a disaster, and I broke it on purpose 2 times.",8000,400,60,40,me
"A thing from a small account.",400,80,5,2,small
"Another thing from a small account.",380,70,4,1,small
"Third thing from a small account, quite normal.",420,60,3,1,small
"Fourth thing from a small account.",390,90,6,3,small
"Fifth thing from a small account.",410,75,5,2,small
"Sixth thing from a small account.",405,68,4,2,small"""

    def test_parse_and_multiples_per_account(self):
        rows = analytics.parse_csv(self.CSV)
        self.assertEqual(len(rows), 12)
        ranked = analytics.ranked(rows)
        self.assertEqual(ranked[0]["multiple"], max(r["multiple"] for r in ranked))
        self.assertTrue(all("multiple" in r for r in ranked))

    def test_report_and_comparison(self):
        out = analytics.report(analytics.parse_csv(self.CSV), "MY POSTS")
        self.assertIn("MY POSTS", out)
        self.assertIn("Top third vs bottom third", out)

    def test_needs_enough_rows(self):
        self.assertIn("Need at least 6", analytics.what_separates(analytics.ranked(analytics.parse_csv("text\na\nb")))[0])


class ProfileTests(unittest.TestCase):
    def test_empty_profile_has_fixes_by_points(self):
        r = profile_audit.audit({})
        self.assertEqual(r["score"], 0)
        points = [f["points"] for f in r["fixes"]]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_strong_profile_scores_high(self):
        r = profile_audit.audit({
            "headline": "DevOps engineer | Kubernetes, Terraform, CI/CD for 40 services",
            "about": "I run platform infrastructure for a fintech. We cut deploys from 2 hours to 9 minutes across 40 services, and I still get paged. " * 3,
            "experience": "Cut build time from 14 min to 4 min. Ran 3 on-call rotations for 12 engineers.",
            "skills": 8, "featured": True, "banner": True, "photo": True, "recent_post": True})
        self.assertGreaterEqual(r["score"], 90)

    def test_buzzword_headline_fails(self):
        r = profile_audit.audit({"headline": "Passionate DevOps guru and ninja"})
        self.assertNotIn("Headline avoids buzzwords", r["passed"])


class PlanTests(unittest.TestCase):
    def test_inbox_first_and_no_triple_repeats(self):
        cfg = json.loads((ROOT / "templates" / "config.json").read_text())
        cfg["schedule"] = {"days": [1, 2, 3, 4, 5], "hour": 9, "minute": 0}
        cfg["timezone"] = "Asia/Kolkata"
        import random
        start = datetime(2026, 10, 12, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        planned = plan.week(cfg, inbox_waiting=2, recent=[], start=start, rng=random.Random(4))
        self.assertEqual([p["mode"] for p in planned[:2]], ["input", "input"])
        modes = [p["mode"] for p in planned]
        for i in range(2, len(modes)):
            self.assertFalse(modes[i] == modes[i - 1] == modes[i - 2])
        self.assertIn("input", plan.render(planned, "Kolkata") or "input")


class EngageTests(Base):
    def test_post_types(self):
        self.assertEqual(engage.classify_post("We launched our billing service today."), "achievement")
        self.assertEqual(engage.classify_post("Unpopular opinion: standups are dead."), "opinion")
        self.assertEqual(engage.classify_post("What do you do when the build is red?"), "question")

    def test_reply_sorting(self):
        out = engage.sort_replies(["Nice!", "Can you send me the pricing?", "I think the cache is the real problem here, we tried it and it was worse.", "Does this work with Postgres?"])
        cats = [o["category"] for o in out]
        self.assertEqual(cats[0], "lead")
        self.assertEqual(cats[-1], "noise")
        self.assertLess(cats.index("question"), cats.index("noise"))

    def test_comment_generation_uses_model_and_gate(self):
        st = self.app.settings()
        self.fake.chat.append({"comment": "What broke first when you moved the traffic over? We saw the same cache stampede in March."})
        res = engage.write_comment(Ollama(self.fake.url, "qwen2.5:7b"), dict(st.cfg, _rules=st.rules),
                                   "We launched our billing service today after six months.")
        self.assertEqual(res["kind"], "achievement")
        self.assertEqual(res["flags"], [])

    def test_dm_follow_up_limit(self):
        st = self.app.settings()
        res = engage.draft_dm(Ollama(self.fake.url, "m"), dict(st.cfg, _rules=st.rules), "follow2", "ctx", follow_ups_sent=2)
        self.assertEqual(res["text"], "")
        self.assertIn("limit", res["flags"][0])
        self.assertIn("limit", studio.dm_report(st, Ollama(self.fake.url, "m"), "follow2", "ctx", 2))

    def test_carousel_pdf_is_valid(self):
        out = self.tmp / "c.pdf"
        ok = engage.carousel_pdf({"title": "T", "intro": "", "slides": [{"headline": "One", "body": "a " * 40}, {"headline": "Two", "body": "b"}]}, str(out))
        self.assertTrue(ok)
        self.assertTrue(out.read_bytes().startswith(b"%PDF"))

    def test_carousel_post_approve_uploads_document(self):
        self.fake.chat.append({"title": "Deploys", "intro": "Six things I learned about deploys.",
                               "slides": [{"headline": "Deploys are scary", "body": "Until they are not."},
                                          {"headline": "Small batches", "body": "Ship smaller changes."}]})
        pid = self.app.carousel_post("deploys")
        p = self.app.db.get_post(pid)
        self.assertEqual(p["mode"], "carousel")
        self.assertTrue(Path(p["source"]["doc_path"]).exists())
        self.app.approve(pid)
        body = self.fake.posts()[0][1]
        self.assertEqual(body["content"]["media"]["id"], "urn:li:document:55")
        self.assertTrue(any(c[0] == "docinit" for c in self.fake.calls))
        self.assertEqual(self.app.db.get_post(pid)["status"], "posted")

    def test_repurpose_saves_angles_to_inbox(self):
        self.fake.chat.append({"angles": ["Why our cache stampede happened", "The rollback checklist we use now"]})
        self.app.repurpose("Long source text. " * 30)
        texts = [i["text"] for i in self.app.db.inbox_waiting()]
        self.assertEqual(len(texts), 2)
        self.assertTrue(texts[0].startswith("From something I wrote:"))

    def test_opener_is_improved_only_when_clearly_better(self):
        weak = "So something happened with the cache that we should talk about.\n" + GOOD.split(". ", 1)[1]
        self.fake.chat += [{"post": weak}, {"openers": ["I broke the cache at 2am and nobody noticed for a week.", "Okay."]}]
        st = self.app.settings()
        st.cfg["hookRewrite"] = True
        g = {"mode": "thought", "context": "x", "topic_key": "", "facts": None}
        d = writer.write(st, Ollama(self.fake.url, "qwen2.5:7b"), g)
        self.assertTrue(d["text"].startswith("I broke the cache at 2am"))
        self.assertIn("trailing space", d["text"])

    def test_check_report_and_studio_run(self):
        self.assertIn("CHECK", self.app.check_text(GOOD))
        st = self.app.settings()
        report = studio.check_report(st, AI_TEXT)
        self.assertIn("Human score", report)
        self.assertIn("stock phrase", report)
        self.assertIn("HOOK RANKING", studio.run(self.app, {"tool": ["hooks"], "text": ["I lost a week to caching.\nHey all."]}))
        self.assertIn("MY POSTS", studio.run(self.app, {"tool": ["audit"], "csv": [AnalyticsTests.CSV]}))
        self.assertIn("PROFILE", studio.run(self.app, {"tool": ["profile"], "headline": ["DevOps engineer"], "skills": ["2"]}))
        self.assertIn("BEAT SHEET", studio.run(self.app, {"tool": ["beats"], "text": ["Hello there. I lost a week.\nDocker is great."], "target": ["30"]}))
        self.assertTrue(self.app.plan_text().startswith("THIS WEEK"))

    def test_tools_cli_runs_standalone(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(ROOT / "templates" / "config.json", tmp / "config.json")
        f = tmp / "draft.txt"
        f.write_text(GOOD, encoding="utf-8")
        env = dict(os.environ, CADENCE_HOME=str(tmp))
        out = subprocess.run([sys.executable, "-m", "cadence", "tools", "check", str(f)], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("CHECK", out.stdout)


class ParityTests(Base):
    """Features added to match the LinkedIn skill set: hook formulas, detector tells, connection notes,
    comment types, inbox triage, engagement list, audit stop-list and profile rewrite."""

    def test_five_new_hook_formulas_classify(self):
        from cadence import hooks
        self.assertEqual(len(hooks.FORMULAS), 21)
        self.assertEqual(hooks.classify("Slow CI is not why our releases keep slipping."), "Myth Bust")
        self.assertEqual(hooks.classify("Writing release notes used to take a day. Now it takes twenty minutes."), "Time Anchor")
        self.assertEqual(hooks.classify("I was wrong."), "Pattern Interrupt")
        self.assertEqual(hooks.classify("I removed our most-used internal tool last month."), "Walk Away")

    def test_detector_flags_rhetorical_one_word_and_engagement_bait(self):
        from cadence import detect
        tells = detect.structural_tells("We shipped it. Ready?\nThoughts? Drop them below.")
        self.assertIn("one-word rhetorical question", tells)
        self.assertIn("engagement bait", tells)
        self.assertEqual(detect.structural_tells("A plain sentence about a plain problem."), [])

    def test_verdict_weights_weakest_check(self):
        from cadence import detect
        res = detect.score("Plain words. " * 10)
        mean = sum(res["checks"].values()) / len(res["checks"])
        self.assertEqual(res["human"], round(0.6 * mean + 0.4 * min(res["checks"].values())))

    def test_connection_note_is_limited_to_200_characters(self):
        from cadence import engage
        long = "x" * 260
        self.fake.chat.append({"dm": long})
        st = self.app.settings()
        res = engage.draft_dm(Ollama(self.fake.url, "qwen2.5:7b"), dict(st.cfg, _rules=st.rules), "invite", "Saw your talk on Kubernetes.")
        self.assertTrue(any("200" in f for f in res["flags"]))
        self.assertIn("invite", engage.DM_KINDS)

    def test_nine_comment_types_and_new_post_kinds(self):
        from cadence import engage
        self.assertEqual(len(engage.COMMENT_TYPES), 9)
        self.assertEqual(engage.classify_post("We're hiring a platform engineer in Pune."), "hiring")
        self.assertEqual(engage.classify_post("Introducing our new CLI, now available."), "product")
        self.assertEqual(engage.classify_post("I was wrong about the rewrite and it cost us a quarter."), "vulnerable")

    def test_inbox_triage_sorts_and_gives_the_tell(self):
        from cadence import inbox_triage as t
        items = t.triage([
            "Guaranteed 10x growth, DM me for details",
            "Can you share your pricing for a migration?",
            "Hi, we are hiring a DevOps lead, interested?",
            "Great post, congrats on the launch",
            "How do I set up Kubernetes?",
        ])
        cats = [i["category"] for i in items]
        self.assertEqual(cats, ["lead", "recruiter", "question", "peer", "spam"])
        self.assertEqual(items[0]["tell"], "pricing")

    def test_inbox_report_runs_through_studio(self):
        self.fake.chat.append({"reply": "Happy to scope it. What is the timeline?"})
        out = studio.run(self.app, {"tool": ["inbox"], "text": ["Can you share your pricing?"]})
        self.assertIn("INBOX", out)
        self.assertIn("tell: pricing", out)
        self.assertIn("reply:", out)

    def test_engagement_list_caps_at_ten_and_renders(self):
        from cadence import plan
        items = plan.engagement_list([f"Person {i}" for i in range(14)])
        self.assertEqual(len(items), 10)
        self.assertIn("ENGAGE THIS WEEK", plan.render_engagement(items))
        self.assertIn("paste up to 10", plan.render_engagement([]))
        out = studio.run(self.app, {"tool": ["plan"], "targets": ["Asha Rao\nRavi Menon"]})
        self.assertIn("Asha Rao", out)
        self.assertIn("THIS WEEK", out)

    def test_audit_has_engagement_rate_and_stop_list(self):
        from cadence import analytics
        rows = analytics.parse_csv(AnalyticsTests.CSV)
        report = analytics.report(rows, "T")
        self.assertIn("engagement", report)
        self.assertIn("Stop doing", report)

    def test_profile_rewrite_flags_invented_numbers(self):
        from cadence import profile_audit
        self.fake.chat.append({"headline": "DevOps engineer who cut deploys from 2 hours to 9 minutes",
                               "about_open": "I run 40 services for a fintech team."})
        st = self.app.settings()
        res = profile_audit.rewrite(Ollama(self.fake.url, "qwen2.5:7b"), dict(st.cfg, _rules=st.rules),
                                    {"headline": "DevOps engineer", "about": "I run platforms.", "experience": "Cut deploy time."})
        self.assertIn("headline", res)
        self.assertTrue(any('"40"' in f for f in res["flags"]))
        self.assertTrue(any('"9"' in f for f in res["flags"]))  # invented in the headline, so it must be flagged


if __name__ == "__main__":
    unittest.main(verbosity=2)
