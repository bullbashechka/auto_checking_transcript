from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from .config import PROMPTS_DIR, Settings

log = logging.getLogger(__name__)


class CheckResult(BaseModel):
    corrected: str | None = Field(default=None, alias="Исправленное_Содержание")
    changes: list[str] = Field(default_factory=list, alias="Изменения")
    warning: str | None = Field(default=None, alias="Предупреждение")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @property
    def is_empty(self) -> bool:
        return self.corrected is None and not self.changes and self.warning is None


def _load_system_prompt() -> str:
    return (PROMPTS_DIR / "system_prompt.txt").read_text(encoding="utf-8")


@dataclass
class LLMClient:
    settings: Settings
    _client: genai.Client = None  # type: ignore[assignment]
    _system_prompt: str = ""
    _semaphore: asyncio.Semaphore = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._client = genai.Client(api_key=self.settings.gemini_api_key)
        self._system_prompt = _load_system_prompt()
        self._semaphore = asyncio.Semaphore(self.settings.llm_concurrency)

    async def check(self, contractor: str, date: str, time: str, content: str) -> CheckResult:
        payload = {
            "Контрагент": contractor,
            "Дата": date,
            "Время_начала": time,
            "Содержание": content,
        }
        user_message = json.dumps(payload, ensure_ascii=False)

        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            temperature=0,
            response_mime_type="application/json",
        )

        async with self._semaphore:
            raw = await self._call_with_retries(user_message, config)

        return self._parse(raw, content)

    async def _call_with_retries(
        self, user_message: str, config: types.GenerateContentConfig
    ) -> str:
        delays = [0, 2, 4]
        last_err: Exception | None = None
        for attempt, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self._client.aio.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=user_message,
                    config=config,
                )
                return response.text or "{}"
            except Exception as err:  # noqa: BLE001
                last_err = err
                log.warning("Gemini call failed (attempt %d): %s", attempt + 1, err)
        raise RuntimeError(f"Gemini call failed after retries: {last_err}")

    def _parse(self, raw: str, original_content: str) -> CheckResult:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON response: %r", raw[:200])
            return CheckResult()

        if not isinstance(data, dict):
            return CheckResult()

        try:
            result = CheckResult.model_validate(data)
        except ValidationError as err:
            log.warning("LLM response failed validation: %s", err)
            return CheckResult()

        if result.corrected == original_content:
            result = CheckResult(
                corrected=None,
                changes=[],
                warning=result.warning,
            )
        return result
