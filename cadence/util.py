"""Small shared helpers: env loading, an HTTP client with retries, time helpers."""
from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("cadence")
_ctx = ssl.create_default_context()


def load_env(path: Path) -> dict:
    """Reads KEY=VALUE lines from a .env file. Real environment variables win."""
    env: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k.startswith(("TELEGRAM_", "LINKEDIN_", "OLLAMA_", "CADENCE_")):
            env[k] = v
    return env


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status, self.body, self.url = status, body, url


class Response:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body.decode("utf-8")) if self.body else {}


def http(method: str, url: str, *, headers: dict | None = None, json_body=None,
         data: bytes | None = None, timeout: float = 30, retries: int = 2) -> Response:
    """HTTP request with retry on network errors, 429 and 5xx. Pass retries=0 for non-idempotent calls."""
    hdrs = dict(headers or {})
    body = data
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("User-Agent", "cadence/1.0")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HttpError(e.code, payload, url)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HttpError(0, str(e), url)
    raise HttpError(0, "unreachable", url)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
