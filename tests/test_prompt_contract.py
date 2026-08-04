from __future__ import annotations

import unittest
from pathlib import Path


PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "prompts" / "system_prompt.txt"
)


class TestPromptContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def test_contains_domain_and_email_spacing_examples(self) -> None:
        for fragment in (
            "openai. com",
            "openai.com",
            "site .kz",
            "site.kz",
            "user@site. ru",
            "user@site.ru",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.prompt)

    def test_explicitly_preserves_correct_addresses(self) -> None:
        self.assertIn(
            "Не добавляй пробелы внутри корректных сайтов и email",
            self.prompt,
        )

    def test_keeps_json_contract(self) -> None:
        for field in (
            '"id"',
            '"Исправленное_Содержание"',
            '"Изменения"',
            '"Предупреждение"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.prompt)


if __name__ == "__main__":
    unittest.main()
