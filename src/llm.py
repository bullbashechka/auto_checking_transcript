from __future__ import annotations

import asyncio
from collections import Counter
from difflib import SequenceMatcher
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from pydantic import BaseModel, Field, ValidationError

from .config import PROMPTS_DIR, Settings

log = logging.getLogger(__name__)

BATCH_SIZE = 15
_MAX_ATTEMPTS = 4
_MIN_OUTPUT_TOKENS = 1024
_MAX_OUTPUT_TOKENS = 4096
_OUTPUT_TOKENS_PER_ITEM = 256
_FALLBACK_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_RETRY_DELAY_HEADER_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m)?\s*$",
    re.IGNORECASE,
)

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
_EQUIVALENT_CHAR_TRANSLATION = str.maketrans({"Ё": "Е", "ё": "е"})
_SEMANTIC_TOKEN_RE = re.compile(
    r"(?<![\w/])(?:СХ|с/х)(?![\w/])|[^\W_]+|[^\w\s]+"
)
_EQUIVALENT_VARIANT_RE = r"(?:\b[её]\b|\bсх\b|с/х)"
_MAX_TOKEN_ALIGNMENT = 2048
_EQUIVALENT_CHANGE_DESCRIPTION_RE = re.compile(
    rf"(?:{_EQUIVALENT_VARIANT_RE}.*?(?:/|↔|→|\bна\b).*?{_EQUIVALENT_VARIANT_RE}"
    rf"|(?:равнознач|эквивалент).*?{_EQUIVALENT_VARIANT_RE}"
    rf"|{_EQUIVALENT_VARIANT_RE}.*?(?:равнознач|эквивалент))",
    re.IGNORECASE,
)


def _is_non_retryable_api_error(err: Exception) -> bool:
    return (
        isinstance(err, APIStatusError)
        and 400 <= err.status_code < 500
        and err.status_code not in {408, 409, 429}
    )


def _is_retryable_api_error(err: Exception) -> bool:
    if isinstance(err, APIStatusError):
        return err.status_code in {408, 409, 429} or err.status_code >= 500
    return isinstance(err, (APIConnectionError, APITimeoutError, TimeoutError))


def _parse_retry_delay(value: object) -> float | None:
    if value is None:
        return None
    match = _RETRY_DELAY_HEADER_RE.match(str(value))
    if not match:
        return None
    delay = float(match.group("value"))
    unit = (match.group("unit") or "s").lower()
    if unit == "ms":
        delay /= 1000
    elif unit == "m":
        delay *= 60
    return max(0.0, delay)


def _retry_delay_from_error(err: Exception, fallback: float) -> float:
    response = getattr(err, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for header_name in (
            "retry-after-ms",
            "retry-after",
            "x-ratelimit-reset-tokens",
        ):
            delay = _parse_retry_delay(headers.get(header_name))
            if delay is not None:
                return delay + random.uniform(0.05, 0.25)
    return fallback + random.uniform(0.05, 0.25)


class InvalidLLMResponseError(RuntimeError):
    """Raised when a single item cannot be parsed after batch splitting."""


class TokenPacer:
    """Smooths token reservations at a configured tokens-per-minute rate."""

    def __init__(self, tokens_per_minute: int) -> None:
        self._rate_per_second = max(1.0, tokens_per_minute / 60.0)
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> float:
        reservation = max(1, int(tokens))
        async with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._next_slot)
            self._next_slot = scheduled + reservation / self._rate_per_second

        delay = max(0.0, scheduled - now)
        if delay:
            await asyncio.sleep(delay)
        return delay


def _canonicalize_equivalent_variants(text: str) -> str:
    """Сводит допустимые варианты е/ё и СХ/с/х для сравнения ответов LLM."""
    with_plain_e = text.translate(_EQUIVALENT_CHAR_TRANSLATION)
    return _EQUIVALENT_ABBREVIATION_RE.sub("СХ", with_plain_e)


def _filter_equivalent_change_descriptions(changes: list[str]) -> list[str]:
    """Remove equivalent-variant clauses while retaining real corrections."""
    filtered: list[str] = []
    for change in changes:
        parts = re.split(
            rf"\s*(?:;|,)\s*|\s+и\s+(?!(?:{_EQUIVALENT_VARIANT_RE})\s+"
            rf"(?:равнознач|эквивалент))",
            change,
            flags=re.IGNORECASE,
        )
        retained = [
            part.strip()
            for part in parts
            if part.strip()
            and not _EQUIVALENT_CHANGE_DESCRIPTION_RE.search(part)
        ]
        if retained:
            filtered.append(" и ".join(retained))
    return filtered


def _canonical_token(text: str) -> str:
    return _canonicalize_equivalent_variants(text).casefold()


def _tokenize_semantic_text(text: str) -> list[tuple[int, int, str, str]]:
    return [
        (match.start(), match.end(), match.group(0), _canonical_token(match.group(0)))
        for match in _SEMANTIC_TOKEN_RE.finditer(text)
    ]


def _restore_equivalent_variant_in_token(original: str, corrected: str) -> str:
    """Restore variants only when the complete semantic token is unchanged."""
    if _canonical_token(original) != _canonical_token(corrected):
        return corrected
    if (
        _EQUIVALENT_ABBREVIATION_RE.fullmatch(original)
        and _EQUIVALENT_ABBREVIATION_RE.fullmatch(corrected)
    ):
        return original
    if len(original) != len(corrected):
        return corrected
    restored = list(corrected)
    for index, (original_char, corrected_char) in enumerate(zip(original, corrected)):
        if (
            original_char in "ЕЁеё"
            and corrected_char in "ЕЁеё"
            and original_char.translate(_EQUIVALENT_CHAR_TRANSLATION).casefold()
            == corrected_char.translate(_EQUIVALENT_CHAR_TRANSLATION).casefold()
        ):
            restored[index] = (
                original_char.upper() if corrected_char.isupper() else original_char.lower()
            )
    return "".join(restored)


def _token_alignment_pairs(
    original_tokens: list[tuple[int, int, str, str]],
    corrected_tokens: list[tuple[int, int, str, str]],
) -> list[tuple[int, int]]:
    original_keys = [token[3] for token in original_tokens]
    corrected_keys = [token[3] for token in corrected_tokens]
    if max(len(original_keys), len(corrected_keys)) <= _MAX_TOKEN_ALIGNMENT:
        matcher = SequenceMatcher(a=original_keys, b=corrected_keys, autojunk=False)
        return [
            (original_start + offset, corrected_start + offset)
            for original_start, corrected_start, size in matcher.get_matching_blocks()
            for offset in range(size)
        ]

    pairs: list[tuple[int, int]] = []
    original_index = 0
    corrected_index = 0
    while original_index < len(original_keys) and corrected_index < len(corrected_keys):
        if original_keys[original_index] == corrected_keys[corrected_index]:
            pairs.append((original_index, corrected_index))
            original_index += 1
            corrected_index += 1
        elif (
            original_index + 1 < len(original_keys)
            and original_keys[original_index + 1] == corrected_keys[corrected_index]
        ):
            original_index += 1
        elif (
            corrected_index + 1 < len(corrected_keys)
            and original_keys[original_index] == corrected_keys[corrected_index + 1]
        ):
            corrected_index += 1
        else:
            original_index += 1
            corrected_index += 1
    return pairs


def _restore_equivalent_variants(original: str, corrected: str) -> str:
    """Restore the author's е/ё and СХ/с/х variants after other edits."""
    original_tokens = _tokenize_semantic_text(original)
    corrected_tokens = _tokenize_semantic_text(corrected)
    if not original_tokens or not corrected_tokens:
        return corrected

    original_counts = Counter(token[3] for token in original_tokens)
    corrected_counts = Counter(token[3] for token in corrected_tokens)
    restorable_keys = {
        key for key, count in original_counts.items() if corrected_counts.get(key) == count
    }
    replacements: dict[int, str] = {}
    alignment_pairs = _token_alignment_pairs(original_tokens, corrected_tokens)
    for original_index, corrected_index in alignment_pairs:
        original_token = original_tokens[original_index]
        corrected_token = corrected_tokens[corrected_index]
        if original_token[3] not in restorable_keys:
            continue
        replacement = _restore_equivalent_variant_in_token(
            original_token[2], corrected_token[2]
        )
        if replacement != corrected_token[2]:
            replacements[corrected_index] = replacement

    pieces: list[str] = []
    cursor = 0
    for index, (start, end, raw, _canonical) in enumerate(corrected_tokens):
        pieces.append(corrected[cursor:start])
        pieces.append(replacements.get(index, raw))
        cursor = end
    pieces.append(corrected[cursor:])
    return "".join(pieces)


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
    _token_pacer: TokenPacer = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            max_retries=0,
        )
        self._system_prompt = _load_system_prompt()
        self._semaphore = asyncio.Semaphore(self.settings.llm_concurrency)
        effective_tpm = int(
            self.settings.openai_tpm_limit * self.settings.openai_tpm_utilization
        )
        if effective_tpm <= 0:
            raise ValueError("OPENAI_TPM_LIMIT × OPENAI_TPM_UTILIZATION must be positive")
        self._token_pacer = TokenPacer(effective_tpm)

    async def check(self, contractor: str, date: str, time: str, content: str) -> CheckResult:
        item = BatchItem(id=0, contractor=contractor, date=date, time=time, content=content)
        results = await self.check_batch([item])
        result = results[0]
        if isinstance(result, BaseException):
            raise result
        return result

    async def check_batch(
        self, items: list[BatchItem]
    ) -> list[CheckResult | BaseException]:
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

        raw = await self._call_with_retries(user_message)
        parsed = self._parse_batch(raw, items)
        if parsed is not None:
            return parsed

        if len(items) == 1:
            raise InvalidLLMResponseError(
                f"LLM returned an invalid response for item id={items[0].id}"
            )

        midpoint = len(items) // 2
        left_items = items[:midpoint]
        right_items = items[midpoint:]
        left_result, right_result = await asyncio.gather(
            self.check_batch(left_items),
            self.check_batch(right_items),
            return_exceptions=True,
        )
        return self._coerce_split_result(left_result, left_items) + self._coerce_split_result(
            right_result, right_items
        )

    @staticmethod
    def _coerce_split_result(
        result: list[CheckResult | BaseException] | BaseException,
        items: list[BatchItem],
    ) -> list[CheckResult | BaseException]:
        if isinstance(result, BaseException):
            return [result for _ in items]
        if len(result) != len(items):
            mismatch = InvalidLLMResponseError(
                f"Split response length mismatch: expected {len(items)}, got {len(result)}"
            )
            return [mismatch for _ in items]
        return result

    async def _call_with_retries(self, user_message: str) -> str:
        input_tokens = await self._count_input_tokens(user_message)
        item_count = self._item_count(user_message)
        max_output_tokens = min(
            _MAX_OUTPUT_TOKENS,
            max(_MIN_OUTPUT_TOKENS, _OUTPUT_TOKENS_PER_ITEM * item_count),
        )
        reserved_tokens = input_tokens + max_output_tokens
        last_err: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            waited = await self._token_pacer.acquire(reserved_tokens)
            if waited:
                log.debug(
                    "Token limiter delayed batch(size=%d) by %.2fs (reserved=%d)",
                    item_count,
                    waited,
                    reserved_tokens,
                )
            try:
                async with self._semaphore:
                    response = await self._client.responses.create(
                        model=self.settings.openai_model,
                        instructions=self._system_prompt,
                        input=user_message,
                        reasoning={"effort": self.settings.openai_reasoning_effort},
                        max_output_tokens=max_output_tokens,
                        text={
                            "format": _RESPONSE_FORMAT,
                            "verbosity": "low",
                        },
                        store=False,
                    )
                usage = getattr(response, "usage", None)
                if usage is not None:
                    log.debug(
                        "OpenAI usage batch(size=%d): input=%s output=%s total=%s",
                        item_count,
                        getattr(usage, "input_tokens", "?"),
                        getattr(usage, "output_tokens", "?"),
                        getattr(usage, "total_tokens", "?"),
                    )
                return response.output_text or '{"results": []}'
            except Exception as err:  # noqa: BLE001
                last_err = err
                if _is_non_retryable_api_error(err):
                    log.error("OpenAI request rejected without retry: %s", err)
                    raise
                if not _is_retryable_api_error(err) or attempt == _MAX_ATTEMPTS - 1:
                    log.warning(
                        "OpenAI call failed permanently after attempt %d: %s",
                        attempt + 1,
                        err,
                    )
                    raise
                fallback = _FALLBACK_BACKOFF_SECONDS[
                    min(attempt, len(_FALLBACK_BACKOFF_SECONDS) - 1)
                ]
                delay = _retry_delay_from_error(err, fallback)
                log.warning(
                    "OpenAI call failed (attempt %d/%d), retrying in %.2fs: %s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    delay,
                    err,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(f"OpenAI call failed after retries: {last_err}")

    async def _count_input_tokens(self, user_message: str) -> int:
        try:
            async with self._semaphore:
                counted = await self._client.beta.responses.input_tokens.count(
                    model=self.settings.openai_model,
                    instructions=self._system_prompt,
                    input=user_message,
                    reasoning={"effort": self.settings.openai_reasoning_effort},
                    text={
                        "format": _RESPONSE_FORMAT,
                        "verbosity": "low",
                    },
                )
            return max(1, int(counted.input_tokens))
        except Exception as err:  # noqa: BLE001
            if _is_non_retryable_api_error(err):
                raise
            estimated = self._estimate_input_tokens(user_message)
            log.warning(
                "Input token preflight failed; using conservative estimate=%d: %s",
                estimated,
                err,
            )
            return estimated

    def _estimate_input_tokens(self, user_message: str) -> int:
        serialized_schema = json.dumps(_RESPONSE_FORMAT, ensure_ascii=False)
        # UTF-8 bytes are an upper bound for byte-pair tokenization. This path is
        # only used when the official preflight endpoint is temporarily unavailable.
        return max(
            1,
            len(
                (
                    self._system_prompt
                    + user_message
                    + serialized_schema
                ).encode("utf-8")
            ),
        )

    @staticmethod
    def _item_count(user_message: str) -> int:
        try:
            payload = json.loads(user_message)
        except json.JSONDecodeError:
            return 1
        return max(1, len(payload) if isinstance(payload, list) else 1)

    def _parse_batch(
        self, raw: str, items: list[BatchItem]
    ) -> list[CheckResult] | None:
        """Parse batch response. Returns None on structural issues for recursive splitting."""
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

            if result.corrected is not None:
                restored = _restore_equivalent_variants(it.content, result.corrected)
                if restored == it.content:
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
                elif restored != result.corrected:
                    result = CheckResult(
                        corrected=restored,
                        changes=_filter_equivalent_change_descriptions(result.changes),
                        warning=result.warning,
                    )

            results.append(result)
        return results
