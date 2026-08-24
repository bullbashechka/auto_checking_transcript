# Bank Brand Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Приводить `kaspi bank` и `халык` в содержании табеля к канонической записи `KaspiBank, Халык`.

**Architecture:** Изменение ограничено системным промтом: две формы добавляются в эталонный словарь и получают явное исключение из общего запрета менять собственные имена. Контрактный тест проверяет присутствие точных форм, исключения и примера.

**Tech Stack:** Python 3.11+, `unittest`, текстовый системный промт Gemini.

## Global Constraints

- Не читать, не выводить, не редактировать и не коммитить `.env`.
- Не модифицировать клиентские xlsx в корне проекта.
- Не изменять Python-код, JSON-контракт, шаблоны, placeholder'ы, правила пробелов и другие собственные имена.
- Канонические формы строго: `KaspiBank` и `Халык`.
- Новые зависимости не добавляются.

---

### Task 1: Контракт канонических названий банков

**Files:**

- Modify: `tests/test_prompt_contract.py:17-43`
- Modify: `src/prompts/system_prompt.txt:19-43`

**Interfaces:**

- Consumes: `src/prompts/system_prompt.txt`, загружаемый `src.llm._load_system_prompt()`.
- Produces: неизменный JSON-контракт Gemini и явное правило `kaspi bank` → `KaspiBank`, `халык` → `Халык`.

- [ ] **Step 1: Написать падающий контрактный тест**

Добавить в `TestPromptContract`:

```python
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
```

- [ ] **Step 2: Подтвердить красную фазу**

Run:

```powershell
python -m unittest tests.test_prompt_contract.TestPromptContract.test_contains_bank_brand_normalization_rule -v
```

Expected: `FAIL`, потому что промт ещё не содержит `KaspiBank`, `Халык` и исключение для этих форм.

- [ ] **Step 3: Добавить две канонические формы и точечное исключение**

В строку эталонного словаря добавить `KaspiBank, Халык` после `Kaspersky`.

После правила словаря добавить текст:

```text
Правило канонических названий банков: исправляй варианты «kaspi bank», «Kaspi bank», «kaspiBank» на «KaspiBank», а «халык» — на «Халык». Это исключение из правила «собственные имена — символ в символ». Пример: «Работа с выписками банка за май 2026г. (kaspi bank, халык).» → «Работа с выписками банка за май 2026г. (KaspiBank, Халык).».
```

В разделе `# Чего НЕ трогать` сохранить общий запрет для остальных собственных имён и добавить ссылку на это исключение: `кроме канонизации KaspiBank и Халык по правилу выше`.

- [ ] **Step 4: Подтвердить зелёную фазу и сохранность контракта**

Run:

```powershell
python -m unittest tests.test_prompt_contract -v
python -m unittest discover tests
$pyFiles = @(Get-ChildItem src, scripts, tests -Filter '*.py' -File)
python -m py_compile $pyFiles.FullName
```

Expected: новый контрактный тест и полный набор проходят; JSON-поля продолжают присутствовать в промте.

- [ ] **Step 5: Проверить границы diff и закоммитить**

Run:

```powershell
git diff --check
git diff -- src/prompts/system_prompt.txt tests/test_prompt_contract.py
git add -- src/prompts/system_prompt.txt tests/test_prompt_contract.py
git commit -m "fix: normalize KaspiBank and Халык names"
```

Expected: в коммит входят только промт и контрактный тест; `.env`, xlsx и Python-код отсутствуют.
