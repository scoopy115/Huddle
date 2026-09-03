"""LLM providers — all local. Each exposes ``complete_json`` (strict JSON object
output) and ``complete`` (free text). The summariser never knows which one it got.

* OllamaProvider   — documented localhost REST API (/api/chat, format=json).
* ExtractiveProvider — no model at all; marker class handled by the summariser.
"""
from __future__ import annotations

import json
import traceback

import httpx

from .base import ProviderError

OLLAMA_URL = "http://127.0.0.1:11434"
LMSTUDIO_URL = "http://127.0.0.1:1234"


class OllamaProvider:
    id = "ollama"

    def __init__(self, model: str, base_url: str = OLLAMA_URL, num_ctx: int = 16384):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx

    def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": self.num_ctx},
            "think": False,
        }
        if json_mode:
            body["format"] = "json"
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=900)
            if r.status_code == 400 and "think" in r.text:
                body.pop("think", None)          # older Ollama without the think flag
                r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=900)
            r.raise_for_status()
            data = r.json()
            return data["message"]["content"]
        except httpx.ConnectError as e:
            raise ProviderError("Ollama is not running. Start Ollama or choose another AI provider.",
                                detail=str(e)) from e
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"Ollama could not run '{self.model}'.",
                                detail=e.response.text[:2000]) from e
        except Exception as e:
            raise ProviderError("Ollama request failed.", detail=traceback.format_exc()) from e

    def complete_json(self, system: str, user: str, max_tokens: int = 2048) -> str:
        return self._chat(system, user, max_tokens, json_mode=True)

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        return self._chat(system, user, max_tokens, json_mode=False)


class ExtractiveProvider:
    """No LLM available: the summariser falls back to Huddle's extractive notes (huddle_engine.text)."""

    id = "extractive"
    model = "extractive"

    def complete_json(self, system: str, user: str, max_tokens: int = 2048) -> str:
        raise ProviderError("No local AI model is available.")

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        raise ProviderError("No local AI model is available.")


def parse_json_object(raw: str) -> dict:
    """Extract the first JSON object from a model response (tolerates prose / code fences)."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(s[start:end + 1])
