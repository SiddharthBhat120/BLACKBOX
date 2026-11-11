"""Optional OpenAI-compatible LLM client (e.g. OpenRouter free models)."""
from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from .config import settings


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 90.0):
        self.api_key, self.base_url, self.model, self.timeout = api_key, base_url.rstrip("/"), model, timeout

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(f"{self.base_url}/chat/completions",
                       headers={"Authorization": f"Bearer {self.api_key}", "X-Title": "BlackBox"},
                       json={"model": self.model, "temperature": 0.2,
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                       timeout=self.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def default_client() -> LLMClient | None:
    return OpenAICompatibleClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model) if settings.llm_enabled else None


def parse_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    return json.loads(fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1])
