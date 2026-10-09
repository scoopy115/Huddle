"""LLM providers — all local. Each exposes ``complete_json`` (strict JSON object
output) and ``complete`` (free text). The summariser never knows which one it got.

* OllamaProvider   — documented localhost REST API (/api/chat, format=json).
* ExtractiveProvider — no model at all; marker class handled by the summariser.
"""
from __future__ import annotations

import json
import os
import traceback
from collections.abc import Callable

import httpx

from . import ollama_runtime
from .base import ProviderError

OLLAMA_URL = os.getenv("HUDDLE_OLLAMA_URL", "").rstrip("/") or "http://127.0.0.1:11434"
LMSTUDIO_URL = "http://127.0.0.1:1234"


class LlmCancelled(Exception):
    """The job was cancelled while the model was answering (see OllamaProvider.cancelled)."""


class OllamaProvider:
    id = "ollama"

    def __init__(self, model: str, base_url: str | None = None, num_ctx: int = 16384):
        self.model = model
        self._base_url = base_url.rstrip("/") if base_url else None
        self.num_ctx = num_ctx
        # Set by the job runner: polled between tokens so a cancel does not wait for the whole
        # answer (a summary can take a minute).
        self.cancelled: Callable[[], bool] | None = None

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        url = ollama_runtime.active_url()
        if not url:
            raise ProviderError("The local AI runtime could not be started. Download it under Settings → Models, or start Ollama.")
        return url

    def _chat(self, system: str, user: str, max_tokens: int, json_mode: bool) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": True,
            "options": {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": self.num_ctx},
            "think": False,
        }
        if json_mode:
            body["format"] = "json"
        try:
            try:
                return self._stream(body)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and "think" in e.response.text:
                    body.pop("think", None)          # older Ollama without the think flag
                    return self._stream(body)
                raise
        except LlmCancelled:
            raise
        except httpx.ConnectError as e:
            raise ProviderError("The local AI runtime stopped responding. Try again; Huddle restarts it when needed.",
                                detail=str(e)) from e
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"Ollama could not run '{self.model}'.",
                                detail=e.response.text[:2000]) from e
        except Exception as e:
            raise ProviderError("Ollama request failed.", detail=traceback.format_exc()) from e

    def _stream(self, body: dict) -> str:
        """One /api/chat call, token by token (NDJSON); the pieces are joined at the end."""
        parts: list[str] = []
        with httpx.stream("POST", f"{self.base_url}/api/chat", json=body, timeout=httpx.Timeout(900, connect=30)) as r:
            if r.status_code >= 400:
                r.read()
                r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                if self.cancelled and self.cancelled():
                    raise LlmCancelled()
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("error"):
                    raise ProviderError(f"Ollama could not run '{self.model}'.", detail=str(item["error"]))
                parts.append((item.get("message") or {}).get("content") or "")
                if item.get("done"):
                    break
        return "".join(parts)

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
