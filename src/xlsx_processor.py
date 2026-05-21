from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.workbook import Workbook


HIGHLIGHT_FILL = PatternFill(start_color="FFFFF2A8", end_color="FFFFF2A8", fill_type="solid")
WARNING_FILL = PatternFill(start_color="FFFFC7C7", end_color="FFFFC7C7", fill_type="solid")
COMBINED_FILL = PatternFill(start_color="FFFFB14D", end_color="FFFFB14D", fill_type="solid")


@dataclass(frozen=True)
class WorkEntry:
    contractor: str
    date: str
    time: str
    content: str
    row_idx: int  # 1-based row in the sheet
    content_col: int  # 1-based column index of «Содержание»


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _find_content_column(ws) -> tuple[int, int]:
    """Returns (header_row, content_col) — 1-based indices."""
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
        for col_idx, value in enumerate(row, 1):
            if _cell_text(value).lower() == "содержание":
                return row_idx, col_idx
    raise ValueError("Не найдена колонка «Содержание» в первых 10 строках листа")


def parse(path: Path | str) -> tuple[Workbook, list[WorkEntry], str]:
    """Open the workbook, locate the «Содержание» column, walk the two-level table.

    Returns (workbook, entries, sheet_name).
    """
    wb = load_workbook(path)
    ws = wb.active

    header_row, content_col = _find_content_column(ws)

    entries: list[WorkEntry] = []
    current_contractor = ""

    for row_idx in range(header_row + 1, ws.max_row + 1):
        col_a = _cell_text(ws.cell(row=row_idx, column=1).value)
        content = _cell_text(ws.cell(row=row_idx, column=content_col).value)

        if not content:
            if col_a:
                current_contractor = col_a
            continue

        time_val = ws.cell(row=row_idx, column=3).value
        time_str = _format_time(time_val)

        entries.append(
            WorkEntry(
                contractor=current_contractor,
                date=col_a,
                time=time_str,
                content=content,
                row_idx=row_idx,
                content_col=content_col,
            )
        )

    return wb, entries, ws.title


def _format_time(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")
    return str(value).strip()


@dataclass(frozen=True)
class Correction:
    row_idx: int
    content_col: int
    new_content: str


@dataclass(frozen=True)
class WarningCell:
    row_idx: int
    content_col: int


def write_result(
    wb: Workbook,
    corrections: list[Correction],
    warnings: list[WarningCell],
    input_path: Path,
    output_dir: Path | None = None,
) -> Path:
    """Apply corrections and warning highlights, then save with derived filename.

    Если на одну строку приходится и correction, и warning — используется
    отдельный COMBINED_FILL, чтобы оба сигнала остались видимы.
    """
    ws = wb.active

    correction_rows = {c.row_idx for c in corrections}
    warning_rows = {w.row_idx for w in warnings}
    combined_rows = correction_rows & warning_rows

    for corr in corrections:
        cell = ws.cell(row=corr.row_idx, column=corr.content_col)
        cell.value = corr.new_content
        cell.fill = COMBINED_FILL if corr.row_idx in combined_rows else HIGHLIGHT_FILL

    for warn in warnings:
        if warn.row_idx in combined_rows:
            continue
        cell = ws.cell(row=warn.row_idx, column=warn.content_col)
        cell.fill = WARNING_FILL

    stem = input_path.stem
    suffix = date.today().strftime("%Y-%m-%d")
    out_name = f"{stem}_исправленное_{suffix}.xlsx"
    out_dir = output_dir or input_path.parent
    out_path = out_dir / out_name

    wb.save(out_path)
    return out_path
