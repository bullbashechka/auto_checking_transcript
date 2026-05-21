from __future__ import annotations

import logging

from .bot import build_application
from .config import load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    settings = load_settings()
    app = build_application(settings)
    logging.info(
        "Starting bot. Whitelist: %s",
        sorted(settings.allowed_user_ids) or "(пусто — открыто всем)",
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
