from __future__ import annotations

import logging
import tempfile
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

log = logging.getLogger(__name__)

WELCOME = (
    "Привет! Я проверяю табели учёта рабочего времени.\n\n"
    "Отправь мне xlsx-файл — в ответ верну исправленную версию "
    "и список найденных опечаток/предупреждений."
)


def _is_allowed(user_id: int | None, settings: Settings) -> bool:
    if not settings.allowed_user_ids:
        return True  # whitelist пуст — открытый режим (для локальной отладки)
    return user_id in settings.allowed_user_ids


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

    progress = await update.message.reply_text("Скачиваю файл…")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / doc.file_name
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(tmp_path)

        await progress.edit_text("Анализирую содержание, это может занять минуту…")

        try:
            output_path, report = await checker.process_file(tmp_path, settings)
        except Exception as err:  # noqa: BLE001
            log.exception("Processing failed for %s", doc.file_name)
            await progress.edit_text(f"Ошибка при обработке файла: {err}")
            return

        await progress.edit_text(report.render())

        with output_path.open("rb") as f:
            await update.message.reply_document(document=f, filename=output_path.name)


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.telegram_token).build()
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    return app
