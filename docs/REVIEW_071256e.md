# Code Review: `071256e` — scaffold Telegram bot for auto-checking timesheets

**14 файлов, +950 строк.** Начальный скаффолд проекта на смену n8n-воркфлоу.

## Overview

Самостоятельный Python-сервис: Telegram-бот принимает xlsx-табель, парсит двухуровневую таблицу, посылает каждую строку «Содержание» в Gemini 2.5 Flash (через async с семафором 5), записывает исправления + жёлтую/красную подсветку в новый файл, отдаёт результат + текстовый отчёт. Доступ — whitelist по Telegram ID.

Архитектура чистая: `bot → checker → xlsx_processor + llm`. Конфиг централизован в `Settings`. Промт вынесен в текстовый файл.

## Code Quality — что хорошо

- Чёткое разделение слоёв, dataclass-ориентированный API между модулями.
- Системный промт — отдельный артефакт, тюнинг без правок кода.
- `Pydantic` с alias'ами на русские поля, `populate_by_name=True` — корректный маппинг JSON ↔ Python.
- Async + `Semaphore(LLM_CONCURRENCY)` корректно ограничивает квоты Gemini.
- `tempfile.TemporaryDirectory` гарантирует уборку скачанных файлов.

## 🔴 Issues — нужно исправить

### 1. Path traversal в `handle_document` ([src/bot.py:72](../src/bot.py#L72))

```python
tmp_path = Path(tmpdir) / doc.file_name
```

Если `doc.file_name = "../../etc/passwd"`, путь выходит за пределы `tmpdir`. Атакующий должен быть в whitelist, но это всё равно баг.

**Фикс**: `tmp_path = Path(tmpdir) / Path(doc.file_name).name` — отрезает любые директории из присланного имени.

### 2. Подсветка теряется при `correction + warning` ([src/xlsx_processor.py:111-118](../src/xlsx_processor.py#L111-L118))

```python
for corr in corrections:
    cell.fill = HIGHLIGHT_FILL   # жёлтая
for warn in warnings:
    cell.fill = WARNING_FILL     # перетирает жёлтую красной
```

Если LLM вернул и `Исправленное_Содержание`, и `Предупреждение` для одной строки (формат ответа явно это допускает) — жёлтый слой затирается красным. Пользователь видит только warning и теряет визуальный сигнал об исправлении.

**Фикс**: ввести третий цвет для combined-случая или применять warning только когда нет correction.

### 3. `LLMClient` создаётся на каждый файл ([src/checker.py:51](../src/checker.py#L51))

```python
async def process_file(...):
    ...
    llm = LLMClient(settings)
```

Каждый запрос пересоздаёт `genai.Client`, перечитывает `system_prompt.txt` с диска, заводит новый `Semaphore`. Семафор-per-file = два параллельных файла дают `2 × LLM_CONCURRENCY = 10` запросов вместо 5 → реальный rate-limit выше декларированного.

**Фикс**: создавать `LLMClient` один раз в `build_application`, класть в `app.bot_data["llm"]`, передавать в `process_file`.

## 🟡 Issues — стоит подумать

### 4. Stripping markdown в `_parse` мёртвый и ломучий ([src/llm.py:87-90](../src/llm.py#L87-L90))

```python
if text.startswith("```"):
    text = text.strip("`")
```

- `response_mime_type="application/json"` гарантирует, что Gemini не возвращает markdown. Эта ветка никогда не сработает.
- `strip("`")` снимает ВСЕ бэктики с обоих концов, что сломает легитимный JSON со значением, оканчивающимся бэктиком.
- Не удаляет закрывающий ` ``` `.

**Рекомендация**: убрать эту ветку — доверять `response_mime_type`.

### 5. Open-mode whitelist — footgun на проде ([src/bot.py:28-31](../src/bot.py#L28-L31))

```python
if not settings.allowed_user_ids:
    return True  # whitelist пуст — открытый режим
```

Документировано как «для локальной отладки», но если на VPS забыть заполнить `ALLOWED_USER_IDS`, бот открыт всему миру + потратит квоту Gemini на любого, кто пришлёт xlsx.

**Рекомендация**: явный `ALLOW_ANY=true` для debug-режима, иначе при пустом whitelist отказывать всем.

### 6. `Report` теряет счётчик ошибок ([src/checker.py:62-65](../src/checker.py#L62-L65))

LLM-исключение логируется и строка пропускается, но в отчёте «Обработано: 18 / Исправлено: 0» не отличить от «всё хорошо» и «все 18 упали с 429».

**Рекомендация**: добавить поле `errors: int` в `Report`, показывать в `render()`.

### 7. Имя класса `Warning_` ([src/xlsx_processor.py:96](../src/xlsx_processor.py#L96))

Трейлинг-underscore чтобы не конфликтовать со встроенным `Warning` — code smell. Переименовать в `PlaceholderHint` / `HighlightedRow` / `WarningCell`.

### 8. `cmd_id` всегда работает, даже не в whitelist ([src/bot.py:47-49](../src/bot.py#L47-L49))

Это вообще-то фича (пользователь должен узнать свой ID чтобы попроситься в whitelist), но в CLAUDE.md уже задокументировано — хорошо. Просто отметка для ревьюера.

## 🟢 Nits

- [src/llm.py:106-111](../src/llm.py#L106-L111) — guard «если LLM вернул тот же текст» молча сбрасывает `Изменения`. Желательно `log.debug` чтобы не путаться при отладке промта.
- [src/checker.py:64](../src/checker.py#L64) — `log.exception(...%s..., result)` лишний `%s`: `log.exception` сам печатает traceback.
- [src/xlsx_processor.py:54-56](../src/xlsx_processor.py#L54-L56) — `iter_rows` был бы чище, но на <100 строк не важно.
- [scripts/check_file.py:9](../scripts/check_file.py#L9) — `sys.path.insert` избыточен при запуске через `python -m`.
- `requirements.txt` использует `>=,<` — не воспроизводимо. Для тула на двоих ок, на проде нужен lockfile.
- Нет ни одного теста. Хотя бы один на `xlsx_processor.parse` против тестового файла спас бы от регрессий парсера.

## Security

- ✅ `.env` в `.gitignore`, не коммитится.
- ✅ Whitelist по Telegram ID.
- ✅ Tempdir auto-cleanup.
- ✅ Нет SQL/shell-инъекций (входов нет).
- 🔴 Path traversal в имени файла (см. issue #1).

## Performance

- Async с лимитом 5 — разумно для бесплатного тарифа Gemini.
- Issue #3 (per-file семафор) удваивает фактическую конкурентность при нескольких пользователях.

## Test Coverage

**Zero.** Документировано в CLAUDE.md как «тесты не нужны, если не просит пользователь». Для скаффолда приемлемо, но как минимум `test_xlsx_processor.py::test_parse_finds_18_entries` на тестовом файле — низкая стоимость, высокая отдача.

## Verdict

Скаффолд решает заявленную задачу. **Перед мерджем (если бы это был реальный PR) — обязательно фиксы #1 (path traversal) и #2 (потеря подсветки)**. Остальное — итеративно.
