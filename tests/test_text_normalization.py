from __future__ import annotations

import importlib.util
import unittest

HAS_OPENAI = importlib.util.find_spec("openai") is not None

if HAS_OPENAI:
    from src import checker


@unittest.skipUnless(HAS_OPENAI, "openai not installed — run `pip install -r requirements.txt`")
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

    def test_normalizes_contextual_notices_case(self) -> None:
        cases = {
            "Работа по извещениям": (
                "Работа по Извещениям",
                "уточнён регистр «Извещениям» в формулировке работы",
            ),
            "Обязательство по Извещениям": (
                "Обязательство по извещениям",
                "уточнён регистр «извещениям» в названии документа",
            ),
            "Проверка по извещениям": (
                "Проверка по Извещениям",
                "уточнён регистр «Извещениям»",
            ),
            "Обязательство по ИЗВЕЩЕНИЯМ": (
                "Обязательство по извещениям",
                "уточнён регистр «извещениям» в названии документа",
            ),
            "Работа по Извещениям": ("Работа по Извещениям", None),
            "Проверка документа Извещение": ("Проверка документа Извещение", None),
        }
        for source, (expected, change) in cases.items():
            with self.subTest(source=source):
                fixed, changes = checker._normalize_contextual_notices_case(source)
                self.assertEqual(fixed, expected)
                self.assertEqual(changes, [] if change is None else [change])

    def test_normalizes_form_number_spacing(self) -> None:
        cases = {
            "300 ф.": "300ф.",
            "300.00 ф.": "300.00ф.",
            "300ф.": "300ф.",
            "ф.300.00": "ф.300.00",
            "5 т.": "5 т.",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                fixed, _changes = checker._normalize_form_and_period_spacing(source)
                self.assertEqual(fixed, expected)

    def test_normalizes_quarter_year_spacing(self) -> None:
        cases = {
            "2кв.2026г.": "2кв. 2026г.",
            "2 кв.2026 г.": "2кв. 2026г.",
            "2 кв. 2026 г.": "2кв. 2026г.",
            "1кв. 2026г.": "1кв. 2026г.",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                fixed, _changes = checker._normalize_form_and_period_spacing(source)
                self.assertEqual(fixed, expected)

    def test_normalizes_user_example_before_llm(self) -> None:
        source = (
            "Вопросы по заполнению ЭДВС (остатки) для оприходования источников "
            "происхождения с NTIN, по заполнению 300 ф. за 2кв.2026г. в ИБ"
        )
        expected = (
            "Вопросы по заполнению ЭДВС (остатки) для оприходования источников "
            "происхождения с NTIN, по заполнению 300ф. за 2кв. 2026г. в ИБ"
        )
        fixed, changes = checker._normalize_input_text(source)
        self.assertEqual(fixed, expected)
        self.assertIn("убран пробел между номером формы и сокращением «ф.»", changes)
        self.assertIn("нормализована запись отчётного периода", changes)

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

    def test_preserves_addresses_before_terminal_sentence_punctuation(self) -> None:
        cases = {
            "Адрес openai.com. Далее": "Адрес openai.com. Далее",
            "Адрес openai. com. Далее": "Адрес openai.com. Далее",
            "Адрес https://portal. org/path. Далее": "Адрес https://portal.org/path. Далее",
            "Адрес user@site. kz. Далее": "Адрес user@site.kz. Далее",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                fixed, _changes = checker._normalize_mechanical_spacing(source)
                self.assertEqual(fixed, expected)

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
