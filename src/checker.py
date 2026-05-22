from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import xlsx_processor
from .llm import CheckResult, LLMClient
from .xlsx_processor import Correction, WarningCell, WorkEntry

log = logging.getLogger(__name__)

_TRAILING_PUNCT = {".", "!", "?", "…"}


def _ensure_trailing_dot(text: str) -> tuple[str, bool]:
    """Гарантирует точку в конце ячейки. Возвращает (новый_текст, была_ли_добавлена)."""
    stripped = text.rstrip()
    if not stripped or stripped[-1] in _TRAILING_PUNCT:
        return text, False
    return stripped + ".", True


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


async def process_file(input_path: Path, llm: LLMClient) -> tuple[Path, Report]:
    wb, entries, header_row = xlsx_processor.parse(input_path)
    total = len(entries)
    log.info("Parsed %d work entries from '%s'", total, input_path.name)

    progress_step = max(1, total // 10)  # лог каждые ~10%
    done = 0

    async def _check_with_progress(entry: WorkEntry):
        nonlocal done
        try:
            return await llm.check(entry.contractor, entry.date, entry.time, entry.content)
        finally:
            done += 1
            if done % progress_step == 0 or done == total:
                log.info("LLM progress: %d / %d (%.0f%%)", done, total, 100 * done / total)

    results = await asyncio.wait_for(
        asyncio.gather(
            *(_check_with_progress(e) for e in entries),
            return_exceptions=True,
        ),
        timeout=600,
    )

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
        final_text, dot_added = _ensure_trailing_dot(base_text)
        changes = list(result.changes)
        if dot_added:
            changes.append("добавлена точка в конце")

        if final_text != entry.content:
            corrections.append(
                Correction(
                    row_idx=entry.row_idx,
                    content_col=entry.content_col,
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
