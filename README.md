# Auto-checking timesheet bot

Telegram-бот для автоматической проверки табелей учёта рабочего времени 1С-консультанта. Принимает xlsx, проверяет колонку «Содержание» через Gemini 2.5 Flash, возвращает исправленный файл с подсветкой и текстовый отчёт.

> 📘 **Первый запуск с нуля** (получение ключей Google и Telegram, заполнение `.env`, тест) — см. [docs/SETUP.md](docs/SETUP.md).

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# открой .env и заполни TELEGRAM_TOKEN, GEMINI_API_KEY, ALLOWED_USER_IDS
```

`ALLOWED_USER_IDS` — список Telegram ID коллег через запятую. Чтобы узнать свой ID, напиши боту команду `/id` (работает до настройки whitelist).

## Запуск

```bash
python -m src.main
```

## Локальное тестирование без Telegram

```bash
python -m scripts.check_file "Сайдашев Кирилл Алексеевич.xlsx"
```

(см. `scripts/check_file.py`)

## Структура

- `src/main.py` — точка входа (запускает бот).
- `src/bot.py` — Telegram-хендлеры.
- `src/checker.py` — связка парсер + LLM + writer.
- `src/llm.py` — Gemini-клиент.
- `src/xlsx_processor.py` — чтение/запись xlsx.
- `src/prompts/system_prompt.txt` — системный промт корректора.
