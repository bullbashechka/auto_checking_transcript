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

    def test_contains_bank_brand_normalization_rule(self) -> None:
        for fragment in (
            "KaspiBank",
            "Халык",
            "kaspi bank",
            "халык",
            "kaspi bank, халык",
            "KaspiBank, Халык",
            "исключение из правила «собственные имена — символ в символ»",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.prompt)

    def test_contains_year_and_quarter_spacing_rule(self) -> None:
        for fragment in (
            "«2026года» → «2026 года»",
            "«2026 г.» → «2026г.»",
            "«1квартал» → «1 квартал»",
            "«2квартала» → «2 квартала»",
            "«1 кв.» → «1кв.»",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.prompt)

        self.assertNotIn("«1кв.» → «1 кв.»,", self.prompt)

    def test_allows_both_1c_webkassa_spellings(self) -> None:
        self.assertIn(
            "«1C:WebKassa» с латинской C и «1С:WebKassa» с кириллической С "
            "одинаково корректны",
            self.prompt,
        )
        self.assertNotIn("«1C» пишется ЛАТИНИЦЕЙ", self.prompt)

    def test_explicitly_forbids_inflecting_domain_abbreviations(self) -> None:
        self.assertIn("Все следующие сокращения несклоняемы", self.prompt)

        for abbreviation in (
            "ИТС", "ЭАВР", "СНТ", "ЭСФ", "ЭЦП", "ЭДО", "ОФД", "ИС",
            "ИСНА", "КНП", "ГЗ", "БК", "УНФ", "УТ", "КА", "СЛК", "ИБ",
            "ТМЗ", "ООСМС", "ОСМС", "ВОСМС", "ОПВ", "ОПВР", "СО", "ИПН",
            "КПН", "НКТ", "НДС", "ФНО", "ОСВ", "НУ", "БУ", "ПР", "ВР",
            "ФА", "ЛС", "НП", "МРП", "СХ", "ГЕМ", "ВС", "ПК", "ТОО",
            "ИП", "СБ", "с/б", "ЗП", "з/п", "БЛ", "б/л", "ИИН", "ИНН",
            "БИН",
        ):
            with self.subTest(abbreviation=abbreviation):
                self.assertIn(f"«{abbreviation}»", self.prompt)

        self.assertIn("не добавляй к ним падежные окончания", self.prompt)
        self.assertIn("исправляй склонённую форму на исходное сокращение", self.prompt)

    def test_keeps_json_contract(self) -> None:
        for field in (
            '"id"',
            '"Исправленное_Содержание"',
            '"Изменения"',
            '"Предупреждение"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.prompt)

        self.assertIn('{"results": [...]}', self.prompt)


if __name__ == "__main__":
    unittest.main()
