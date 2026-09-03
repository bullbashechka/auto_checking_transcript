"""Тесты chunking-логики checker.process_file без сетевых вызовов."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook, load_workbook

HAS_OPENAI = importlib.util.find_spec("openai") is not None

if HAS_OPENAI:
    from src import checker
    from src.llm import BATCH_SIZE, CheckResult
    from src.xlsx_processor import HIGHLIGHT_FILL


def _make_workbook_with_contents(contents: list[str]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Время нач"
    ws["D1"] = "Содержание"
    ws["A2"] = "ООО Тест"
    row = 3
    for i, content in enumerate(contents):
        ws.cell(row=row, column=1).value = "01.01.2026"
        row += 1
        ws.cell(row=row, column=1).value = f"{9 + i:02d}:00:00"
        ws.cell(row=row, column=4).value = content
        row += 1
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "input.xlsx"
    wb.save(path)
    return path


def _make_workbook_with_entries(n: int) -> Path:
    return _make_workbook_with_contents([f"Работа {i}" for i in range(n)])


@unittest.skipUnless(HAS_OPENAI, "openai not installed — run `pip install -r requirements.txt`")
class TestCheckerBatching(unittest.TestCase):
    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

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

    def test_does_not_insert_space_between_initials(self) -> None:
        fixed, changed = checker._ensure_space_after_sentence_punctuation(
            "Настройка пользователя Иванов И.И."
        )
        self.assertFalse(changed)
        self.assertEqual(fixed, "Настройка пользователя Иванов И.И.")

    def test_normalizes_extra_space_between_surname_initials(self) -> None:
        fixed, changed = checker._normalize_surname_initials(
            "Настройка пользователя Иванов И. И."
        )
        self.assertTrue(changed)
        self.assertEqual(fixed, "Настройка пользователя Иванов И.И.")

    def test_batch_size_is_fifteen(self) -> None:
        self.assertEqual(BATCH_SIZE, 15, "размер батча зафиксирован = 15")

    def test_chunks_into_batches_of_15(self) -> None:
        path = _make_workbook_with_entries(32)
        mock_llm = AsyncMock()

        async def side_effect(items):
            return [CheckResult() for _ in items]

        mock_llm.check_batch.side_effect = side_effect
        self._run(checker.process_file(path, mock_llm))
        # 32 entries / 15 = 3 батча (15+15+2)
        batch_sizes = [len(call.args[0]) for call in mock_llm.check_batch.await_args_list]
        self.assertEqual(mock_llm.check_batch.await_count, 3)
        self.assertEqual(sorted(batch_sizes, reverse=True), [15, 15, 2])

    def test_run_preserves_current_event_loop(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            current_loop = asyncio.get_event_loop()

        self._run(asyncio.sleep(0))

        try:
            current_loop_after_run = asyncio.get_event_loop()
        except RuntimeError:
            self.fail("_run cleared the current event loop")
        self.assertIs(current_loop_after_run, current_loop)

    def test_sends_pre_normalized_content_to_llm_and_reports_changes(self) -> None:
        path = _make_workbook_with_contents(["Проверка  openai. com"])

        async def side_effect(items):
            self.assertEqual(items[0].content, "Проверка openai.com")
            return [CheckResult()]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        output_path, report = self._run(checker.process_file(path, mock_llm))

        self.assertTrue(output_path.exists())
        self.assertEqual(len(report.corrections), 1)
        _entry, changes = report.corrections[0]
        self.assertIn("исправлены пробелы внутри адреса сайта или email", changes)
        self.assertIn("двойные пробелы заменены одним", changes)
        written_wb = load_workbook(output_path)
        self.assertEqual(
            written_wb.active["D4"].fill.start_color.rgb,
            HIGHLIGHT_FILL.start_color.rgb,
        )

    def test_normalizes_contextual_notices_before_llm_and_in_report(self) -> None:
        path = _make_workbook_with_contents(
            [
                "Работа  по извещениям",
                "Обязательство по Извещениям",
                "Проверка по извещениям",
            ]
        )

        async def side_effect(items):
            self.assertEqual(
                [item.content for item in items],
                [
                    "Работа по Извещениям",
                    "Обязательство по извещениям",
                    "Проверка по Извещениям",
                ],
            )
            return [CheckResult() for _ in items]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        output_path, report = self._run(checker.process_file(path, mock_llm))

        self.assertEqual(len(report.corrections), 3)
        work_changes = report.corrections[0][1]
        obligation_changes = report.corrections[1][1]
        self.assertIn("двойные пробелы заменены одним", work_changes)
        self.assertIn("уточнён регистр «Извещениям» в формулировке работы", work_changes)
        self.assertIn(
            "уточнён регистр «извещениям» в названии документа", obligation_changes
        )
        other_changes = report.corrections[2][1]
        self.assertIn("уточнён регистр «Извещениям»", other_changes)
        written_wb = load_workbook(output_path)
        self.assertEqual(written_wb.active["E4"].value, "Работа по Извещениям.")
        self.assertEqual(
            written_wb.active["E6"].value, "Обязательство по извещениям."
        )
        self.assertEqual(written_wb.active["E8"].value, "Проверка по Извещениям.")

    def test_normalizes_forms_and_periods_before_and_after_llm(self) -> None:
        source = (
            "Вопросы по заполнению ЭДВС (остатки) для оприходования источников "
            "происхождения с NTIN, по заполнению 300 ф. за 2кв.2026г. в ИБ"
        )
        expected_for_llm = (
            "Вопросы по заполнению ЭДВС (остатки) для оприходования источников "
            "происхождения с NTIN, по заполнению 300ф. за 2кв. 2026г. в ИБ"
        )
        path = _make_workbook_with_contents([source])

        async def side_effect(items):
            self.assertEqual(items[0].content, expected_for_llm)
            return [CheckResult(corrected=source)]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        output_path, report = self._run(checker.process_file(path, mock_llm))

        self.assertEqual(len(report.corrections), 1)
        _entry, changes = report.corrections[0]
        self.assertEqual(
            changes.count("убран пробел между номером формы и сокращением «ф.»"),
            1,
        )
        self.assertEqual(
            changes.count("нормализована запись отчётного периода"),
            1,
        )
        written_wb = load_workbook(output_path)
        self.assertEqual(
            written_wb.active["E4"].value,
            expected_for_llm + ".",
        )

    def test_normalizes_llm_output_and_merges_changes_without_duplicates(self) -> None:
        path = _make_workbook_with_contents(["Проверка openai. com"])

        async def side_effect(items):
            self.assertEqual(items[0].content, "Проверка openai.com")
            return [
                CheckResult.model_validate(
                    {
                        "Исправленное_Содержание": "Проверка openai. com  выполнена",
                        "Изменения": ["добавлено слово «выполнена»"],
                    }
                )
            ]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        _output_path, report = self._run(checker.process_file(path, mock_llm))

        self.assertEqual(len(report.corrections), 1)
        _entry, changes = report.corrections[0]
        self.assertEqual(changes.count("исправлены пробелы внутри адреса сайта или email"), 1)
        self.assertEqual(changes.count("двойные пробелы заменены одним"), 1)
        self.assertIn("добавлено слово «выполнена»", changes)
        self.assertIn("добавлена точка в конце", changes)

    def test_preserves_equivalent_variants_when_llm_also_corrects(self) -> None:
        path = _make_workbook_with_contents(["Учет в с/х. Опечатка"])

        async def side_effect(items):
            return [
                CheckResult(
                    corrected="Учёт в СХ. Исправлена",
                    changes=["исправлена опечатка", "заменено ё на е"],
                )
                for _item in items
            ]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        output_path, report = self._run(checker.process_file(path, mock_llm))

        self.assertEqual(len(report.corrections), 1)
        self.assertIn("исправлена опечатка", report.corrections[0][1])
        self.assertFalse(any("ё" in change or "СХ" in change for change in report.corrections[0][1]))
        self.assertEqual(
            load_workbook(output_path).active["E4"].value,
            "Учет в с/х. Исправлена.",
        )

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
        path = _make_workbook_with_entries(16)

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
        self.assertIn("16 / 16 entries", last)

    def test_exception_in_check_batch_marks_errors_for_whole_batch(self) -> None:
        path = _make_workbook_with_entries(4)

        async def side_effect(items):
            if items[0].content == "Работа 0":
                raise RuntimeError("network died")
            return [CheckResult() for _ in items]

        mock_llm = AsyncMock()
        mock_llm.check_batch.side_effect = side_effect
        _, report = self._run(checker.process_file(path, mock_llm))
        # единственный батч (4 шт.) упал — все четыре уходят в errors
        self.assertEqual(report.errors, 4)


if __name__ == "__main__":
    unittest.main()
