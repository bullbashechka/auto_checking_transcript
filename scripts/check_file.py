"""Локальный прогон проверки без Telegram: python -m scripts.check_file path/to/file.xlsx"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import checker
from src.config import load_settings


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.check_file <path-to-xlsx>")
        sys.exit(1)

    input_path = Path(sys.argv[1]).expanduser().resolve()
    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()

    output_path, report = await checker.process_file(input_path, settings)
    print()
    print(report.render())
    print()
    print(f"Файл с правками: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
