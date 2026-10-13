"""SQLite storage. One small file in data/cadence.db holds posts, the inbox and memory of what was already used."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .util import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL,
  topic_key TEXT DEFAULT '',
  text TEXT NOT NULL,
  flags TEXT DEFAULT '[]',
  status TEXT NOT NULL,              -- pending | posted | skipped | failed | expired
  source TEXT DEFAULT '{}',          -- JSON: what the writer was given, so "regenerate" can reuse it
  news_link TEXT DEFAULT '',
  input_id INTEGER DEFAULT 0,
  photo_path TEXT DEFAULT '',
  tg_message_id INTEGER DEFAULT 0,
  linkedin_id TEXT DEFAULT '',
  error TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT, posted_at TEXT
);
CREATE TABLE IF NOT EXISTS inbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  photo_path TEXT DEFAULT '',
  skips INTEGER DEFAULT 0,
  used INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS used(
  kind TEXT NOT NULL, key TEXT NOT NULL, at TEXT,
  PRIMARY KEY(kind, key)
);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for col in ("analysis TEXT DEFAULT ''", "tg_file_id TEXT DEFAULT ''"):  # upgrade older databases
            try:
                self.conn.execute(f"ALTER TABLE inbox ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        self.lock = threading.RLock()

    def _q(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args)

    # ---- key/value ----
    def kv_get(self, k, default=None):
        row = self._q("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def kv_set(self, k, v):
        self._q("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    # ---- posts ----
    def add_post(self, **f) -> int:
        now = iso()
        f.setdefault("flags", [])
        f.setdefault("source", {})
        cur = self._q(
            "INSERT INTO posts(mode,topic_key,text,flags,status,source,news_link,input_id,photo_path,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f["mode"], f.get("topic_key", ""), f["text"], json.dumps(f["flags"]), f["status"],
             json.dumps(f["source"]), f.get("news_link", ""), f.get("input_id", 0), f.get("photo_path", ""), now, now))
        return cur.lastrowid

    def update_post(self, pid: int, **f):
        if not f:
            return
        for k in ("flags", "source"):
            if k in f and not isinstance(f[k], str):
                f[k] = json.dumps(f[k])
        f["updated_at"] = iso()
        cols = ", ".join(f"{k}=?" for k in f)
        self._q(f"UPDATE posts SET {cols} WHERE id=?", (*f.values(), pid))

    def claim(self, pid: int, expect: str, new: str) -> bool:
        """Atomic status change. Returns False if someone else already moved the post (double-tap safe)."""
        cur = self._q("UPDATE posts SET status=?, updated_at=? WHERE id=? AND status=?", (new, iso(), pid, expect))
        return cur.rowcount == 1

    def get_post(self, pid: int):
        row = self._q("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        return self._post(row) if row else None

    def list_posts(self, limit=30, status: str | None = None):
        if status:
            rows = self._q("SELECT * FROM posts WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = self._q("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._post(r) for r in rows]

    def pending(self):
        rows = self._q("SELECT * FROM posts WHERE status='pending' ORDER BY id").fetchall()
        return [self._post(r) for r in rows]

    def posted_between(self, start_iso: str, end_iso: str) -> int:
        row = self._q("SELECT COUNT(*) c FROM posts WHERE status='posted' AND posted_at>=? AND posted_at<?",
                      (start_iso, end_iso)).fetchone()
        return row["c"]

    def recent_modes(self, n=2):
        rows = self._q("SELECT mode FROM posts WHERE status IN ('posted','pending','skipped') ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [r["mode"] for r in rows][::-1]

    @staticmethod
    def _post(row):
        d = dict(row)
        d["flags"] = json.loads(d.get("flags") or "[]")
        d["source"] = json.loads(d.get("source") or "{}")
        return d

    # ---- inbox ----
    def add_inbox(self, text: str, photo_path: str = "", tg_file_id: str = "") -> int:
        return self._q("INSERT INTO inbox(text,photo_path,tg_file_id,created_at) VALUES(?,?,?,?)",
                       (text, photo_path, tg_file_id, iso())).lastrowid

    def inbox_set_analysis(self, iid: int, analysis: str):
        self._q("UPDATE inbox SET analysis=? WHERE id=?", (analysis, iid))

    def inbox_waiting(self):
        return [dict(r) for r in self._q("SELECT * FROM inbox WHERE used=0 ORDER BY id").fetchall()]

    def inbox_get(self, iid: int):
        r = self._q("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def inbox_use(self, iid: int):
        self._q("UPDATE inbox SET used=1 WHERE id=?", (iid,))

    def inbox_skip(self, iid: int, retire_after=2):
        self._q("UPDATE inbox SET skips=skips+1 WHERE id=?", (iid,))
        self._q("UPDATE inbox SET used=1 WHERE id=? AND skips>=?", (iid, retire_after))

    def inbox_clear(self) -> int:
        n = self._q("SELECT COUNT(*) c FROM inbox WHERE used=0").fetchone()["c"]
        self._q("UPDATE inbox SET used=1")
        return n

    # ---- memory of used material ----
    def mark_used(self, kind: str, key: str):
        self._q("INSERT OR REPLACE INTO used(kind,key,at) VALUES(?,?,?)", (kind, key, iso()))

    def used_keys(self, kind: str) -> set:
        return {r["key"] for r in self._q("SELECT key FROM used WHERE kind=?", (kind,)).fetchall()}

    def reset_used(self, kind: str):
        self._q("DELETE FROM used WHERE kind=?", (kind,))
