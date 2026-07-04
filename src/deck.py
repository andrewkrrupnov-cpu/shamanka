"""Колода Таро (78 карт) и криптостойкая тяга.

Только колода и тяга — ни БД, ни отправки сообщений (это в других модулях).
Названия карт, мастей и рангов — строго по docs/spreads_source.md.
"""
from __future__ import annotations

import random
import secrets

# Старшие арканы (22, номера 0–21) — порядок по docs/spreads_source.md.
MAJOR_ARCANA = [
    "Шут", "Маг", "Верховная Жрица", "Императрица", "Император", "Иерофант",
    "Влюблённые", "Колесница", "Сила", "Отшельник", "Колесо Фортуны",
    "Справедливость", "Повешенный", "Смерть", "Умеренность", "Дьявол", "Башня",
    "Звезда", "Луна", "Солнце", "Суд", "Мир",
]

# Младшие арканы: 4 масти × 14 рангов (Туз, 2–10, Паж, Рыцарь, Королева, Король).
SUITS = ["Кубков", "Пентаклей", "Мечей", "Жезлов"]
RANKS = ["Туз", "2", "3", "4", "5", "6", "7", "8", "9", "10",
         "Паж", "Рыцарь", "Королева", "Король"]
MINOR_ARCANA = [f"{rank} {suit}" for suit in SUITS for rank in RANKS]

FULL_DECK = MAJOR_ARCANA + MINOR_ARCANA  # 22 + 56 = 78

UPRIGHT = "прямая"
REVERSED = "перевёрнутая"


def _resolve_rng(rng, seed):
    """Боевой путь — криптостойкий secrets. rng/seed — ТОЛЬКО для тестов."""
    if rng is not None:
        return rng
    if seed is not None:
        return random.Random(seed)
    return secrets.SystemRandom()


def draw(n: int, *, rng=None, seed: int | None = None) -> list[tuple[str, str]]:
    """Вытянуть n карт без повторов в одном раскладе; ориентация 50/50.

    По умолчанию тяга криптостойкая (secrets.SystemRandom). Параметры rng и seed —
    только для воспроизводимых тестов; в боевом коде их не передавать.
    Возвращает список пар (название карты, "прямая"|"перевёрнутая").
    """
    if not 0 <= n <= len(FULL_DECK):
        raise ValueError(f"n={n}: доступно от 0 до {len(FULL_DECK)} карт")
    r = _resolve_rng(rng, seed)
    picked = r.sample(FULL_DECK, n)
    return [(card, UPRIGHT if r.randint(0, 1) else REVERSED) for card in picked]
