"""Тесты на парсинг батч-ответа OpenAI без обращения к сети.

Запуск: `python -m unittest tests.test_llm_parse` из корня проекта.
"""
from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

HAS_OPENAI = importlib.util.find_spec("openai") is not None

if HAS_OPENAI:
    from src.config import Settings
    from src.llm import (
        BatchItem,
        LLMClient,
        _filter_equivalent_change_descriptions,
        _restore_equivalent_variants,
    )


def _stub_settings() -> "Settings":
    return Settings(
        telegram_token="x",
        openai_api_key="x",
        allowed_user_ids=frozenset(),
        allow_any=True,
        openai_model="gpt-5.6-luna",
        openai_reasoning_effort="low",
        llm_concurrency=1,
    )


def _make_items(n: int) -> list["BatchItem"]:
    return [
        BatchItem(id=i, contractor=f"c{i}", date="01.01.2026", time="09:00", content=f"text{i}")
        for i in range(n)
    ]


@unittest.skipUnless(HAS_OPENAI, "openai not installed — run `pip install -r requirements.txt`")
class TestParseBatch(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.llm.AsyncOpenAI")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = LLMClient(_stub_settings())

    def test_all_empty(self) -> None:
        items = _make_items(3)
        raw = '{"results": [{"id": 0}, {"id": 1}, {"id": 2}]}'
        results = self.client._parse_batch(raw, items)
        self.assertIsNotNone(results)
        assert results is not None
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertTrue(r.is_empty)

    def test_mixed_results(self) -> None:
        items = _make_items(3)
        raw = (
            '[{"id": 0, "Исправленное_Содержание": "fixed0", "Изменения": ["a"]},'
            ' {"id": 1, "Предупреждение": "warn1"},'
            ' {"id": 2, "Исправленное_Содержание": "fixed2", "Изменения": ["b"], "Предупреждение": "warn2"}]'
        )
        results = self.client._parse_batch(raw, items)
        assert results is not None
        self.assertEqual(results[0].corrected, "fixed0")
        self.assertEqual(results[1].warning, "warn1")
        self.assertEqual(results[2].corrected, "fixed2")
        self.assertEqual(results[2].warning, "warn2")

    def test_order_independent(self) -> None:
        items = _make_items(3)
        raw = (
            '[{"id": 2, "Исправленное_Содержание": "for-2"},'
            ' {"id": 0, "Исправленное_Содержание": "for-0"},'
            ' {"id": 1}]'
        )
        results = self.client._parse_batch(raw, items)
        assert results is not None
        self.assertEqual(results[0].corrected, "for-0")
        self.assertTrue(results[1].is_empty)
        self.assertEqual(results[2].corrected, "for-2")

    def test_corrected_equals_original_dropped(self) -> None:
        items = _make_items(2)
        # items[0].content == 'text0'
        raw = '[{"id": 0, "Исправленное_Содержание": "text0", "Изменения": ["fake"]}, {"id": 1}]'
        results = self.client._parse_batch(raw, items)
        assert results is not None
        self.assertIsNone(results[0].corrected)
        self.assertEqual(results[0].changes, [])

    def test_corrected_with_only_equivalent_variants_is_dropped(self) -> None:
        items = [
            BatchItem(
                id=0,
                contractor="c",
                date="01.01.2026",
                time="09:00",
                content="Учет в с/х",
            )
        ]
        raw = (
            '[{"id": 0, "Исправленное_Содержание": "Учёт в СХ", '
            '"Изменения": ["заменено ё и сокращение"]}]'
        )
        results = self.client._parse_batch(raw, items)
        assert results is not None
        self.assertIsNone(results[0].corrected)
        self.assertEqual(results[0].changes, [])

    def test_mixed_correction_preserves_equivalent_variants(self) -> None:
        items = [
            BatchItem(
                id=0,
                contractor="c",
                date="01.01.2026",
                time="09:00",
                content="Учет в с/х. Опечатка",
            )
        ]
        raw = (
            '[{"id": 0, "Исправленное_Содержание": "Учёт в СХ. Исправлена", '
            '"Изменения": ["исправлена опечатка", "заменена буква «ё» на «е»"]}]'
        )
        results = self.client._parse_batch(raw, items)
        assert results is not None
        self.assertEqual(results[0].corrected, "Учет в с/х. Исправлена")
        self.assertEqual(results[0].changes, ["исправлена опечатка"])

    def test_long_equivalent_text_is_restored_without_quadratic_alignment(self) -> None:
        original = "ё" * 16000 + " с/х"
        corrected = "е" * 16000 + " СХ"
        self.assertEqual(_restore_equivalent_variants(original, corrected), original)

    def test_whole_word_replacement_does_not_copy_equivalent_letters(self) -> None:
        self.assertEqual(_restore_equivalent_variants("всё", "лесной"), "лесной")
        self.assertEqual(_restore_equivalent_variants("ёлка", "зелёный"), "зелёный")
        self.assertEqual(
            _restore_equivalent_variants("для всё пользователей", "для всех пользователей"),
            "для всех пользователей",
        )
        self.assertEqual(
            _restore_equivalent_variants("провёл работы", "проведение работ"),
            "проведение работ",
        )
        self.assertEqual(_restore_equivalent_variants("своё", "своего"), "своего")

    def test_restores_variants_across_punctuation_and_inserted_tokens(self) -> None:
        self.assertEqual(_restore_equivalent_variants("Учёт,", "Учет."), "Учёт.")
        self.assertEqual(
            _restore_equivalent_variants("Учёт данных", "Добавлен полный учет данных"),
            "Добавлен полный учёт данных",
        )
        self.assertEqual(_restore_equivalent_variants("Учёт", "Учетный"), "Учетный")

    def test_does_not_reuse_variant_from_deleted_repeated_token(self) -> None:
        self.assertEqual(
            _restore_equivalent_variants("с/х СХ отчет", "СХ отчет"),
            "СХ отчет",
        )

    def test_filters_equivalent_clause_but_keeps_real_change(self) -> None:
        self.assertEqual(
            _filter_equivalent_change_descriptions(
                ["исправлена опечатка и заменена буква «ё» на «е»"]
            ),
            ["исправлена опечатка"],
        )
        self.assertEqual(
            _filter_equivalent_change_descriptions(["заменено «ё» на «е»"]),
            [],
        )
        self.assertEqual(
            _filter_equivalent_change_descriptions(["«СХ» заменено на «с/х»"]),
            [],
        )
        self.assertEqual(
            _filter_equivalent_change_descriptions(["СХ и с/х равнозначны"]),
            [],
        )
        self.assertEqual(
            _filter_equivalent_change_descriptions(
                ["исправлена опечатка в термине «с/х»"]
            ),
            ["исправлена опечатка в термине «с/х»"],
        )

    def test_invalid_json_returns_none(self) -> None:
        items = _make_items(3)
        self.assertIsNone(self.client._parse_batch("not json", items))

    def test_object_instead_of_array_returns_none(self) -> None:
        items = _make_items(2)
        self.assertIsNone(self.client._parse_batch('{"id": 0}', items))

    def test_wrong_length_returns_none(self) -> None:
        items = _make_items(3)
        raw = '[{"id": 0}, {"id": 1}]'
        self.assertIsNone(self.client._parse_batch(raw, items))

    def test_duplicate_ids_returns_none(self) -> None:
        items = _make_items(2)
        raw = '[{"id": 0}, {"id": 0}]'
        self.assertIsNone(self.client._parse_batch(raw, items))

    def test_missing_id_returns_none(self) -> None:
        items = _make_items(2)
        raw = '[{"id": 0}, {"Исправленное_Содержание": "x"}]'
        self.assertIsNone(self.client._parse_batch(raw, items))

    def test_id_out_of_range_returns_none(self) -> None:
        items = _make_items(2)
        raw = '[{"id": 0}, {"id": 99}]'
        self.assertIsNone(self.client._parse_batch(raw, items))

    def test_extra_fields_ignored(self) -> None:
        items = _make_items(1)
        raw = '[{"id": 0, "Исправленное_Содержание": "y", "Unknown_Field": 42}]'
        results = self.client._parse_batch(raw, items)
        assert results is not None
        self.assertEqual(results[0].corrected, "y")

    def test_non_dict_element_returns_none(self) -> None:
        items = _make_items(2)
        raw = '[{"id": 0}, "not-an-object"]'
        self.assertIsNone(self.client._parse_batch(raw, items))


if __name__ == "__main__":
    unittest.main()
