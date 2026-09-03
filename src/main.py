from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from .bot import build_application
from .config import load_settings

_TELEGRAM_TOKEN_URL_RE = re.compile(
    r"(https://api\.telegram\.org/bot)[^/\s\"]+",
    re.IGNORECASE,
)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = _PROJECT_ROOT / "logs"


class _RedactTelegramTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _TELEGRAM_TOKEN_URL_RE.sub(r"\1<redacted>", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _next_log_path(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = log_dir / f"bot_{timestamp}.log"
    suffix = 1
    while candidate.exists():
        candidate = log_dir / f"bot_{timestamp}_{suffix}.log"
        suffix += 1
    return candidate


def _configure_logging(log_dir: Path = LOG_DIR) -> Path:
    """Configure console and per-run file logging and return the log path."""
    log_path = _next_log_path(log_dir)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    redact_filter = _RedactTelegramTokenFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redact_filter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redact_filter)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[console_handler, file_handler],
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return log_path


def main() -> None:
    _configure_logging()
    settings = load_settings()
    app = build_application(settings)
    logging.info(
        "Starting bot. Whitelist: %s",
        sorted(settings.allowed_user_ids) or "(пусто — открыто всем)",
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
