from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from src.main import _RedactTelegramTokenFilter, _configure_logging


class TestLoggingSecurity(unittest.TestCase):
    def test_redacts_telegram_token_from_request_url(self) -> None:
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=(
                "POST https://api.telegram.org/"
                "bot123456789:secret_token/editMessageText"
            ),
            args=(),
            exc_info=None,
        )

        self.assertTrue(_RedactTelegramTokenFilter().filter(record))
        message = record.getMessage()
        self.assertNotIn("secret_token", message)
        self.assertIn("bot<redacted>/editMessageText", message)

    def test_configure_logging_writes_utf8_file_and_redacts_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = _configure_logging(Path(tmpdir))
            try:
                self.assertTrue(log_path.is_file())
                self.assertRegex(
                    log_path.name,
                    r"^bot_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?\.log$",
                )

                logging.getLogger("test.logging").info(
                    "POST https://api.telegram.org/bot123456789:secret_token/getMe"
                )
                for handler in logging.getLogger().handlers:
                    handler.flush()

                content = log_path.read_text(encoding="utf-8")
                self.assertNotIn("secret_token", content)
                self.assertIn("bot<redacted>/getMe", content)
            finally:
                self._reset_root_logging()

    @staticmethod
    def _reset_root_logging() -> None:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
