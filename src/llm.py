from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from .config import PROMPTS_DIR, Settings

log = logging.getLogger(__name__)

BATCH_SIZE = 3
CACHE_TTL_SECONDS = 1800  # 30 минут — TTL explicit context cache в Gemini
CACHE_TTL_MARGIN_SECONDS = 60  # перезаливаем за минуту до истечения


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
    _client: genai.Client = None  # type: ignore[assignment]
    _system_prompt: str = ""
    _semaphore: asyncio.Semaphore = None  # type: ignore[assignment]
    _cache_name: str | None = None
    _cache_expires_at: float = 0.0
    _cache_lock: asyncio.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._client = genai.Client(api_key=self.settings.gemini_api_key)
        self._system_prompt = _load_system_prompt()
        self._semaphore = asyncio.Semaphore(self.settings.llm_concurrency)
        self._cache_lock = asyncio.Lock()
        self._try_create_cache_sync()

    def _try_create_cache_sync(self) -> None:
        """Создаём explicit context cache синхронно при старте.

        Если не получилось (например, system_prompt короче минимума токенов для
        кэша на этой модели) — продолжаем работу без кэша: каждый вызов будет
        слать system_instruction целиком.
        """
        try:
            cache = self._client.caches.create(
                model=self.settings.gemini_model,
                config=types.CreateCachedContentConfig(
                    system_instruction=self._system_prompt,
                    ttl=f"{CACHE_TTL_SECONDS}s",
                ),
            )
            self._cache_name = cache.name
            self._cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS - CACHE_TTL_MARGIN_SECONDS
            log.info(
                "Created Gemini context cache: %s (TTL=%ds)",
                cache.name, CACHE_TTL_SECONDS,
            )
        except Exception as err:  # noqa: BLE001
            log.warning(
                "Context cache unavailable (%s) — system prompt будет отправляться на каждый вызов",
                err,
            )
            self._cache_name = None

    async def _ensure_cache(self) -> str | None:
        """Возвращает актуальное имя кэша. Перезаливает, если TTL вот-вот истечёт."""
        if self._cache_name and time.monotonic() < self._cache_expires_at:
            return self._cache_name
        async with self._cache_lock:
            if self._cache_name and time.monotonic() < self._cache_expires_at:
                return self._cache_name
            try:
                cache = await asyncio.to_thread(
                    self._client.caches.create,
                    model=self.settings.gemini_model,
                    config=types.CreateCachedContentConfig(
                        system_instruction=self._system_prompt,
                        ttl=f"{CACHE_TTL_SECONDS}s",
                    ),
                )
                self._cache_name = cache.name
                self._cache_expires_at = (
                    time.monotonic() + CACHE_TTL_SECONDS - CACHE_TTL_MARGIN_SECONDS
                )
                log.info("Refreshed Gemini context cache: %s", cache.name)
                return self._cache_name
            except Exception as err:  # noqa: BLE001
                log.warning(
                    "Cache refresh failed (%s) — fallback к system_instruction на этот раз",
                    err,
                )
                self._cache_name = None
                self._cache_expires_at = 0.0
                return None

    def _build_config(self, cache_name: str | None) -> types.GenerateContentConfig:
        if cache_name:
            return types.GenerateContentConfig(
                cached_content=cache_name,
                temperature=0,
                response_mime_type="application/json",
            )
        return types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            temperature=0,
            response_mime_type="application/json",
        )

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

        cache_name = await self._ensure_cache()
        config = self._build_config(cache_name)

        raw: str | None = None
        async with self._semaphore:
            try:
                raw = await self._call_with_retries(user_message, config)
            except Exception as err:  # noqa: BLE001
                log.warning("Batch call failed entirely (size=%d): %s", len(items), err)

        if raw is not None:
            parsed = self._parse_batch(raw, items)
            if parsed is not None:
                return parsed
            log.warning("Batch response invalid (size=%d) — falling back to singles", len(items))

        # Fallback: при размере 1 — возвращаем пустой результат, чтобы не входить в рекурсию.
        if len(items) == 1:
            return [CheckResult()]

        results: list[CheckResult] = []
        for it in items:
            single = BatchItem(id=0, contractor=it.contractor, date=it.date,
                               time=it.time, content=it.content)
            try:
                sub = await self.check_batch([single])
                results.append(sub[0])
            except Exception:  # noqa: BLE001
                log.exception("Single fallback failed for original id=%d", it.id)
                results.append(CheckResult())
        return results

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
                return response.text or "[]"
            except Exception as err:  # noqa: BLE001
                last_err = err
                log.warning("Gemini call failed (attempt %d): %s", attempt + 1, err)
        raise RuntimeError(f"Gemini call failed after retries: {last_err}")

    def _parse_batch(
        self, raw: str, items: list[BatchItem]
    ) -> list[CheckResult] | None:
        """Parse batch response. Returns None on any structural issue (→ caller does fallback)."""
        text = raw.strip()
        try:
            data = json.loads(text) if text else []
        except json.JSONDecodeError:
            log.warning("LLM returned non-JSON response: %r", raw[:200])
            return None

        if not isinstance(data, list):
            log.warning("LLM response is not a JSON array: %r", raw[:200])
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

            if result.corrected == it.content:
                log.debug(
                    "LLM returned identical 'corrected' text for id=%d — dropping changes (%d items)",
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
