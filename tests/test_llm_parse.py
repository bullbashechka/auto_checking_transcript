"""Тесты на парсинг ответа Gemini без обращения к сети.

Запуск: `python -m unittest tests.test_llm_parse` из корня проекта.
"""
from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

HAS_GENAI = importlib.util.find_spec("google.genai") is not None

if HAS_GENAI:
    from src.config import Settings
    from src.llm import LLMClient


def _stub_settings() -> "Settings":
    return Settings(
        telegram_token="x",
        gemini_api_key="x",
        allowed_user_ids=frozenset(),
        allow_any=True,
        gemini_model="gemini-2.5-flash",
        llm_concurrency=1,
    )


@unittest.skipUnless(HAS_GENAI, "google-genai not installed — run `pip install -r requirements.txt`")
class TestParse(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.llm.genai.Client")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = LLMClient(_stub_settings())

    def test_empty_object_returns_empty_result(self) -> None:
        result = self.client._parse("{}", original_content="hello")
        self.assertTrue(result.is_empty)

    def test_blank_response_returns_empty_result(self) -> None:
        result = self.client._parse("", original_content="hello")
        self.assertTrue(result.is_empty)

    def test_correction_only(self) -> None:
        raw = '{"Исправленное_Содержание": "Вопрос", "Изменения": ["было «Вопро» → стало «Вопрос»"]}'
        result = self.client._parse(raw, original_content="Вопро")
        self.assertEqual(result.corrected, "Вопрос")
        self.assertEqual(result.changes, ["было «Вопро» → стало «Вопрос»"])
        self.assertIsNone(result.warning)

    def test_warning_only(self) -> None:
        raw = '{"Предупреждение": "Незаполненный placeholder: «(чей ПК)»"}'
        result = self.client._parse(raw, original_content="...")
        self.assertIsNone(result.corrected)
        self.assertEqual(result.warning, "Незаполненный placeholder: «(чей ПК)»")

    def test_both_correction_and_warning(self) -> None:
        raw = (
            '{"Исправленное_Содержание": "fixed", "Изменения": ["x"], '
            '"Предупреждение": "(чей ПК)"}'
        )
        result = self.client._parse(raw, original_content="original")
        self.assertEqual(result.corrected, "fixed")
        self.assertEqual(result.warning, "(чей ПК)")

    def test_corrected_equal_to_original_is_dropped(self) -> None:
        """Защита от моделей, которые возвращают тот же текст, объявляя 'исправление'."""
        raw = '{"Исправленное_Содержание": "same", "Изменения": ["fake"]}'
        result = self.client._parse(raw, original_content="same")
        self.assertIsNone(result.corrected)
        self.assertEqual(result.changes, [])

    def test_corrected_equal_to_original_keeps_warning(self) -> None:
        raw = '{"Исправленное_Содержание": "same", "Предупреждение": "warn"}'
        result = self.client._parse(raw, original_content="same")
        self.assertIsNone(result.corrected)
        self.assertEqual(result.warning, "warn")

    def test_invalid_json_returns_empty(self) -> None:
        result = self.client._parse("not json at all", original_content="x")
        self.assertTrue(result.is_empty)

    def test_json_array_returns_empty(self) -> None:
        result = self.client._parse('["nope"]', original_content="x")
        self.assertTrue(result.is_empty)

    def test_extra_fields_are_ignored(self) -> None:
        raw = '{"Исправленное_Содержание": "y", "Unknown_Field": 42}'
        result = self.client._parse(raw, original_content="x")
        self.assertEqual(result.corrected, "y")


if __name__ == "__main__":
    unittest.main()
