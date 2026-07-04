"""Главный поток: вопрос пользователя → готовый расклад с трактовкой.

Связывает изолированные модули в один сценарий:
  1. classify(question)      → spread_id (src/classifier.py, LLM по правилам)
  2. deck.draw(n)            → карты без повторов, ориентация 50/50 (src/deck.py)
  3. db.create_reading(...)  → фиксируем тягу В БД ДО генерации текста
  4. llm.interpret(context)  → трактовка из 5 блоков (src/llm.py, Gemini)
  5. отправка карт альбомом (src/cards.py) + текст трактовки

Сервис бесплатный: пейволла и списания free_readings здесь нет.
classify()/interpret() синхронные (requests) — крутим их в asyncio.to_thread,
чтобы не блокировать event loop aiogram.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message

from . import cards as card_images
from . import db, deck
from .classifier import classify
from .config import load_spreads
from .llm import interpret

logger = logging.getLogger("shamanka.reading")

router = Router(name="reading")

FALLBACK_SPREAD_ID = "proshloe_nastoyashee_budushee"
TELEGRAM_LIMIT = 4096  # максимум символов в одном сообщении

# Расклады читаем один раз (позиции фиксированы, файл при работе не меняется).
_spreads_cache: dict | None = None


def _spreads() -> dict:
    global _spreads_cache
    if _spreads_cache is None:
        _spreads_cache = load_spreads()
    return _spreads_cache


def _split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Разбить длинную трактовку на части по абзацам, не рвя слова грубо."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        piece = (current + "\n\n" + para) if current else para
        if len(piece) <= limit:
            current = piece
            continue
        if current:
            chunks.append(current)
        # сам абзац длиннее лимита — режем по строкам/жёстко
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return chunks


@router.message(F.text & ~F.text.startswith("/"))
async def handle_question(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer("Сначала давай познакомимся — набери /start 🌙")
        return

    question = message.text.strip()
    if not question:
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    notice = await message.answer("Тяну карты… 🔮")

    try:
        spreads = _spreads()
        spread_id = await asyncio.to_thread(classify, question)
        spread = spreads.get(spread_id) or spreads[FALLBACK_SPREAD_ID]
        drawn = deck.draw(spread["cards"])

        # Фиксируем тягу ДО генерации текста (требование CLAUDE.md).
        reading_id = await db.create_reading(
            user.id, question, spread_id, spread["title"], drawn
        )

        context = {
            "name": user.name,
            "gender": user.gender or "",
            "question": question,
            "spread_id": spread_id,
            "spread": spread,
            "cards": drawn,
        }
        text = await asyncio.to_thread(interpret, context)
        await db.set_reading_text(reading_id, text)
    except Exception:
        logger.exception("Не удалось построить расклад")
        await notice.edit_text(
            "Карты сейчас молчат 🌙 Попробуй, пожалуйста, ещё раз чуть позже."
        )
        return

    await notice.delete()
    await card_images.send_album(message, drawn)
    for chunk in _split_message(text):
        await message.answer(chunk)
