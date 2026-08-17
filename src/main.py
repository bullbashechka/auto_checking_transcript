from __future__ import annotations

import logging
import re

from .bot import build_application
from .config import load_settings

_TELEGRAM_TOKEN_URL_RE = re.compile(
    r"(https://api\.telegram\.org/bot)[^/\s\"]+",
    re.IGNORECASE,
)


class _RedactTelegramTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _TELEGRAM_TOKEN_URL_RE.sub(r"\1<redacted>", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RedactTelegramTokenFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = load_settings()
    app = build_application(settings)
    logging.info(
        "Starting bot. Whitelist: %s",
        sorted(settings.allowed_user_ids) or "(пусто — открыто всем)",
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
