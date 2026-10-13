"""Posting schedule. Pure functions for the maths, plus a background loop that calls the app when a slot is due."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .util import log, parse_iso, utcnow


def _days(cfg) -> set[int]:
    return {7 if d in (0, 7) else int(d) for d in cfg["schedule"]["days"]}  # ISO weekdays, 1=Mon .. 7=Sun


def slot_for(day: datetime, cfg) -> datetime:
    s = cfg["schedule"]
    return day.replace(hour=int(s["hour"]), minute=int(s["minute"]), second=0, microsecond=0)


def due_slot(now: datetime, cfg, last_slot: str | None) -> datetime | None:
    """Today's slot, if it has passed, was not already handled, and is not older than catchUpHours."""
    if now.isoweekday() not in _days(cfg):
        return None
    slot = slot_for(now, cfg)
    if now < slot or (now - slot) > timedelta(hours=float(cfg["catchUpHours"])):
        return None
    return None if last_slot == slot.isoformat() else slot


def next_slot(now: datetime, cfg) -> datetime | None:
    days = _days(cfg)
    if not days:
        return None
    for i in range(0, 8):
        d = now + timedelta(days=i)
        if d.isoweekday() in days:
            slot = slot_for(d, cfg)
            if slot > now:
                return slot
    return None


def local_now(cfg) -> datetime:
    return utcnow().astimezone(ZoneInfo(cfg["timezone"]))


def run_forever(app, stop: threading.Event):
    log.info("scheduler running")
    while not stop.is_set():
        try:
            tick(app)
        except Exception:
            log.exception("scheduler tick failed")
        stop.wait(30)


def tick(app):
    st = app.settings()
    cfg = st.cfg
    now = local_now(cfg)
    app.remind_token_expiry(now)
    if app.db.kv_get("paused", False):
        return
    app.publish_due(now)
    slot = due_slot(now, cfg, app.db.kv_get("last_slot"))
    if not slot:
        return
    app.db.kv_set("last_slot", slot.isoformat())  # mark first: a crash must not cause a retry storm
    if app.posts_today(now) >= int(cfg["maxPostsPerDay"]):
        log.info("slot %s skipped: already posted today", slot)
        return
    log.info("slot %s is due, writing", slot)
    app.generate_safe(manual=False)
