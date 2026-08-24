from __future__ import annotations

import logging
import unittest

from src.main import _RedactTelegramTokenFilter


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


if __name__ == "__main__":
    unittest.main()
