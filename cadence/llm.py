"""Talks to a local Ollama server."""
from __future__ import annotations

import json
import re

from .util import HttpError, http


class LLMError(Exception):
    pass


class Ollama:
    def __init__(self, base: str, model: str, timeout: float = 300):
        self.base, self.model, self.timeout = base.rstrip("/"), model, timeout

    def chat(self, system: str, user: str, temperature: float = 0.8, images: list[str] | None = None,
             keep_alive: str = "15m") -> str:
        """images: base64 strings, for vision models."""
        msg = {"role": "user", "content": user}
        if images:
            msg["images"] = images
        try:
            r = http("POST", f"{self.base}/api/chat", timeout=self.timeout, retries=0, json_body={
                "model": self.model, "stream": False, "format": "json", "keep_alive": keep_alive,
                "options": {"temperature": temperature, "num_ctx": 8192},
                "messages": [{"role": "system", "content": system}, msg],
            })
        except HttpError as e:
            if e.status == 0:
                raise LLMError(f"Cannot reach Ollama at {self.base}. Is it running? (systemctl status ollama)") from e
            if e.status == 404:
                raise LLMError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}") from e
            raise LLMError(str(e)) from e
        return (r.json().get("message") or {}).get("content", "")

    def health(self) -> tuple[bool, str, list[str]]:
        try:
            names = [m["name"] for m in http("GET", f"{self.base}/api/tags", timeout=5, retries=0).json().get("models", [])]
        except HttpError:
            return False, f"Ollama is not reachable at {self.base}", []
        wanted = self.model if ":" in self.model else self.model + ":latest"
        has = wanted in names
        return has, ("ready" if has else f"model '{self.model}' not pulled (ollama pull {self.model})"), names


def parse_json(s: str):
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        m = re.search(r"\{[\s\S]*\}", s or "")
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None
