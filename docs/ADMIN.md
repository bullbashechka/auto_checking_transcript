# Администрирование бота на VPS

VPS: `root@45.80.69.22`  
Директория: `/opt/auto_checking_transcript`  
Сервис: `auto-checking-transcript`

---

## Управление сервисом

```bash
systemctl status auto-checking-transcript    # статус
systemctl start auto-checking-transcript     # запустить
systemctl stop auto-checking-transcript      # остановить
systemctl restart auto-checking-transcript   # перезапустить
```

---

## Логи

```bash
journalctl -u auto-checking-transcript -f          # в реальном времени
journalctl -u auto-checking-transcript -n 100      # последние 100 строк
journalctl -u auto-checking-transcript --since today  # за сегодня
```

---

## Обновление бота

После `git push` на локальной машине — на VPS:

```bash
bash /opt/auto_checking_transcript/deploy.sh
```

Скрипт сам сделает `git pull`, обновит зависимости и перезапустит сервис.

---

## Управление whitelist (кто может использовать бота)

Открыть `.env` на VPS:

```bash
nano /opt/auto_checking_transcript/.env
```

Найти строку `ALLOWED_USER_IDS` и добавить/убрать Telegram ID через запятую:

```dotenv
ALLOWED_USER_IDS=1295392762,726039693,988556738
```

Чтобы узнать ID нового пользователя — попроси его написать боту [@userinfobot](https://t.me/userinfobot).

После изменения перезапустить бота:

```bash
systemctl restart auto-checking-transcript
```

---

## Поменять модель OpenAI

```bash
nano /opt/auto_checking_transcript/.env
```

Изменить строку `OPENAI_MODEL`:

```dotenv
OPENAI_MODEL=gpt-5.6-luna          # экономичная модель по умолчанию
OPENAI_MODEL=gpt-5.6-terra         # точнее, но дороже
OPENAI_REASONING_EFFORT=low        # none/low/medium/high/xhigh/max
LLM_CONCURRENCY=3
OPENAI_TPM_LIMIT=200000
OPENAI_TPM_UTILIZATION=0.80
```

Перезапустить:

```bash
systemctl restart auto-checking-transcript
```

---

## Если бот упал и не поднимается

```bash
journalctl -u auto-checking-transcript -n 50 --no-pager
```

Частые причины:
- `RuntimeError: TELEGRAM_TOKEN is not set` — проверь `.env`
- `RuntimeError: OPENAI_API_KEY is not set` — проверь `.env`
- `ModuleNotFoundError` — запусти `.venv/bin/pip install -r requirements.txt`
- `409 Conflict` — бот уже запущен где-то ещё (локально или второй раз на сервере)

---

## Если бот завис (не отвечает, но сервис активен)

```bash
systemctl restart auto-checking-transcript
```

---

## Ручной запуск для отладки (без systemd)

```bash
cd /opt/auto_checking_transcript
.venv/bin/python -m src.main
```

Ctrl+C — остановить. Полезно когда нужно видеть вывод напрямую.

---

## Автозапуск при перезагрузке сервера

Уже включён командой `systemctl enable` при установке. Проверить:

```bash
systemctl is-enabled auto-checking-transcript
# должно вывести: enabled
```
