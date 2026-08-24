from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from .config import PROMPTS_DIR, Settings

log = logging.getLogger(__name__)

BATCH_SIZE = 3

_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "timesheet_checks",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "Исправленное_Содержание": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                        "Изменения": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "Предупреждение": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                    },
                    "required": [
                        "id",
                        "Исправленное_Содержание",
                        "Изменения",
                        "Предупреждение",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}

_EQUIVALENT_ABBREVIATION_RE = re.compile(r"(?<![\w/])(?:СХ|с/х)(?![\w/])")


def _is_non_retryable_api_error(err: Exception) -> bool:
    return (
        isinstance(err, APIStatusError)
        and 400 <= err.status_code < 500
        and err.status_code != 429
    )


def _canonicalize_equivalent_variants(text: str) -> str:
    """Сводит допустимые варианты е/ё и СХ/с/х для сравнения ответов LLM."""
    with_plain_e = text.replace("Ё", "Е").replace("ё", "е")
    return _EQUIVALENT_ABBREVIATION_RE.sub("СХ", with_plain_e)


def _has_only_equivalent_differences(original: str, corrected: str) -> bool:
    return _canonicalize_equivalent_variants(original) == _canonicalize_equivalent_variants(
        corrected
    )


@dataclass(frozen=True)
class BatchItem:
    id: int
    contractor: str
    date: str
    time: str
    content: str


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
    _client: AsyncOpenAI = None  # type: ignore[assignment]
    _system_prompt: str = ""
    _semaphore: asyncio.Semaphore = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            max_retries=0,
        )
        self._system_prompt = _load_system_prompt()
        self._semaphore = asyncio.Semaphore(self.settings.llm_concurrency)

    async def check(self, contractor: str, date: str, time: str, content: str) -> CheckResult:
        item = BatchItem(id=0, contractor=contractor, date=date, time=time, content=content)
        results = await self.check_batch([item])
        return results[0]

    async def check_batch(self, items: list[BatchItem]) -> list[CheckResult]:
        if not items:
            return []

        payload = [
            {
                "id": it.id,
                "Контрагент": it.contractor,
                "Дата": it.date,
                "Время_начала": it.time,
                "Содержание": it.content,
            }
            for it in items
        ]
        user_message = json.dumps(payload, ensure_ascii=False)

        raw: str | None = None
        async with self._semaphore:
            try:
                raw = await self._call_with_retries(user_message)
            except Exception as err:  # noqa: BLE001
                if _is_non_retryable_api_error(err):
                    raise
                log.warning("Batch call failed entirely (size=%d): %s", len(items), err)

        if raw is not None:
            parsed = self._parse_batch(raw, items)
            if parsed is not None:
                return parsed
            log.warning("Batch response invalid (size=%d) — falling back to singles", len(items))

        # При размере 1 возвращаем пустой результат, чтобы не входить в рекурсию.
        if len(items) == 1:
            return [CheckResult()]

        results: list[CheckResult] = []
        for it in items:
            single = BatchItem(
                id=0,
                contractor=it.contractor,
                date=it.date,
                time=it.time,
                content=it.content,
            )
            try:
                sub = await self.check_batch([single])
                results.append(sub[0])
            except Exception:  # noqa: BLE001
                log.exception("Single fallback failed for original id=%d", it.id)
                results.append(CheckResult())
        return results

    async def _call_with_retries(self, user_message: str) -> str:
        delays = [0, 2, 4]
        last_err: Exception | None = None
        for attempt, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self._client.responses.create(
                    model=self.settings.openai_model,
                    instructions=self._system_prompt,
                    input=user_message,
                    reasoning={"effort": self.settings.openai_reasoning_effort},
                    text={
                        "format": _RESPONSE_FORMAT,
                        "verbosity": "low",
                    },
                    store=False,
                )
                return response.output_text or '{"results": []}'
            except Exception as err:  # noqa: BLE001
                last_err = err
                if _is_non_retryable_api_error(err):
                    log.error("OpenAI request rejected without retry: %s", err)
                    raise
                log.warning("OpenAI call failed (attempt %d): %s", attempt + 1, err)
        raise RuntimeError(f"OpenAI call failed after retries: {last_err}")

    def _parse_batch(
        self, raw: str, items: list[BatchItem]
    ) -> list[CheckResult] | None:
        """Parse batch response. Returns None on any structural issue (→ caller does fallback)."""
        text = raw.strip()
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON response: %r", raw[:200])
            return None

        if isinstance(data, dict):
            data = data.get("results")
        elif isinstance(data, list):
            log.debug("Parsing legacy top-level JSON array")
        else:
            log.warning("LLM response is not a JSON object: %r", raw[:200])
            return None

        if not isinstance(data, list):
            log.warning("LLM response has no results array: %r", raw[:200])
            return None

        if len(data) != len(items):
            log.warning(
                "Batch length mismatch: expected %d, got %d", len(items), len(data)
            )
            return None

        expected_ids = {it.id for it in items}
        by_id: dict[int, dict] = {}
        for element in data:
            if not isinstance(element, dict):
                log.warning("Batch element is not an object: %r", element)
                return None
            element_id = element.get("id")
            if not isinstance(element_id, int):
                log.warning("Batch element missing int id: %r", element)
                return None
            if element_id not in expected_ids:
                log.warning("Batch element id %d not in expected set", element_id)
                return None
            if element_id in by_id:
                log.warning("Duplicate id %d in batch response", element_id)
                return None
            by_id[element_id] = element

        results: list[CheckResult] = []
        for it in items:
            element = by_id[it.id]
            try:
                result = CheckResult.model_validate(element)
            except ValidationError as err:
                log.warning("Batch element validation failed for id=%d: %s", it.id, err)
                return None

            if result.corrected is not None and _has_only_equivalent_differences(
                it.content, result.corrected
            ):
                log.debug(
                    "LLM returned only equivalent variants for id=%d — dropping changes (%d items)",
                    it.id,
                    len(result.changes),
                )
                result = CheckResult(
                    corrected=None,
                    changes=[],
                    warning=result.warning,
                )
            results.append(result)
        return results
