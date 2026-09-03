# Auto-checking timesheet bot

## Логи

При каждом запуске все записи стандартного логирования бота сохраняются в отдельный
UTF-8-файл `logs/bot_YYYY-MM-DD_HH-MM-SS.log`; те же записи остаются видны в терминале.
Каталог `logs/` создаётся автоматически и не попадает в Git.

Telegram-бот для автоматической проверки табелей учёта рабочего времени 1С-консультанта. Принимает xlsx, проверяет колонку «Содержание» через OpenAI GPT-5.6 Luna, возвращает исправленный файл с подсветкой и текстовый отчёт.

> 📘 **Первый запуск с нуля** (получение ключей Google и Telegram, заполнение `.env`, тест) — см. [docs/SETUP.md](docs/SETUP.md).

## Установка

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# открой .env и заполни TELEGRAM_TOKEN, OPENAI_API_KEY, ALLOWED_USER_IDS
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# открой .env и заполни TELEGRAM_TOKEN, OPENAI_API_KEY, ALLOWED_USER_IDS
```

> На Windows, если `Activate.ps1` блокируется политикой, выполни один раз:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

`ALLOWED_USER_IDS` — список Telegram ID коллег через запятую. Чтобы узнать свой ID, напиши боту команду `/id` (работает до настройки whitelist).

## Запуск

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
python -m src.main
```

**macOS / Linux:**
```bash
source .venv/bin/activate
python -m src.main
```

## Локальное тестирование без Telegram

**Windows:**
```powershell
python -m scripts.check_file "Сайдашев Кирилл Алексеевич.xlsx"
```

**macOS / Linux:**
```bash
python -m scripts.check_file "Сайдашев Кирилл Алексеевич.xlsx"
```

(см. `scripts/check_file.py`)

## Структура

- `src/main.py` — точка входа (запускает бот).
- `src/bot.py` — Telegram-хендлеры.
- `src/checker.py` — связка парсер + LLM + writer.
- `src/llm.py` — клиент OpenAI Responses API.
- `src/xlsx_processor.py` — чтение/запись xlsx.
- `src/prompts/system_prompt.txt` — системный промт корректора.
