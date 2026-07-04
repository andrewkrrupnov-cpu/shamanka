"""Загрузка конфигурации из окружения (.env) и конфигов раскладов."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
SPREADS_PATH = ROOT / "config" / "spreads.yaml"


@dataclass(frozen=True)
class Config:
    bot_token: str
    openrouter_api_key: str
    database_url: str


def _database_url() -> str:
    user = os.getenv("POSTGRES_USER", "shamanka")
    password = os.getenv("POSTGRES_PASSWORD", "shamanka")
    db = os.getenv("POSTGRES_DB", "shamanka")
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Заполни его в .env (см. .env.example)."
        )
    return Config(
        bot_token=bot_token,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        database_url=_database_url(),
    )


def load_spreads(path: Path | None = None) -> dict:
    """Прочитать config/spreads.yaml. Позиции раскладов берутся ТОЛЬКО отсюда."""
    p = path or SPREADS_PATH
    if not p.exists():
        raise FileNotFoundError(f"Не найден {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))
