"""Fetches headlines from the RSS/Atom feeds in config.json."""
from __future__ import annotations

import html
import random
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .util import HttpError, http, log


def parse_feed(xml: bytes, source: str, limit: int = 6) -> list[dict]:
    out: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title, link = "", ""
        for ch in el:
            t = ch.tag.split("}")[-1]
            if t == "title":
                title = re.sub(r"\s+", " ", html.unescape("".join(ch.itertext()))).strip()
            elif t == "link":
                link = (ch.text or "").strip() or ch.attrib.get("href", "")
        if title and link.startswith("http"):
            out.append({"title": title, "link": link, "source": source})
        if len(out) >= limit:
            break
    return out


def _fetch(feed: dict) -> list[dict]:
    try:
        r = http("GET", feed["url"], timeout=10, retries=1, headers={"Accept": "application/rss+xml, application/xml, */*"})
        return parse_feed(r.body[:2_000_000], feed["name"])
    except HttpError as e:
        log.warning("feed %s failed: %s", feed["name"], e)
        return []


def fetch_headlines(feeds: list[dict], used_links: set, count: int = 12, rng=random) -> list[dict]:
    if not feeds:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(feeds))) as ex:
        lists = list(ex.map(_fetch, feeds))
    seen, items = set(), []
    for it in (i for lst in lists for i in lst):
        if it["link"] in used_links or it["link"] in seen:
            continue
        seen.add(it["link"])
        items.append(it)
    rng.shuffle(items)
    return items[:count]
