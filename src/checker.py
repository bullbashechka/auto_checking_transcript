from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import xlsx_processor
from .llm import CheckResult, LLMClient
from .xlsx_processor import Correction, WarningCell, WorkEntry

log = logging.getLogger(__name__)


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
    log.info("Parsed %d work entries from %s", len(entries), input_path.name)

    results = await asyncio.gather(
        *(llm.check(e.contractor, e.date, e.time, e.content) for e in entries),
        return_exceptions=True,
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
        if not isinstance(result, CheckResult) or result.is_empty:
            continue

        if result.corrected:
            corrections.append(
                Correction(
                    row_idx=entry.row_idx,
                    content_col=entry.content_col,
                    new_content=result.corrected,
                )
            )
            report_corrections.append((entry, result.changes))

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
