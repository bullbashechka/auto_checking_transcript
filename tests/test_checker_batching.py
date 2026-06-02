"""Тесты chunking-логики checker.process_file без сетевых вызовов."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook

HAS_GENAI = importlib.util.find_spec("google.genai") is not None

if HAS_GENAI:
    from src import checker
    from src.llm import BATCH_SIZE, CheckResult


def _make_workbook_with_entries(n: int) -> Path:
    """Создаёт xlsx в трёхстрочной раскладке с n записями под одним контрагентом."""
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Время нач"
    ws["D1"] = "Содержание"
    ws["A2"] = "ООО Тест"  # контрагент
    row = 3
    for i in range(n):
        ws.cell(row=row, column=1).value = "01.01.2026"
        row += 1
        ws.cell(row=row, column=1).value = f"{9 + i:02d}:00:00"
        ws.cell(row=row, column=4).value = f"Работа {i}"
        row += 1
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "input.xlsx"
    wb.save(path)
    return path


@unittest.skipUnless(HAS_GENAI, "google-genai not installed — run `pip install -r requirements.txt`")
class TestCheckerBatching(unittest.TestCase):
    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_restores_missing_space_after_sentence_dot(self) -> None:
        fixed, changed = checker._ensure_space_after_sentence_punctuation(
            "Первое предложение.меня зовут кирилл"
        )
        self.assertTrue(changed)
        self.assertEqual(fixed, "Первое предложение. меня зовут кирилл")

    def test_collapses_extra_spaces_after_sentence_dot_to_one(self) -> None:
        fixed, changed = checker._ensure_space_after_sentence_punctuation(
            "Первое предложение.  Следующее предложение"
        )
        self.assertTrue(changed)
        self.assertEqual(fixed, "Первое предложение. Следующее предложение")

    def test_restores_missing_space_after_other_sentence_punctuation(self) -> None:
        fixed, changed = checker._ensure_space_after_sentence_punctuation(
            "Готово!следующее действие?проверить"
        )
        self.assertTrue(changed)
        self.assertEqual(fixed, "Готово! следующее действие? проверить")

    def test_does_not_break_known_dot_abbreviations(self) -> None:
        fixed, changed = checker._ensure_space_after_sentence_punctuation(
            "Регистрация физ.лица и рег.номер через т.е.пример"
        )
        self.assertFalse(changed)
        self.assertEqual(fixed, "Регистрация физ.лица и рег.номер через т.е.пример")

    def test_batch_size_is_three(self) -> None:
        self.assertEqual(BATCH_SIZE, 3, "размер батча зафиксирован = 3")

    def test_chunks_into_batches_of_3(self) -> None:
        path = _make_workbook_with_entries(10)
        mock_llm = AsyncMock()
        mock_llm.check_batch.return_value = [CheckResult() for _ in range(3)]

        async def side_effect(items):
            return [CheckResult() for _ in items]

        mock_llm.check_batch.side_effect = side_effect
        self._run(checker.process_file(path, mock_llm))
        # 10 entries / 3 = 4 батча (3+3+3+1)
        self.assertEqual(mock_llm.check_batch.await_count, 4)
        batch_sizes = [len(call.args[0]) for call in mock_llm.check_batch.await_args_list]
        self.assertEqual(sorted(batch_sizes, reverse=True), [3, 3, 3, 1])

    def test_preserves_entry_order(self) -> None:
        path = _make_workbook_with_entries(7)

        async def side_effect(items):
            # отдаём в каждом ответе текст-маркер с id и content
            return [
                CheckResult.model_validate(
                    {"Исправленное_Содержание": f"fixed-{it.content}", "Изменения": ["x"]}
                )
                for it in items
            ]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        _, report = self._run(checker.process_file(path, mock_llm))
        # все 7 правок должны быть на месте, в порядке исходных строк
        self.assertEqual(len(report.corrections), 7)
        contents = [entry.content for entry, _ in report.corrections]
        self.assertEqual(contents, [f"Работа {i}" for i in range(7)])

    def test_progress_log_uses_batch_units(self) -> None:
        path = _make_workbook_with_entries(6)

        async def side_effect(items):
            return [CheckResult() for _ in items]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect

        with self.assertLogs("src.checker", level="INFO") as captured:
            self._run(checker.process_file(path, mock_llm))

        progress_lines = [m for m in captured.output if "batch progress" in m]
        self.assertTrue(progress_lines, "должна быть хотя бы одна строка прогресса по батчам")
        last = progress_lines[-1]
        self.assertIn("2/2 batches", last)
        self.assertIn("6 / 6 entries", last)

    def test_exception_in_check_batch_marks_errors_for_whole_batch(self) -> None:
        path = _make_workbook_with_entries(4)

        async def side_effect(items):
            if items[0].content == "Работа 0":
                raise RuntimeError("network died")
            return [CheckResult() for _ in items]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        _, report = self._run(checker.process_file(path, mock_llm))
        # первый батч (3 шт.) упал — все три уходят в errors
        self.assertEqual(report.errors, 3)


if __name__ == "__main__":
    unittest.main()
