"""Timed publication tests use real SQLite and stub external services."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from cadence import scheduler, web
from cadence.db import DB
from cadence.linkedin import LinkedInError
from cadence.service import App

NOW = datetime(2030, 1, 1, 3, 0, tzinfo=timezone.utc)
DUE = datetime(2030, 1, 1, 3, 30, tzinfo=timezone.utc)


class ScheduledPostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'config.json').write_text(json.dumps({'timezone': 'Asia/Kolkata', 'schedule': {'days': []}}))
        self.app = App(root)
        self.addCleanup(self.app.db.conn.close)
        self.app.li = Mock()
        self.app.li.status.return_value = {'connected': True, 'note': 'connected', 'days_left': 60}
        self.app.li.post.return_value = 'urn:li:share:example'
        self.app.tg = Mock(enabled=False, owner='')
        self.app.tg.send.return_value = 0
        self.app.tg.send_draft.return_value = 0
        self.app.llm_health = Mock(return_value=(True, 'ready', []))
        self.pid = self.app.db.add_post(mode='input', text='My saved draft', status='pending')

    def schedule(self):
        with patch('cadence.service.utcnow', return_value=NOW):
            return self.app.schedule_post(self.pid, '2030-01-01T09:00')

    def test_timezone_due_time_and_publish_once(self):
        self.schedule()
        self.assertEqual(self.app.db.get_post(self.pid)['scheduled_at'], '2030-01-01T03:30:00Z')
        self.app.publish_due(NOW)
        self.app.li.post.assert_not_called()
        self.app.publish_due(DUE)
        self.app.publish_due(DUE)
        self.app.li.post.assert_called_once()
        self.assertEqual(self.app.db.get_post(self.pid)['status'], 'posted')
        self.assertTrue(self.app.db.get_post(self.pid)['posted_at'])

    def test_cancel_restores_draft_and_prevents_publication(self):
        self.schedule()
        self.assertTrue(self.app.cancel_schedule(self.pid))
        self.app.publish_due(DUE)
        self.app.li.post.assert_not_called()
        self.assertEqual(self.app.db.get_post(self.pid)['status'], 'pending')
        self.assertEqual(self.app.db.get_post(self.pid)['scheduled_at'], '')

    def test_reschedule_and_new_draft_do_not_expire_scheduled_post(self):
        self.schedule()
        with patch('cadence.service.utcnow', return_value=NOW):
            self.app.schedule_post(self.pid, '2030-01-01T10:00')
        self.app._expire_pending()
        self.app.publish_due(DUE)
        self.assertEqual(self.app.db.get_post(self.pid)['status'], 'scheduled')
        self.app.li.post.assert_not_called()

    def test_restart_retains_schedule_and_publishes_overdue_post(self):
        self.schedule()
        another = App(self.app.root)
        self.addCleanup(another.db.conn.close)
        another.li, another.tg = self.app.li, self.app.tg
        another.publish_due(datetime(2030, 1, 2, tzinfo=timezone.utc))
        self.app.li.post.assert_called_once()

    def test_pause_blocks_publication_then_resume_publishes(self):
        self.schedule()
        self.app.db.kv_set('paused', True)
        with patch('cadence.scheduler.local_now', return_value=DUE):
            scheduler.tick(self.app)
            self.app.li.post.assert_not_called()
            self.app.db.kv_set('paused', False)
            scheduler.tick(self.app)
        self.app.li.post.assert_called_once()

    def test_failed_publish_is_visible_and_does_not_retry_automatically(self):
        self.schedule()
        self.app.li.post.side_effect = LinkedInError('Token expired')
        self.app.publish_due(DUE)
        self.app.publish_due(DUE)
        post = self.app.db.get_post(self.pid)
        self.assertEqual(post['status'], 'failed')
        self.assertEqual(post['error'], 'Token expired')
        self.app.li.post.assert_called_once()

    def test_invalid_past_disconnected_and_published_posts_cannot_schedule(self):
        with patch('cadence.service.utcnow', return_value=NOW):
            for value in ('', 'bad', '2020-01-01T09:00', '2030-01-01T09:00+05:30'):
                with self.assertRaises(ValueError):
                    self.app.schedule_post(self.pid, value)
            self.app.li.status.return_value['connected'] = False
            with self.assertRaises(ValueError):
                self.app.schedule_post(self.pid, '2030-01-01T09:00')
            self.app.li.status.return_value['connected'] = True
            self.app.db.update_post(self.pid, status='posted')
            with self.assertRaises(ValueError):
                self.app.schedule_post(self.pid, '2030-01-01T09:00')

    def test_dashboard_saves_edited_text_and_displays_local_time(self):
        dash = web.Dashboard(self.app)
        with patch('cadence.service.utcnow', return_value=NOW):
            result = dash.act(f'/decide/{self.pid}', {'do': ['schedule'], 'scheduled_at': ['2030-01-01T09:00'], 'text': ['Edited draft']})
        self.assertIn('Scheduled for', result)
        self.assertEqual(self.app.db.get_post(self.pid)['text'], 'Edited draft')
        body = dash.today('').decode()
        self.assertIn('Scheduled posts', body)
        self.assertIn('09:00', body)
        self.assertIn('Asia/Kolkata', body)
        self.assertIn('Edited draft', body)
        self.assertIn('cancel', body)
        self.assertIn('cancelled', dash.act(f'/decide/{self.pid}', {'do': ['cancel']}))

    def test_telegram_schedule_and_cancel(self):
        from cadence.telegram import Telegram
        tg = Telegram(self.app.st, self.app.db)
        tg.app = self.app
        tg.send = Mock()
        with patch('cadence.service.utcnow', return_value=NOW):
            tg._on_message({'text': f'/schedule {self.pid} 2030-01-01 09:00'})
        self.assertEqual(self.app.db.get_post(self.pid)['status'], 'scheduled')
        tg._on_message({'text': f'/unschedule {self.pid}'})
        self.assertEqual(self.app.db.get_post(self.pid)['status'], 'pending')

    def test_existing_database_migration_preserves_posts(self):
        old = Path(self.tmp.name) / 'old.db'
        with sqlite3.connect(old) as conn:
            conn.execute('CREATE TABLE posts (id INTEGER PRIMARY KEY, text TEXT)')
            conn.execute("INSERT INTO posts VALUES(1,'Existing draft')")
        db = DB(old)
        self.addCleanup(db.conn.close)
        self.assertEqual(db.conn.execute('SELECT text,scheduled_at FROM posts').fetchone()[:], ('Existing draft', ''))


if __name__ == '__main__':
    unittest.main()
