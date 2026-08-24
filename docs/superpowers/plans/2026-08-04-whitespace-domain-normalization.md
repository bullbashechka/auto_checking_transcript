# Whitespace and Domain Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Детерминированно исправлять повторные пробелы и пробелы внутри разрешённых доменов/email, не повреждая предложения, версии, сокращения и инициалы, а затем упростить соответствующие правила системного промта.

**Architecture:** В `src/checker.py` добавляется чистый нормализатор механических пробелов, который возвращает текст и список изменений. `process_file()` применяет его перед Gemini и после ответа Gemini, объединяет изменения без дублей, а существующие правила инициалов и финальной точки оставляет только в финальной постобработке. Системный промт сохраняет предметный словарь и шаблоны, но получает однозначный контракт для пробелов, сайтов и email.

**Tech Stack:** Python 3.11+, стандартный `re`, `unittest`, `openpyxl`, Pydantic, Google Gen AI SDK.

## Global Constraints

- Не читать, не выводить, не редактировать и не коммитить `.env`.
- Не переименовывать и не модифицировать клиентские xlsx в корне проекта.
- Выходной файл сохраняет формат имени `{stem}_исправленное_{YYYY-MM-DD}.xlsx`.
- Разрешённые доменные зоны: `.kz`, `.ru`, `.com`, `.org`, `.net`, `.рф`, `.қаз`; сравнение без учёта регистра.
- Схлопываются только последовательности из двух и более обычных пробелов U+0020; табуляции и переводы строк этим правилом не меняются.
- Python не вставляет пробелы в склейки `на1`, `2025г.` и подобные; это остаётся ответственностью Gemini.
- JSON-контракт Gemini, размер батча, конкурентность, имя модели и правила подсветки не изменяются.
- При ошибке LLM сохраняется текущая семантика: строка считается пропущенной и увеличивает `Report.errors`.
- Новые зависимости не добавляются.

---

### Task 1: Чистый нормализатор пробелов и защищённые доменные фрагменты

**Files:**
- Create: `tests/test_text_normalization.py`
- Modify: `src/checker.py:16-70`

**Interfaces:**
- Consumes: `str` из `WorkEntry.content` или `CheckResult.corrected`.
- Produces: `_normalize_mechanical_spacing(text: str) -> tuple[str, list[str]]`.
- Produces: обновлённое `_ensure_space_after_sentence_punctuation(text: str) -> tuple[str, bool]`, которое игнорирует точки внутри распознанных адресов.
- Change labels are exact constants: `исправлены пробелы внутри адреса сайта или email`, `двойные пробелы заменены одним`, `добавлен пробел после знака препинания`.

- [ ] **Step 1: Создать unit-тесты, воспроизводящие дефект и фиксирующие контракт**

Создать `tests/test_text_normalization.py`:

```python
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
```

- [ ] **Step 2: Запустить новый тест и подтвердить красную фазу**

Run:

```powershell
python -m unittest tests.test_text_normalization -v
```

Expected: `ERROR` с `AttributeError: module 'src.checker' has no attribute '_normalize_mechanical_spacing'`. На текущем коде отдельная регрессия `openai.com` → `openai. com` также воспроизводится через `_ensure_space_after_sentence_punctuation()`.

- [ ] **Step 3: Добавить регулярные выражения и чистые функции нормализации**

В `src/checker.py` рядом с существующими регулярными выражениями добавить:

```python
_ALLOWED_DOMAIN_ZONES = ("kz", "ru", "com", "org", "net", "рф", "қаз")
_DOMAIN_LABEL = r"[^\W_](?:(?:[^\W_]|-)*[^\W_])?"
_EMAIL_LOCAL = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
_DOMAIN_ZONE_PATTERN = "|".join(re.escape(zone) for zone in _ALLOWED_DOMAIN_ZONES)
_ADDRESS_WITH_SPACES_RE = re.compile(
    rf"(?<![\w-])"
    rf"(?:(?:{_EMAIL_LOCAL})|(?:https?://)?)"
    rf"(?:{_DOMAIN_LABEL} *\. *)+"
    rf"(?:{_DOMAIN_ZONE_PATTERN})"
    rf"(?=$|[\s/:?#),;!?])",
    re.IGNORECASE,
)
_SPACES_AROUND_DOT_RE = re.compile(r" *\. *")
_REPEATED_PLAIN_SPACES_RE = re.compile(r" {2,}")

_ADDRESS_SPACING_CHANGE = "исправлены пробелы внутри адреса сайта или email"
_REPEATED_SPACES_CHANGE = "двойные пробелы заменены одним"
_SENTENCE_SPACING_CHANGE = "добавлен пробел после знака препинания"
```

Добавить функции:

```python
def _normalize_address_spacing(text: str) -> tuple[str, bool]:
    def replace(match: re.Match[str]) -> str:
        return _SPACES_AROUND_DOT_RE.sub(".", match.group(0))

    fixed = _ADDRESS_WITH_SPACES_RE.sub(replace, text)
    return fixed, fixed != text


def _address_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _ADDRESS_WITH_SPACES_RE.finditer(text)]
```

В `_ensure_space_after_sentence_punctuation()` до вложенной `replace()` вычислить `protected_address_spans = _address_spans(text)`. В начале обработки точки добавить:

```python
dot_index = match.start(1)
if any(start <= dot_index < end for start, end in protected_address_spans):
    return match.group(0)
```

Существующие исключения `_DOT_ABBREVIATIONS_WITHOUT_SPACE` и инициалов оставить после этой проверки.

Добавить общий интерфейс:

```python
def _normalize_mechanical_spacing(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []

    address_text, address_changed = _normalize_address_spacing(text)
    if address_changed:
        changes.append(_ADDRESS_SPACING_CHANGE)

    collapsed_text = _REPEATED_PLAIN_SPACES_RE.sub(" ", address_text)
    if collapsed_text != address_text:
        changes.append(_REPEATED_SPACES_CHANGE)

    sentence_text, sentence_changed = _ensure_space_after_sentence_punctuation(
        collapsed_text
    )
    if sentence_changed:
        changes.append(_SENTENCE_SPACING_CHANGE)

    return sentence_text, changes
```

- [ ] **Step 4: Запустить unit-тесты нормализатора**

Run:

```powershell
python -m unittest tests.test_text_normalization tests.test_checker_batching -v
```

Expected: все тесты `OK`; `openai.com`, URL и email сохраняются без вставленного пробела, предложения продолжают нормализоваться.

- [ ] **Step 5: Просмотреть diff только первого изменения**

Run:

```powershell
git diff -- src/checker.py tests/test_text_normalization.py
```

Expected: изменены только константы/чистые функции в `src/checker.py` и новый unit-тест; `process_file()` и промт ещё не изменены.

- [ ] **Step 6: Зафиксировать нормализатор отдельным коммитом**

```powershell
git add -- src/checker.py tests/test_text_normalization.py
git commit -m "fix: normalize spacing without breaking domains"
```

---

### Task 2: Предварительная и финальная нормализация в `process_file()`

**Files:**
- Modify: `src/checker.py:107-206`
- Modify: `tests/test_checker_batching.py:21-156`

**Interfaces:**
- Consumes: `_normalize_mechanical_spacing(text: str) -> tuple[str, list[str]]` из Task 1.
- Produces: `_merge_unique_changes(*groups: list[str]) -> list[str]`.
- `LLMClient.check_batch()` продолжает получать `list[BatchItem]`, но поле `BatchItem.content` содержит предварительно нормализованный текст.
- `process_file()` сохраняет сигнатуру `async process_file(input_path: Path, llm: LLMClient, on_progress: ProgressCallback | None = None) -> tuple[Path, Report]`.

- [ ] **Step 1: Расширить фабрику тестового xlsx произвольным содержанием**

В `tests/test_checker_batching.py` заменить импорт на `from openpyxl import Workbook, load_workbook`, а в условный блок импортов добавить `from src.xlsx_processor import HIGHLIGHT_FILL`. Затем заменить внутреннюю генерацию содержимого на две функции:

```python
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
```

- [ ] **Step 2: Написать падающие интеграционные тесты предварительной нормализации и отчёта**

Добавить в `TestCheckerBatching`:

```python
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
```

- [ ] **Step 3: Запустить интеграционные тесты и подтвердить красную фазу**

Run:

```powershell
python -m unittest tests.test_checker_batching.TestCheckerBatching.test_sends_pre_normalized_content_to_llm_and_reports_changes tests.test_checker_batching.TestCheckerBatching.test_normalizes_llm_output_and_merges_changes_without_duplicates -v
```

Expected: первый тест падает, потому что Gemini получает исходный `Проверка  openai. com`; второй тест показывает дублированный/неисправленный домен в модельном ответе.

- [ ] **Step 4: Добавить объединение изменений без дублей**

В `src/checker.py` перед `Report` добавить:

```python
def _merge_unique_changes(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for change in group:
            if change in seen:
                continue
            seen.add(change)
            merged.append(change)
    return merged
```

- [ ] **Step 5: Предварительно нормализовать каждую запись до разбиения на запросы**

Сразу после логирования количества записей в `process_file()` создать отображение:

```python
normalized_inputs = {
    entry.row_idx: _normalize_mechanical_spacing(entry.content)
    for entry in entries
}
```

В `_run_batch()` формировать `BatchItem` из нормализованного текста:

```python
items = [
    BatchItem(
        id=i,
        contractor=entry.contractor,
        date=entry.date,
        time=entry.time,
        content=normalized_inputs[entry.row_idx][0],
    )
    for i, entry in enumerate(batch)
]
```

- [ ] **Step 6: Повторно нормализовать ответ и объединить все источники изменений**

Заменить финальную обработку `base_text` в цикле результатов на:

```python
pre_normalized_text, pre_changes = normalized_inputs[entry.row_idx]
base_text = result.corrected if result.corrected else pre_normalized_text
spaced_text, post_changes = _normalize_mechanical_spacing(base_text)
initials_text, initials_normalized = _normalize_surname_initials(spaced_text)
final_text, dot_added = _ensure_trailing_dot(initials_text)
changes = _merge_unique_changes(pre_changes, list(result.changes), post_changes)
if initials_normalized:
    changes = _merge_unique_changes(changes, ["убран лишний пробел между инициалами"])
if dot_added:
    changes = _merge_unique_changes(changes, ["добавлена точка в конце"])
```

Удалить прежний отдельный вызов `_ensure_space_after_sentence_punctuation()` из этого цикла: он уже входит в `_normalize_mechanical_spacing()`.

- [ ] **Step 7: Проверить интеграцию, batching и обработку ошибок**

Run:

```powershell
python -m unittest tests.test_checker_batching tests.test_text_normalization -v
```

Expected: все тесты `OK`; размеры батчей и порядок строк не изменены; ошибки LLM по-прежнему увеличивают `Report.errors`; предварительные и финальные изменения появляются в отчёте один раз.

- [ ] **Step 8: Зафиксировать интеграцию отдельным коммитом**

```powershell
git add -- src/checker.py tests/test_checker_batching.py
git commit -m "feat: normalize content around Gemini checks"
```

---

### Task 3: Уточнение системного промта и полная регрессия

**Files:**
- Create: `tests/test_prompt_contract.py`
- Modify: `src/prompts/system_prompt.txt:4-40`

**Interfaces:**
- Consumes: системный промт через существующую `_load_system_prompt() -> str` в `src/llm.py`.
- Produces: неизменный JSON-контракт с полями `id`, `Исправленное_Содержание`, `Изменения`, `Предупреждение`.
- Preserves verbatim: разделы `# Шаблонные формулировки и placeholder'ы`, `# Эталонные шаблоны работ`, `# Формат запроса`, `# Формат ответа`, `# Жёсткие требования`.

- [ ] **Step 1: Создать тест ключевого контракта промта**

Создать `tests/test_prompt_contract.py`:

```python
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
```

- [ ] **Step 2: Запустить контрактный тест и подтвердить красную фазу**

Run:

```powershell
python -m unittest tests.test_prompt_contract -v
```

Expected: доменные и email-примеры отсутствуют, поэтому первые два теста `FAIL`; JSON-контракт уже проходит.

- [ ] **Step 3: Переписать правило пробелов в разделе `# Что проверять`**

Заменить текущий длинный пункт 2 на структурированный текст:

```text
2. Пробелы и границы предложений.
- Последовательность из двух и более пробелов заменяй ОДНИМ пробелом: «на  1 место» → «на 1 место», НЕ «на1 место».
- Если после точки, вопросительного, восклицательного знака или многоточия начинается следующее предложение, между ними должен быть ОДИН пробел: «Сделано.  Следующее» → «Сделано. Следующее», «Сделано.Следующее» → «Сделано. Следующее».
- Пробелы около точки внутри сайта или email убирай только для доменных зон .kz, .ru, .com, .org, .net, .рф и .қаз: «openai. com» → «openai.com», «site .kz» → «site.kz», «user@site. ru» → «user@site.ru».
- Не добавляй пробелы внутри корректных сайтов и email: «openai.com» и «user@site.kz» оставляй без изменений.
- Склеенные слова и числа со словами продолжай исправлять по правилам ниже: «обновлениедо» → «обновление до», «на1 рабочее место» → «на 1 рабочее место».
- Механическое схлопывание повторных пробелов и защита адресов дополнительно выполняются Python до и после твоего ответа. Не отменяй эти исправления.
- Пробелы в конце ячейки игнорируй: Python срезает их до отправки в LLM.
```

- [ ] **Step 4: Удалить доказанные повторы без изменения предметных правил**

В `src/prompts/system_prompt.txt` выполнить только следующие сокращения:

1. Оставить одно полное правило формата `Иванов И.И.` → без пробела между инициалами в пункте 6 раздела `# Что проверять`.
2. В `# Приоритет правил` оставить короткое указание, что правило инициалов и правило адресов приоритетнее общего пробела после точки.
3. Удалить повторное полное объяснение инициалов из `# Чего НЕ трогать`, сохранив правило «названия контрагентов и собственные имена — символ в символ» и его исключение для `И. И.` → `И.И.`.
4. Сохранить без смысловых изменений правила версий, `2ИБ`/`3ПК`, `г.`, `кв`, `ф.`, `рег.`, placeholder’ов, эталонного словаря и допустимых бухгалтерских сокращений.
5. Не менять разделы с шаблонными формулировками и JSON-примерами.

Итоговый блок приоритетов должен содержать:

```text
# Приоритет правил
Если правила конфликтуют, более узкое правило имеет приоритет над общим.
- Инициалы «И.И.» не являются границей предложений: пробел между инициалами не добавляй.
- Точки внутри распознанных сайтов и email не являются границей предложений: пробел внутри адреса не добавляй.
- Буквы собственных имён не меняй; разрешено только убрать лишний пробел между инициалами «И. И.» → «И.И.».
```

- [ ] **Step 5: Запустить контракт промта и профильные тесты**

Run:

```powershell
python -m unittest tests.test_prompt_contract tests.test_text_normalization tests.test_checker_batching -v
```

Expected: все тесты `OK`; системный промт содержит новый контракт, а Python-нормализатор обеспечивает его механическую часть.

- [ ] **Step 6: Проверить сохранность критических разделов промта**

Run:

```powershell
rg -n --encoding utf-8 "^# (Эталонный словарь|Шаблонные формулировки и placeholder'ы|Эталонные шаблоны работ|Формат запроса|Формат ответа|Жёсткие требования)" src/prompts/system_prompt.txt
rg -n --encoding utf-8 "3\.0\.\.\.\.\.|2ИБ|3ПК|1C:WebKassa|Исправленное_Содержание|Предупреждение" src/prompts/system_prompt.txt
```

Expected: все перечисленные заголовки, предметные маркеры, placeholder версии и JSON-поля найдены.

- [ ] **Step 7: Запустить полный набор тестов и проверку синтаксиса**

Сначала проверить рабочее окружение:

```powershell
python --version
python -c "import google.genai, openpyxl, pydantic; print('dependencies-ok')"
```

Если существующее `.venv` остаётся сломанным из-за отсутствующего Python 3.11, не удалять и не перезаписывать его. Создать одноразовое окружение в системном temp текущим установленным Python, установить зависимости и выполнять следующие команды его интерпретатором:

```powershell
$testEnv = Join-Path ([IO.Path]::GetTempPath()) ("auto-checking-transcript-" + [guid]::NewGuid())
python -m venv $testEnv
& (Join-Path $testEnv "Scripts\python.exe") -m pip install -r requirements.txt
& (Join-Path $testEnv "Scripts\python.exe") -m unittest discover tests
$pythonFiles = @(Get-ChildItem src, scripts, tests -Filter "*.py" -File)
& (Join-Path $testEnv "Scripts\python.exe") -m py_compile $pythonFiles.FullName
$resolvedTestEnv = (Resolve-Path -LiteralPath $testEnv).Path
$resolvedTemp = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path
if (-not $resolvedTestEnv.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove non-temp test environment: $resolvedTestEnv"
}
Remove-Item -LiteralPath $resolvedTestEnv -Recurse -Force
```

Expected: `unittest` завершён с `OK`, `py_compile` завершён с exit code 0. Временное окружение удалено только после проверки, что его абсолютный путь находится внутри системного temp.

- [ ] **Step 8: Проверить итоговый diff и отсутствие запрещённых файлов**

Run:

```powershell
git status --short
git diff --check
git diff -- src/checker.py src/prompts/system_prompt.txt tests/test_text_normalization.py tests/test_checker_batching.py tests/test_prompt_contract.py
```

Expected: `.env` и клиентские xlsx отсутствуют в diff; нет trailing whitespace; существующая `.claude/` не добавлена; изменения ограничены согласованными файлами.

- [ ] **Step 9: Зафиксировать промт и контрактные тесты**

```powershell
git add -- src/prompts/system_prompt.txt tests/test_prompt_contract.py
git commit -m "docs: clarify spacing rules for domains and email"
```

- [ ] **Step 10: Зафиксировать доказательства завершения**

Run:

```powershell
git status --short
git log -5 --oneline
```

Expected: из несвязанных изменений остаётся только ранее существовавшая `.claude/`; три коммита реализации видны отдельно; рабочие файлы задачи не изменены после последнего коммита.
