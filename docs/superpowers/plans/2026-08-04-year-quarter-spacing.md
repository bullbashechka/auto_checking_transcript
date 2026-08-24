# Пробелы в обозначениях года и квартала Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Настроить промт на пробел перед полными словами года и квартала и слитное написание чисел с сокращениями `г.` и `кв.`.

**Architecture:** Изменяется только текст системного промта. Контрактный тест читает промт как UTF-8-текст и фиксирует обязательные примеры без вызова LLM.

**Tech Stack:** Python 3, unittest, UTF-8 text files.

## Global Constraints

- Не читать, не изменять и не коммитить `.env`.
- Изменить только `src/prompts/system_prompt.txt` и `tests/test_prompt_contract.py`.
- Полные формы `года`, `год`, `квартал`, `квартала` отделяются от числа пробелом.
- Сокращения `г.` и `кв.` пишутся слитно с числом и не раскрываются.

### Task 1: Контракт промта для года и квартала

**Files:**

- Modify: `tests/test_prompt_contract.py`
- Modify: `src/prompts/system_prompt.txt`

**Interfaces:**

- Consumes: `PROMPT_PATH.read_text(encoding="utf-8")` в `TestPromptContract.setUpClass`.
- Produces: `TestPromptContract.test_contains_year_and_quarter_spacing_rule`.

- [x] **Step 1: Write the failing test**

Добавить в `TestPromptContract` метод, проверяющий строки `«2026года» → «2026 года»`, `«2026 г.» → «2026г.»`, `«1квартал» → «1 квартал»`, `«2квартала» → «2 квартала»`, `«1 кв.» → «1кв.»` и отсутствие старого правила `«1кв.» → «1 кв.»,`.

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_prompt_contract.TestPromptContract.test_contains_year_and_quarter_spacing_rule`

Expected: FAIL из-за отсутствия новых примеров и присутствия старого правила.

- [x] **Step 3: Write minimal implementation**

В `src/prompts/system_prompt.txt` заменить конфликтующие правила одним: `После числа полные слова «год», «года», «квартал» и «квартала» пишутся через пробел: «2026года» → «2026 года», «1квартал» → «1 квартал», «2квартала» → «2 квартала». Сокращения «г.» и «кв.» пишутся слитно с числом: «2026 г.» → «2026г.», «1 кв.» → «1кв.». Сокращения не раскрывай и точку в них не убирай.` Сохранить исключение `2ИБ` и `3ПК`.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_prompt_contract.TestPromptContract.test_contains_year_and_quarter_spacing_rule`

Expected: PASS.

- [x] **Step 5: Run regression suite and syntax check**

Run: `python -m unittest discover tests` и `python -m py_compile src/*.py scripts/*.py tests/*.py`.

Expected: обе команды завершаются с кодом 0.

- [x] **Step 6: Commit**

Run: `git add -- src/prompts/system_prompt.txt tests/test_prompt_contract.py docs/superpowers/plans/2026-08-04-year-quarter-spacing.md` затем `git commit -m "fix: clarify year and quarter spacing"`.
