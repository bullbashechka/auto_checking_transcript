from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from . import xlsx_processor
from .config import Settings
from .llm import CheckResult, LLMClient
from .xlsx_processor import Correction, WorkEntry, Warning_

log = logging.getLogger(__name__)


@dataclass
class Report:
    total: int
    corrections: list[tuple[WorkEntry, list[str]]]
    warnings: list[tuple[WorkEntry, str]]

    def render(self) -> str:
        lines = [
            f"✓ Обработано строк: {self.total}",
            f"✓ Исправлено: {len(self.corrections)}",
            f"⚠ Предупреждения: {len(self.warnings)}",
        ]

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


async def process_file(input_path: Path, settings: Settings) -> tuple[Path, Report]:
    wb, entries, _sheet = xlsx_processor.parse(input_path)
    log.info("Parsed %d work entries from %s", len(entries), input_path.name)

    llm = LLMClient(settings)
    results = await asyncio.gather(
        *(llm.check(e.contractor, e.date, e.time, e.content) for e in entries),
        return_exceptions=True,
    )

    corrections: list[Correction] = []
    warnings: list[Warning_] = []
    report_corrections: list[tuple[WorkEntry, list[str]]] = []
    report_warnings: list[tuple[WorkEntry, str]] = []

    for entry, result in zip(entries, results):
        if isinstance(result, BaseException):
            log.exception("LLM call failed for row %d: %s", entry.row_idx, result)
            continue
        if not isinstance(result, CheckResult):
            continue
        if result.is_empty:
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
                Warning_(row_idx=entry.row_idx, content_col=entry.content_col)
            )
            report_warnings.append((entry, result.warning))

    output_path = xlsx_processor.write_result(wb, corrections, warnings, input_path)
    report = Report(
        total=len(entries),
        corrections=report_corrections,
        warnings=report_warnings,
    )
    return output_path, report
