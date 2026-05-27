from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

load_dotenv(ROOT / ".env")


def _parse_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    return frozenset(int(x.strip()) for x in raw.split(",") if x.strip())


def _parse_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    gemini_api_key: str
    allowed_user_ids: frozenset[int]
    allow_any: bool
    gemini_model: str
    llm_concurrency: int


def load_settings() -> Settings:
    telegram_token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not telegram_token:
        raise RuntimeError("TELEGRAM_TOKEN is not set (see .env.example)")
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set (see .env.example)")

    allowed = _parse_ids(os.environ.get("ALLOWED_USER_IDS"))
    allow_any = _parse_bool(os.environ.get("ALLOW_ANY"))
    if not allowed and not allow_any:
        raise RuntimeError(
            "ALLOWED_USER_IDS is empty. Set it to a comma-separated list of Telegram IDs, "
            "or set ALLOW_ANY=true for local debugging (open access)."
        )

    return Settings(
        telegram_token=telegram_token,
        gemini_api_key=gemini_api_key,
        allowed_user_ids=allowed,
        allow_any=allow_any,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        llm_concurrency=int(os.environ.get("LLM_CONCURRENCY", "5")),
    )
