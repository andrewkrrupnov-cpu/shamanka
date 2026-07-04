"""Главный поток: расклад запускается ТОЛЬКО кнопкой «Сделать расклад».

Сценарий:
  1. Тап по кнопке → просим назвать вопрос (входим в состояние ожидания вопроса).
  2. Пользователь пишет вопрос → валидируем (is_meaningful_question): билиберду
     отсекаем и просим перефразировать.
  3. classify(question)      → spread_id (src/classifier.py, LLM по правилам)
  4. deck.draw(n)            → карты без повторов, ориентация 50/50 (src/deck.py)
  5. db.create_reading(...)  → фиксируем тягу В БД ДО генерации текста
  6. llm.interpret(context)  → трактовка Шаманки (src/llm.py, Gemini)
  7. карты альбомом + текст фрагментами; в конце — кнопка «ещё один расклад».

Просто набранный текст (без кнопки) расклад НЕ запускает — бот мягко напоминает
про кнопку. Сервис бесплатный: пейволла нет. classify/interpret синхронные —
крутим в asyncio.to_thread, чтобы не блокировать event loop aiogram.
"""
from __future__ import annotations

import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from . import cards as card_images
from . import db, deck
from .classifier import classify, is_meaningful_question
from .config import load_spreads
from .keyboards import AGAIN_KEYBOARD, MAIN_KEYBOARD, READING_BUTTONS
from .llm import interpret

logger = logging.getLogger("shamanka.reading")

router = Router(name="reading")


class Reading(StatesGroup):
    waiting_question = State()

FALLBACK_SPREAD_ID = "proshloe_nastoyashee_budushee"
TELEGRAM_LIMIT = 4096  # максимум символов в одном сообщении

# Расклады читаем один раз (позиции фиксированы, файл при работе не меняется).
_spreads_cache: dict | None = None


def _spreads() -> dict:
    global _spreads_cache
    if _spreads_cache is None:
        _spreads_cache = load_spreads()
    return _spreads_cache


# Шаманка разделяет мысли строкой из дефисов (`---`). По ней и бьём на сообщения.
_FRAGMENT_SEP = re.compile(r"(?m)^\s*[-–—]{3,}\s*$")


def _split_long(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Страховка: если отдельный фрагмент вдруг длиннее лимита — режем по абзацам."""
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
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return chunks


def _fragments(text: str) -> list[str]:
    """Разбить ответ Шаманки на сообщения по разделителю `---` (одна мысль = одно)."""
    parts = [p.strip() for p in _FRAGMENT_SEP.split(text)]
    parts = [p for p in parts if p]
    messages: list[str] = []
    for part in parts:
        messages.extend(_split_long(part))
    return messages or [text.strip()]


def _card_list(drawn: list[tuple[str, str]]) -> str:
    """Список выпавших карт: названия жирным, перевёрнутые — с подписью."""
    lines = []
    for card, orient in drawn:
        if orient == deck.REVERSED:
            lines.append(f"<b>{card}</b> – перевёрнутая")
        else:
            lines.append(f"<b>{card}</b>")
    return "\n".join(lines)


@router.message(F.text.in_(READING_BUTTONS))
async def start_reading(message: Message, state: FSMContext) -> None:
    """Тап по кнопке «Сделать расклад» / «ещё один» — просим назвать вопрос."""
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(
            "Мы ещё не сидели у одного огня. Набери /start – и я узнаю тебя 🌙"
        )
        return
    await state.set_state(Reading.waiting_question)
    await message.answer(
        "О чём молчит твоё сердце? Назови – и я услышу, что скажут карты."
    )


@router.message(Reading.waiting_question, F.text, ~F.text.startswith("/"),
                ~F.text.in_(READING_BUTTONS))
async def do_reading(message: Message, state: FSMContext) -> None:
    """Вопрос назван — валидируем и, если внятно, делаем расклад."""
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await state.clear()
        await message.answer(
            "Мы ещё не сидели у одного огня. Набери /start – и я узнаю тебя 🌙"
        )
        return

    question = message.text.strip()

    # Отсекаем билиберду — остаёмся в ожидании и просим перефразировать.
    if not await asyncio.to_thread(is_meaningful_question, question):
        await message.answer(
            "Твои слова рассыпались, как песок сквозь пальцы – я не разобрала "
            "вопроса. Спроси иначе, яснее: о чём душа хочет знать? 🌙"
        )
        return

    await state.clear()
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    notice = await message.answer("Тасую тени, слушаю огонь… 🔥")

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
            "Дым сегодня густой, я не вижу ясно – вернись ко мне позже 🌙"
        )
        return

    await notice.delete()
    await card_images.send_album(message, drawn)
    await message.answer(_card_list(drawn))  # названия карт жирным, перевёрнутые с подписью
    messages = _fragments(text)
    for i, part in enumerate(messages):
        # на последнем сообщении — кнопка «сделать ещё один расклад».
        await message.answer(
            part, reply_markup=AGAIN_KEYBOARD if i == len(messages) - 1 else None
        )


@router.message(F.text, ~F.text.startswith("/"))
async def nudge_to_button(message: Message) -> None:
    """Любой текст вне сценария: расклад — только по кнопке."""
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(
            "Мы ещё не сидели у одного огня. Набери /start – и я узнаю тебя 🌙"
        )
        return
    await message.answer(
        "Когда захочешь заглянуть в карты – коснись «Сделать расклад» внизу. 🔮",
        reply_markup=MAIN_KEYBOARD,
    )
