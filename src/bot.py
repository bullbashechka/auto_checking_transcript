from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from telegram import BotCommand, Document, Message, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import checker
from .config import Settings
from .llm import LLMClient

log = logging.getLogger(__name__)

WELCOME = (
    "Привет! Я проверяю табели учёта рабочего времени.\n\n"
    "Отправь мне xlsx-файл — в ответ верну исправленную версию "
    "и список найденных опечаток/предупреждений.\n\n"
    "Можно прислать несколько файлов подряд — встанут в очередь.\n"
    "/cancel — отменить текущую обработку и очистить очередь."
)


@dataclass
class QueuedItem:
    update: Update
    doc: Document
    progress: Message
    user_id: int
    queued_at: float = field(default_factory=time.monotonic)


@dataclass
class ChatQueue:
    items: deque[QueuedItem] = field(default_factory=deque)
    worker: asyncio.Task[None] | None = None


def _is_allowed(user_id: int | None, settings: Settings) -> bool:
    if settings.allow_any:
        return True
    return user_id is not None and user_id in settings.allowed_user_ids


def _get_chat_queue(app: Application, chat_id: int) -> ChatQueue:
    state: dict[int, ChatQueue] = app.bot_data.setdefault("queues", {})
    return state.setdefault(chat_id, ChatQueue())


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    settings: Settings = _ctx.application.bot_data["settings"]
    if not _is_allowed(update.effective_user.id, settings):
        await update.message.reply_text(
            f"У тебя нет доступа. Твой Telegram ID: {update.effective_user.id}. "
            "Попроси администратора добавить его в whitelist."
        )
        return
    await update.message.reply_text(WELCOME)


async def cmd_id(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.effective_user:
        await update.message.reply_text(f"Твой Telegram ID: {update.effective_user.id}")


async def cmd_cancel(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    chat = _get_chat_queue(_ctx.application, chat_id)
    if chat.worker and not chat.worker.done():
        pending = len(chat.items)
        chat.worker.cancel()
        suffix = f" + {pending} в очереди" if pending else ""
        await update.message.reply_text(f"Отменяю текущую обработку{suffix}…")
    else:
        await update.message.reply_text("Сейчас ничего не обрабатывается.")


async def handle_document(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.message.document:
        return
    settings: Settings = _ctx.application.bot_data["settings"]
    user_id = update.effective_user.id

    if not _is_allowed(user_id, settings):
        await update.message.reply_text(f"У тебя нет доступа. Твой Telegram ID: {user_id}.")
        return

    doc = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".xlsx"):
        await update.message.reply_text("Нужен файл .xlsx — пришли табель в этом формате.")
        return

    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    size_kb = (doc.file_size or 0) / 1024
    log.info(
        "Received xlsx '%s' (%.1f KB) from user=%s chat=%s",
        doc.file_name, size_kb, user_id, chat_id,
    )

    chat = _get_chat_queue(_ctx.application, chat_id)
    worker_busy = chat.worker is not None and not chat.worker.done()
    position = len(chat.items) + (1 if worker_busy else 0) + 1

    if position == 1:
        progress = await update.message.reply_text("Скачиваю файл…")
    else:
        progress = await update.message.reply_text(
            f"Принято, в очереди. Позиция: {position}."
        )

    chat.items.append(
        QueuedItem(update=update, doc=doc, progress=progress, user_id=user_id)
    )

    if chat.worker is None or chat.worker.done():
        chat.worker = asyncio.create_task(_worker(_ctx.application, chat_id))


async def _worker(app: Application, chat_id: int) -> None:
    chat = _get_chat_queue(app, chat_id)
    try:
        while chat.items:
            item = chat.items.popleft()
            try:
                await _process_one(app, item)
            except asyncio.CancelledError:
                log.info(
                    "Processing cancelled for '%s' (user=%s); draining %d queued",
                    item.doc.file_name, item.user_id, len(chat.items),
                )
                try:
                    await asyncio.shield(item.progress.edit_text("Обработка отменена."))
                except Exception:  # noqa: BLE001
                    pass
                while chat.items:
                    dropped = chat.items.popleft()
                    try:
                        await asyncio.shield(
                            dropped.progress.edit_text("Очередь отменена.")
                        )
                    except Exception:  # noqa: BLE001
                        pass
                raise
            except Exception:  # noqa: BLE001
                log.exception("Queue item failed for '%s'", item.doc.file_name)
    finally:
        chat.worker = None


async def _process_one(app: Application, item: QueuedItem) -> None:
    update = item.update
    doc = item.doc
    progress = item.progress
    user_id = item.user_id
    assert update.message is not None

    try:
        await progress.edit_text("Скачиваю файл…")
    except Exception:  # noqa: BLE001
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        safe_name = Path(doc.file_name or "uploaded.xlsx").name or "uploaded.xlsx"
        tmp_path = Path(tmpdir) / safe_name

        t0 = time.monotonic()
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(tmp_path)
        log.info("Downloaded '%s' in %.2fs", doc.file_name, time.monotonic() - t0)

        await progress.edit_text("Анализирую содержание, это может занять минуту…")
        log.info("Starting LLM check ('%s')", doc.file_name)

        last_edit_ts = 0.0
        last_percent = -1

        async def on_progress(done_b: int, total_b: int, done_e: int, total_e: int) -> None:
            nonlocal last_edit_ts, last_percent
            percent = int(100 * done_e / total_e) if total_e else 0
            now = time.monotonic()
            is_final = done_b == total_b
            if percent == last_percent:
                return
            if not is_final and (now - last_edit_ts) < 2.0:
                return
            last_edit_ts = now
            last_percent = percent
            try:
                await progress.edit_text(
                    f"Анализирую содержание… {done_e}/{total_e} ({percent}%)"
                )
            except Exception:  # noqa: BLE001
                pass

        llm: LLMClient = app.bot_data["llm"]
        t0 = time.monotonic()
        try:
            output_path, report = await checker.process_file(
                tmp_path, llm, on_progress=on_progress
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            log.exception(
                "Processing failed for '%s' (after %.2fs)",
                doc.file_name, time.monotonic() - t0,
            )
            try:
                await progress.edit_text(f"Ошибка при обработке файла: {err}")
            except Exception:  # noqa: BLE001
                pass
            return

        t_llm = time.monotonic() - t0
        out_size_kb = output_path.stat().st_size / 1024
        log.info(
            "LLM done in %.2fs: total=%d, corrections=%d, warnings=%d, errors=%d; output=%.1f KB",
            t_llm, report.total, len(report.corrections),
            len(report.warnings), report.errors, out_size_kb,
        )

        await progress.edit_text(report.render())

        with output_path.open("rb") as f:
            await update.message.reply_document(document=f, filename=output_path.name)
        log.info("Document delivered to user=%s ('%s')", user_id, doc.file_name)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error in handler", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(f"Что-то пошло не так: {ctx.error}")
        except Exception:  # noqa: BLE001
            pass


BOT_COMMANDS = [
    BotCommand("start", "Описание и проверка доступа"),
    BotCommand("cancel", "Отменить обработку и очистить очередь"),
    BotCommand("id", "Показать твой Telegram ID"),
]


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
        log.info("Bot commands menu synced (%d commands)", len(BOT_COMMANDS))
    except Exception:  # noqa: BLE001
        log.exception("Failed to set bot commands menu")


def build_application(settings: Settings) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .read_timeout(60)
        .write_timeout(120)
        .connect_timeout(30)
        .post_init(_post_init)
        .build()
    )
    app.bot_data["settings"] = settings
    app.bot_data["llm"] = LLMClient(settings)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document, block=False))
    app.add_error_handler(on_error)

    return app
