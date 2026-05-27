from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from telegram import Update
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
    "/cancel — отменить текущую обработку."
)


def _is_allowed(user_id: int | None, settings: Settings) -> bool:
    if settings.allow_any:
        return True
    return user_id is not None and user_id in settings.allowed_user_ids


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
    tasks: dict[int, asyncio.Task] = _ctx.application.bot_data.setdefault("tasks", {})
    task = tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
        await update.message.reply_text("Отменяю обработку…")
    else:
        await update.message.reply_text("Сейчас ничего не обрабатывается.")


async def handle_document(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.message.document:
        return
    settings: Settings = _ctx.application.bot_data["settings"]
    user_id = update.effective_user.id

    if not _is_allowed(user_id, settings):
        await update.message.reply_text(
            f"У тебя нет доступа. Твой Telegram ID: {user_id}."
        )
        return

    doc = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".xlsx"):
        await update.message.reply_text("Нужен файл .xlsx — пришли табель в этом формате.")
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    size_kb = (doc.file_size or 0) / 1024
    log.info(
        "Received xlsx '%s' (%.1f KB) from user=%s chat=%s",
        doc.file_name, size_kb, user_id, chat_id,
    )

    tasks: dict[int, asyncio.Task] = _ctx.application.bot_data.setdefault("tasks", {})
    if chat_id is not None:
        existing = tasks.get(chat_id)
        if existing and not existing.done():
            await update.message.reply_text(
                "Уже обрабатываю предыдущий файл. Отправь /cancel чтобы отменить."
            )
            return
        current = asyncio.current_task()
        if current is not None:
            tasks[chat_id] = current

    progress = await update.message.reply_text("Скачиваю файл…")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            safe_name = Path(doc.file_name).name or "uploaded.xlsx"
            tmp_path = Path(tmpdir) / safe_name

            t0 = time.monotonic()
            tg_file = await doc.get_file()
            await tg_file.download_to_drive(tmp_path)
            t_download = time.monotonic() - t0
            log.info("Downloaded '%s' in %.2fs", doc.file_name, t_download)

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

            llm: LLMClient = _ctx.application.bot_data["llm"]
            t0 = time.monotonic()
            try:
                output_path, report = await checker.process_file(tmp_path, llm, on_progress=on_progress)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                log.exception("Processing failed for '%s' (after %.2fs)", doc.file_name, time.monotonic() - t0)
                await progress.edit_text(f"Ошибка при обработке файла: {err}")
                return

            t_llm = time.monotonic() - t0
            out_size_kb = output_path.stat().st_size / 1024
            log.info(
                "LLM done in %.2fs: total=%d, corrections=%d, warnings=%d, errors=%d; output=%.1f KB",
                t_llm, report.total, len(report.corrections),
                len(report.warnings), report.errors, out_size_kb,
            )

            t0 = time.monotonic()
            await progress.edit_text(report.render())
            log.info("Report message sent in %.2fs, uploading xlsx (%.1f KB)", time.monotonic() - t0, out_size_kb)

            t0 = time.monotonic()
            with output_path.open("rb") as f:
                await update.message.reply_document(document=f, filename=output_path.name)
            log.info("Document delivered to user=%s in %.2fs", user_id, time.monotonic() - t0)
    except asyncio.CancelledError:
        log.info("Processing cancelled for '%s' (user=%s)", doc.file_name, user_id)
        try:
            await asyncio.shield(progress.edit_text("Обработка отменена."))
        except Exception:  # noqa: BLE001
            pass
    finally:
        if chat_id is not None and tasks.get(chat_id) is asyncio.current_task():
            tasks.pop(chat_id, None)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error in handler", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"Что-то пошло не так: {ctx.error}"
            )
        except Exception:  # noqa: BLE001
            pass


def build_application(settings: Settings) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .read_timeout(60)
        .write_timeout(120)
        .connect_timeout(30)
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
