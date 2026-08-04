"""Trusted Gemini client.  API credentials never enter evolvable modules."""

from __future__ import annotations

import random
import time

from google import genai
from google.genai import types

from core import prompts
from engine.candidate import parse_model_response


class Brain:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", max_retries: int = 2):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_retries = max(0, int(max_retries))

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def _call(self, prompt: str, temperature: float) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        max_output_tokens=65536,
                    ),
                )
                if not response.text:
                    raise RuntimeError("Model returned an empty response")
                return response.text
            except Exception as exc:
                text = str(exc).upper()
                retryable = any(code in text for code in ("429", "500", "502", "503", "504", "UNAVAILABLE"))
                if not retryable or attempt >= self.max_retries:
                    raise
                delay = min(2 ** attempt + random.random(), 8.0)
                time.sleep(delay)
        raise RuntimeError("unreachable")

    def generate_improvement(
        self, file_contents: dict[str, str], strategy: str, history: list | None = None
    ) -> dict[str, str]:
        prompt = prompts.build_improvement_prompt(file_contents, strategy, history)
        return parse_model_response(self._call(prompt, temperature=0.4))

    def review_changes(
        self,
        original: dict[str, str],
        modified: dict[str, str],
        strategy: str,
    ) -> tuple[bool, str]:
        prompt = prompts.build_review_prompt(original, modified, strategy)
        result = self._call(prompt, temperature=0.1)
        prefix = result[:100].upper()
        approved = "APPROVED" in prefix and "REJECTED" not in prefix
        return approved, result
