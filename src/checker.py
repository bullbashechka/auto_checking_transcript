from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from . import xlsx_processor
from .llm import BATCH_SIZE, BatchItem, CheckResult, LLMClient
from .xlsx_processor import Correction, WarningCell, WorkEntry

log = logging.getLogger(__name__)

_TRAILING_PUNCT = {".", "!", "?", "…"}
_SPACE_AFTER_PUNCT_RE = re.compile(r"([.!?…])([ \t]*)([A-Za-zА-Яа-яЁё])")
_SURNAME_INITIALS_SPACING_RE = re.compile(r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ])\.\s+([А-ЯЁ])\.")
_DOT_ABBREVIATIONS_WITHOUT_SPACE = {
    "рег",
    "физ",
    "т",
    "т.е",
    "т.д",
    "т.п",
    "н-р",
}
_ALLOWED_DOMAIN_ZONES = ("kz", "ru", "com", "org", "net", "рф", "қаз")
_DOMAIN_LABEL = r"[^\W_](?:(?:[^\W_]|-)*[^\W_])?"
_EMAIL_LOCAL = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
_DOMAIN_ZONE_PATTERN = "|".join(re.escape(zone) for zone in _ALLOWED_DOMAIN_ZONES)
_ADDRESS_WITH_SPACES_RE = re.compile(
    rf"(?<![\w-])"
    rf"(?:(?:{_EMAIL_LOCAL})|(?:https?://)?)"
    rf"(?:{_DOMAIN_LABEL} *\. *)+"
    rf"(?:{_DOMAIN_ZONE_PATTERN})"
    rf"(?=$|[\s/:?#),;!?.…])",
    re.IGNORECASE,
)
_SPACES_AROUND_DOT_RE = re.compile(r" *\. *")
_REPEATED_PLAIN_SPACES_RE = re.compile(r" {2,}")

_ADDRESS_SPACING_CHANGE = "исправлены пробелы внутри адреса сайта или email"
_REPEATED_SPACES_CHANGE = "двойные пробелы заменены одним"
_SENTENCE_SPACING_CHANGE = "добавлен пробел после знака препинания"

ProgressCallback = Callable[[int, int, int, int], Awaitable[None]]


def _ensure_trailing_dot(text: str) -> tuple[str, bool]:
    """Гарантирует точку в конце ячейки. Возвращает (новый_текст, была_ли_добавлена)."""
    stripped = text.rstrip()
    if not stripped or stripped[-1] in _TRAILING_PUNCT:
        return text, False
    return stripped + ".", True


def _ensure_space_after_sentence_punctuation(text: str) -> tuple[str, bool]:
    """Оставляет один пробел после финальной пунктуации между предложениями."""
    protected_address_spans = _address_spans(text)

    def replace(match: re.Match[str]) -> str:
        punct = match.group(1)
        spaces = match.group(2)
        next_char = match.group(3)
        if punct == ".":
            dot_index = match.start(1)
            if any(start <= dot_index < end for start, end in protected_address_spans):
                return match.group(0)
            token_start = max(
                text.rfind(" ", 0, match.start(1)),
                text.rfind("\n", 0, match.start(1)),
                text.rfind("\t", 0, match.start(1)),
            ) + 1
            token = text[token_start:match.start(1)].strip("«»\"'()[]{}").lower()
            if token in _DOT_ABBREVIATIONS_WITHOUT_SPACE:
                return match.group(0)
            raw_token = text[token_start:match.start(1)].strip("«»\"'()[]{}")
            if len(raw_token) == 1 and raw_token.isupper() and next_char.isupper():
                return match.group(0)
        if spaces == " ":
            return match.group(0)
        return f"{punct} {next_char}"

    fixed = _SPACE_AFTER_PUNCT_RE.sub(replace, text)
    return fixed, fixed != text


def _normalize_address_spacing(text: str) -> tuple[str, bool]:
    def replace(match: re.Match[str]) -> str:
        return _SPACES_AROUND_DOT_RE.sub(".", match.group(0))

    fixed = _ADDRESS_WITH_SPACES_RE.sub(replace, text)
    return fixed, fixed != text


def _address_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _ADDRESS_WITH_SPACES_RE.finditer(text)]


def _normalize_mechanical_spacing(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []

    address_text, address_changed = _normalize_address_spacing(text)
    if address_changed:
        changes.append(_ADDRESS_SPACING_CHANGE)

    collapsed_text = _REPEATED_PLAIN_SPACES_RE.sub(" ", address_text)
    if collapsed_text != address_text:
        changes.append(_REPEATED_SPACES_CHANGE)

    sentence_text, sentence_changed = _ensure_space_after_sentence_punctuation(
        collapsed_text
    )
    if sentence_changed:
        changes.append(_SENTENCE_SPACING_CHANGE)

    return sentence_text, changes


def _normalize_surname_initials(text: str) -> tuple[str, bool]:
    """Убирает лишний пробел между инициалами после фамилии: Иванов И. И. -> Иванов И.И."""
    fixed = _SURNAME_INITIALS_SPACING_RE.sub(r"\1 \2.\3.", text)
    return fixed, fixed != text


@dataclass
class Report:
    total: int
    corrections: list[tuple[WorkEntry, list[str]]] = field(default_factory=list)
    warnings: list[tuple[WorkEntry, str]] = field(default_factory=list)
    errors: int = 0

    def render(self) -> str:
        lines = [
            f"✓ Обработано строк: {self.total}",
            f"✓ Исправлено: {len(self.corrections)}",
            f"⚠ Предупреждения: {len(self.warnings)}",
        ]
        if self.errors:
            lines.append(f"❌ Ошибок LLM (строки пропущены): {self.errors}")

        if self.warnings:
            lines.append("")
            lines.append("Предупреждения:")
            for entry, text in self.warnings:
                lines.append(f"   • Строка {entry.row_idx} ({entry.contractor}): {text}")

        if self.corrections:
            lines.append("")
            lines.append("Исправления:")
            for entry, changes in self.corrections[:20]:
                short = "; ".join(changes) if changes else "—"
                lines.append(f"   • Строка {entry.row_idx}: {short}")
            if len(self.corrections) > 20:
                lines.append(f"   … и ещё {len(self.corrections) - 20}")

        return "\n".join(lines)


async def process_file(
    input_path: Path,
    llm: LLMClient,
    on_progress: ProgressCallback | None = None,
) -> tuple[Path, Report]:
    wb, entries, header_row = xlsx_processor.parse(input_path)
    total = len(entries)
    log.info("Parsed %d work entries from '%s'", total, input_path.name)

    batches: list[list[WorkEntry]] = [
        entries[i : i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)
    ]
    total_batches = len(batches)
    progress_step = max(1, total_batches // 10)
    done_batches = 0
    done_entries = 0

    async def _run_batch(batch: list[WorkEntry]) -> list[CheckResult | BaseException]:
        nonlocal done_batches, done_entries
        items = [
            BatchItem(id=i, contractor=e.contractor, date=e.date, time=e.time, content=e.content)
            for i, e in enumerate(batch)
        ]
        try:
            results: list[CheckResult | BaseException] = list(await llm.check_batch(items))
        except BaseException as err:  # noqa: BLE001
            results = [err] * len(batch)
        finally:
            done_batches += 1
            done_entries += len(batch)
            if done_batches % progress_step == 0 or done_batches == total_batches:
                log.info(
                    "LLM batch progress: %d/%d batches (%d / %d entries, %.0f%%)",
                    done_batches, total_batches, done_entries, total,
                    100 * done_entries / total if total else 0,
                )
            if on_progress is not None:
                try:
                    await on_progress(done_batches, total_batches, done_entries, total)
                except Exception:  # noqa: BLE001
                    log.exception("progress callback failed")
        return results

    batch_results = await asyncio.wait_for(
        asyncio.gather(*(_run_batch(b) for b in batches), return_exceptions=False),
        timeout=600,
    )

    results: list[CheckResult | BaseException] = [r for batch in batch_results for r in batch]

    corrections: list[Correction] = []
    warnings: list[WarningCell] = []
    report_corrections: list[tuple[WorkEntry, list[str]]] = []
    report_warnings: list[tuple[WorkEntry, str]] = []
    errors = 0

    for entry, result in zip(entries, results):
        if isinstance(result, BaseException):
            log.error("LLM call failed for row %d", entry.row_idx, exc_info=result)
            errors += 1
            continue
        if not isinstance(result, CheckResult):
            continue

        base_text = result.corrected if result.corrected else entry.content
        spaced_text, space_added = _ensure_space_after_sentence_punctuation(base_text)
        initials_text, initials_normalized = _normalize_surname_initials(spaced_text)
        final_text, dot_added = _ensure_trailing_dot(initials_text)
        changes = list(result.changes)
        if space_added:
            changes.append("добавлен пробел после знака препинания")
        if initials_normalized:
            changes.append("убран лишний пробел между инициалами")
        if dot_added:
            changes.append("добавлена точка в конце")

        if final_text != entry.content:
            corrections.append(
                Correction(
                    row_idx=entry.row_idx,
                    content_col=entry.content_col,
                    original_content=entry.content,
                    new_content=final_text,
                )
            )
            report_corrections.append((entry, changes))

        if result.warning:
            warnings.append(
                WarningCell(row_idx=entry.row_idx, content_col=entry.content_col)
            )
            report_warnings.append((entry, result.warning))

    output_path = xlsx_processor.write_result(wb, corrections, warnings, input_path, header_row)
    return output_path, Report(
        total=len(entries),
        corrections=report_corrections,
        warnings=report_warnings,
        errors=errors,
    )
