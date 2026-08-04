from __future__ import annotations

import importlib.util
import unittest

HAS_GENAI = importlib.util.find_spec("google.genai") is not None

if HAS_GENAI:
    from src import checker


@unittest.skipUnless(HAS_GENAI, "google-genai not installed — run `pip install -r requirements.txt`")
class TestTextNormalization(unittest.TestCase):
    def test_collapses_two_or_more_plain_spaces(self) -> None:
        cases = {
            "на  1 место": "на 1 место",
            "между   словами": "между словами",
            "один пробел": "один пробел",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                fixed, _changes = checker._normalize_mechanical_spacing(source)
                self.assertEqual(fixed, expected)

    def test_preserves_tabs_and_newlines(self) -> None:
        source = "один\t\tдва\n\nтри"
        fixed, changes = checker._normalize_mechanical_spacing(source)
        self.assertEqual(fixed, source)
        self.assertEqual(changes, [])

    def test_removes_spaces_inside_allowed_domains(self) -> None:
        cases = {
            "openai. com": "openai.com",
            "openai .com": "openai.com",
            "www. openai.com": "www.openai.com",
            "https://openai. com/path": "https://openai.com/path",
            "http://portal .kz/login": "http://portal.kz/login",
            "user@openai. com": "user@openai.com",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                fixed, _changes = checker._normalize_mechanical_spacing(source)
                self.assertEqual(fixed, expected)

    def test_supports_every_allowed_domain_zone_case_insensitively(self) -> None:
        for zone in ("kz", "ru", "com", "org", "net", "рф", "қаз", "COM"):
            with self.subTest(zone=zone):
                fixed, _changes = checker._normalize_mechanical_spacing(f"site. {zone}")
                self.assertEqual(fixed, f"site.{zone}")

    def test_does_not_modify_correct_addresses(self) -> None:
        source = "openai.com user@site.kz https://portal.org/path"
        fixed, changes = checker._normalize_mechanical_spacing(source)
        self.assertEqual(fixed, source)
        self.assertEqual(changes, [])

    def test_keeps_sentence_spacing_without_breaking_protected_tokens(self) -> None:
        cases = {
            "Сделано.Следующее": "Сделано. Следующее",
            "Сделано.  Следующее": "Сделано. Следующее",
            "Версия 3.0.72.1": "Версия 3.0.72.1",
            "Иванов И.И.": "Иванов И.И.",
            "ф.300.00 рег.номер т.е.пример": "ф.300.00 рег.номер т.е.пример",
            "openai.com user@site.kz": "openai.com user@site.kz",
            "user.name@site.kz": "user.name@site.kz",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                fixed, _changes = checker._normalize_mechanical_spacing(source)
                self.assertEqual(fixed, expected)

    def test_reports_each_kind_of_change_once(self) -> None:
        fixed, changes = checker._normalize_mechanical_spacing(
            "Сайт openai. com  проверен.Следующее"
        )
        self.assertEqual(fixed, "Сайт openai.com проверен. Следующее")
        self.assertEqual(
            changes,
            [
                "исправлены пробелы внутри адреса сайта или email",
                "двойные пробелы заменены одним",
                "добавлен пробел после знака препинания",
            ],
        )


if __name__ == "__main__":
    unittest.main()
