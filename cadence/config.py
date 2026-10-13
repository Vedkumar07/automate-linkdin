"""Loads settings. Everything the user edits lives in plain files: config.json, voice.md, profile.md, stories.md, .env."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import load_env

DEFAULTS = {
    "name": "Me",
    "ownerChatId": "",
    "model": "qwen2.5:7b",
    "ollamaUrl": "http://localhost:11434",
    "temperature": 0.85,
    "autoPost": False,
    "alwaysApproveNews": True,
    "schedule": {"days": [1, 3, 5], "hour": 9, "minute": 0},
    "timezone": "Asia/Kolkata",
    "catchUpHours": 6,
    "maxPostsPerDay": 1,
    "mix": {"profile": 0.25, "news": 0.55, "thought": 0.20},
    "length": {"minWords": 90, "maxWords": 190},
    "maxHashtags": 2,
    "allowEmoji": False,
    "maxTries": 3,
    "linkedinVersion": "202510",
    "photoAltText": "Photo from my work",
    "visionModel": "gemma3:4b",
    "previewChars": 180,
    "hookMin": 55,
    "humanScoreMin": 60,
    "hookRewrite": True,
    "photoDraftsImmediately": True,
    "web": {"host": "127.0.0.1", "port": 8787},
    "interests": [],
    "feeds": [],
    "banned": [],
}


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def parse_voice(text: str) -> tuple[str, list[str]]:
    parts = re.split(r"^## My real writing samples.*$", text, flags=re.M)
    rules = strip_comments(parts[0])
    rules = re.sub(r"^# .*$", "", rules, flags=re.M)
    rules = rules.split("## Rules", 1)[-1].strip()
    samples: list[str] = []
    if len(parts) > 1:
        body = strip_comments(parts[1])
        samples = [s.strip() for s in re.split(r"^---\s*$", body, flags=re.M) if s.strip()]
    return rules, samples


def parse_lines(text: str) -> list[str]:
    out = []
    for line in strip_comments(text).splitlines():
        m = re.match(r"^\s*[-*]\s+(.*\S)\s*$", line)
        if m and "(example)" not in m.group(1).lower() and len(m.group(1)) >= 15:
            out.append(m.group(1))
    return out


class Settings:
    """A snapshot of the user's files. Call Settings.load() again to pick up edits (it is cheap)."""

    def __init__(self, root: Path, cfg: dict, env: dict):
        self.root, self.cfg, self.env = root, cfg, env
        voice = (root / "voice.md").read_text(encoding="utf-8") if (root / "voice.md").exists() else ""
        self.rules, self.samples = parse_voice(voice)
        prof = (root / "profile.md").read_text(encoding="utf-8") if (root / "profile.md").exists() else ""
        stor = (root / "stories.md").read_text(encoding="utf-8") if (root / "stories.md").exists() else ""
        self.profile_lines = parse_lines(prof) + parse_lines(stor)
        self.data_dir = root / "data"
        self.data_dir.mkdir(exist_ok=True)
        (self.data_dir / "media").mkdir(exist_ok=True)

    @classmethod
    def load(cls, root: Path | str) -> "Settings":
        root = Path(root)
        config_path = root / "config.json"
        if not config_path.exists():
            config_path = root / "templates" / "config.json"
        cfg = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        merged = json.loads(json.dumps(DEFAULTS))
        for k, v in cfg.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        env = load_env(root / ".env")
        # Container wiring overrides only networking; editable settings stay on disk.
        if env.get("OLLAMA_URL"):
            merged["ollamaUrl"] = env["OLLAMA_URL"]
        if env.get("CADENCE_WEB_HOST"):
            merged["web"]["host"] = env["CADENCE_WEB_HOST"]
        if env.get("CADENCE_WEB_PORT"):
            merged["web"]["port"] = int(env["CADENCE_WEB_PORT"])
        if env.get("TELEGRAM_OWNER_CHAT_ID"):
            merged["ownerChatId"] = env["TELEGRAM_OWNER_CHAT_ID"]
        return cls(root, merged, env)

    def __getitem__(self, k):
        return self.cfg[k]

    def get(self, k, default=None):
        return self.cfg.get(k, default)

    def secret(self, k: str) -> str:
        return self.env.get(k, "")

    def warnings(self) -> list[str]:
        w = []
        if not self.samples:
            w.append("No writing samples in voice.md yet. Posts will sound generic until you add 3 to 6 things you really wrote.")
        if not self.profile_lines:
            w.append("profile.md and stories.md have no usable lines, so 'background' posts are switched off.")
        if not self.secret("TELEGRAM_BOT_TOKEN"):
            w.append("TELEGRAM_BOT_TOKEN is not set, so there is no phone approval or inbox.")
        if not (self.secret("LINKEDIN_CLIENT_ID") and self.secret("LINKEDIN_CLIENT_SECRET")):
            w.append("LinkedIn client id and secret are not set in .env.")
        return w
