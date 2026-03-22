"""
core/brain.py - Gemini API integration.
All AI communication flows through this module.
"""

from google import genai
from google.genai import types

from core import prompts
from core.codemod import parse_gemini_response


class Brain:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def _call(self, prompt: str, temperature: float = 0.3) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=65536,
            ),
        )
        return response.text

    def analyze_code(self, file_contents: dict[str, str]) -> str:
        """Ask Gemini to analyze current code for weaknesses."""
        prompt = prompts.build_analysis_prompt(file_contents)
        return self._call(prompt, temperature=0.2)

    def generate_improvement(
        self, file_contents: dict[str, str], strategy: str
    ) -> dict[str, str]:
        """
        Ask Gemini to produce improved files based on the strategy.
        Returns {rel_path: new_content} for files that should change.
        """
        prompt = prompts.build_improvement_prompt(file_contents, strategy)
        raw = self._call(prompt, temperature=0.4)
        return parse_gemini_response(raw)

    def review_changes(
        self,
        original: dict[str, str],
        modified: dict[str, str],
        strategy: str,
    ) -> tuple[bool, str]:
        """
        Ask Gemini to review proposed changes.
        Returns (approved: bool, reasoning: str).
        """
        prompt = prompts.build_review_prompt(original, modified, strategy)
        result = self._call(prompt, temperature=0.1)
        approved = result.upper().startswith("APPROVED")
        return approved, result
