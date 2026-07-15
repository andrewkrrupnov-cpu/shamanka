"""Главный поток: расклад запускается ТОЛЬКО кнопкой «Сделать расклад».

Сценарий:
  1. Тап по кнопке → просим назвать вопрос (входим в состояние ожидания вопроса).
  2. Пользователь пишет вопрос → валидируем (is_meaningful_question): билиберду
     отсекаем и просим перефразировать.
  3. classify(question)      → spread_id (src/classifier.py, LLM по правилам)
  4. deck.draw(n)            → карты без повторов, ориентация 50/50 (src/deck.py)
  5. db.create_reading(...)  → фиксируем тягу В БД ДО генерации текста
  6. llm.interpret(context)  → трактовка Прорицательницы (src/llm.py, Gemini)
  7. карты альбомом + текст фрагментами; в конце — кнопка «ещё один расклад».

Просто набранный текст (без кнопки) расклад НЕ запускает — бот мягко напоминает
про кнопку. Сервис бесплатный: пейволла нет. classify/interpret синхронные —
крутим в asyncio.to_thread, чтобы не блокировать event loop aiogram.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import cards as card_images
from . import db, deck, payments
from .classifier import classify, is_meaningful_question
from .config import load_spreads
from .keyboards import READING_BUTTONS, main_keyboard
from .llm import interpret, interpret_clarification, suggest_clarifications

logger = logging.getLogger("shamanka.reading")

router = Router(name="reading")


class Reading(StatesGroup):
    waiting_question = State()

FALLBACK_SPREAD_ID = "proshloe_nastoyashee_budushee"
TELEGRAM_LIMIT = 4096  # максимум символов в одном сообщении

# Статичные варианты реплик (ротация random.choice, без затрат на токены).
ASK_PROMPTS = [
    "О чём молчит твоё сердце? Назови – и я услышу, что скажут карты.",
    "Говори, что тревожит. Карты уже слушают.",
    "Задай свой вопрос – и я загляну в дым, что вьётся над огнём.",
    "О чём хочешь узнать? Назови – и тени начнут складываться в узор.",
    "Спрашивай. Что бы ни лежало на душе – карты ответят.",
    "Назови то, что не даёт покоя. Я вгляжусь в нити твоей судьбы.",
    "Открой мне свой вопрос – и пусть карты укажут дорогу.",
    "О чём думаешь в этот час? Скажи – и я разложу карты на твою тревогу.",
    "Что привело тебя к огню? Назови вопрос, и он ответит образами.",
    "Доверь мне свой вопрос. Карты не лгут тому, кто спрашивает честно.",
]

NOTICE_MESSAGES = [
    "Тасую тени, слушаю огонь… 🔥",
    "Карты ложатся одна за другой… 🌙",
    "Вглядываюсь в дым над костром… 🔥",
    "Нити твоей судьбы сплетаются… ✨",
    "Слушаю, что шепчет пламя… 🔥",
    "Раскладываю карты на песке… 🌙",
    "Тени говорят – я внимаю… ✨",
    "Пламя качнулось, узор проявляется… 🔥",
    "Вопрошаю карты о тебе… 🌙",
    "Дым вьётся, складывая ответ… ✨",
]

ERROR_MESSAGES = [
    "Дым сегодня густой, я не вижу ясно – вернись ко мне позже 🌙",
    "Пламя качнулось и погасло – спроси меня снова чуть погодя 🌙",
    "Тени сомкнулись, ответ ускользнул. Приходи чуть позже 🌙",
    "Карты молчат в этот час – попробуй ещё раз немного погодя 🌙",
    "Ветер спутал нити – дай мне срок и спроси заново 🌙",
]

# Расклады читаем один раз (позиции фиксированы, файл при работе не меняется).
_spreads_cache: dict | None = None


def _spreads() -> dict:
    global _spreads_cache
    if _spreads_cache is None:
        _spreads_cache = load_spreads()
    return _spreads_cache


# Прорицательница разделяет мысли строкой из дефисов (`---`). По ней и бьём на сообщения.
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
    """Разбить ответ Прорицательницы на сообщения по разделителю `---` (одна мысль = одно)."""
    parts = [p.strip() for p in _FRAGMENT_SEP.split(text)]
    parts = [p for p in parts if p]
    messages: list[str] = []
    for part in parts:
        messages.extend(_split_long(part))
    return messages or [text.strip()]


def _to_html(text: str) -> str:
    """Экранируем спецсимволы и превращаем markdown-жирный **...** в <b>...</b>."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text.replace("**", "")  # непарные остатки убираем


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
    if user.free_readings <= 0:  # расклады кончились — витрина вместо вопроса
        await payments.show_paywall(message, 0)
        return
    await state.set_state(Reading.waiting_question)
    await message.answer(random.choice(ASK_PROMPTS))


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

    if user.free_readings <= 0:  # подстраховка: баланс кончился
        await state.clear()
        await payments.show_paywall(message, 0)
        return

    await state.clear()
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    notice = await message.answer(random.choice(NOTICE_MESSAGES))

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
            "gender": db.gender_ru(user.gender),
            "question": question,
            "spread_id": spread_id,
            "spread": spread,
            "cards": drawn,
        }
        raw_text = await asyncio.to_thread(interpret, context)
        text = _to_html(raw_text)  # заголовки **...** -> <b>...</b>, спецсимволы экранированы
        await db.set_reading_text(reading_id, text)
    except Exception:
        logger.exception("Не удалось построить расклад")
        await notice.edit_text(random.choice(ERROR_MESSAGES))
        return

    await notice.delete()
    # Список карт идёт подписью к альбому (одно сообщение с картинками, без дубля).
    card_list = _card_list(drawn)
    if not await card_images.send_album(message, drawn, caption=card_list):
        await message.answer(card_list)  # картинок нет — список отдельным сообщением

    remaining = await db.spend_reading(user.id)  # списываем расклад с баланса
    again_kb = main_keyboard(user.daily_card, again=True)
    messages = _fragments(text)
    for i, part in enumerate(messages):
        last = i == len(messages) - 1
        # кнопку вешаем на последнее сообщение (если нет хвоста про остаток)
        kb = again_kb if last and remaining != 0 else None
        await message.answer(part, reply_markup=kb)
    if remaining == 0:
        await message.answer(
            "🌙 Это был твой последний расклад. Чтобы открыть новые дороги – "
            "коснись «Купить расклады» внизу.",
            reply_markup=again_kb,
        )

    # Уточнения: 2 контекстных вопроса кнопками — бесплатно, по тем же картам.
    context["interpretation"] = raw_text
    await _offer_clarifications(message, reading_id, context)


def _clarify_kb(reading_id: int,
                indexed_qs: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Инлайн-кнопки уточнений: по кнопке на вопрос, callback_data=clr:{id}:{idx}."""
    rows = [[InlineKeyboardButton(text=q, callback_data=f"clr:{reading_id}:{i}")]
            for i, q in indexed_qs]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _offer_clarifications(message: Message, reading_id: int,
                                context: dict) -> None:
    """Сгенерировать и показать уточняющие вопросы. Тихо пропускаем при сбое."""
    try:
        questions = await asyncio.to_thread(suggest_clarifications, context)
    except Exception:
        logger.exception("Не удалось сгенерировать уточнения")
        return
    if not questions:
        return
    await db.set_clarifications(reading_id, questions)
    await message.answer(
        "🔮 Хочешь копнуть глубже? Коснись вопроса – отвечу по этим же картам:",
        reply_markup=_clarify_kb(reading_id, list(enumerate(questions))),
    )


@router.callback_query(F.data.startswith("clr:"))
async def on_clarify(callback: CallbackQuery) -> None:
    """Тап по уточняющему вопросу — ответ по уже вытянутым картам расклада."""
    try:
        _, rid_s, idx_s = callback.data.split(":")
        reading_id, index = int(rid_s), int(idx_s)
    except (ValueError, AttributeError):
        await callback.answer()
        return

    user = await db.get_user(callback.from_user.id)
    reading = await db.get_reading(reading_id)
    if user is None or reading is None or reading.user_id != user.id:
        await callback.answer("Этот расклад уже не найти 🌙")
        return

    used = await db.use_clarification(reading_id, index)
    if used is None:  # уже раскрыто или двойной тап
        await callback.answer("Это уточнение уже раскрыто 🌙")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    clar_question, remaining = used
    await callback.answer()

    # Обновляем клавиатуру: убираем использованную кнопку, остальные оставляем.
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_clarify_kb(reading_id, remaining) if remaining else None)
    except Exception:
        pass

    await callback.message.answer(f"🔮 <i>{_to_html(clar_question)}</i>")
    await callback.bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    notice = await callback.message.answer(random.choice(NOTICE_MESSAGES))
    try:
        spreads = _spreads()
        spread = spreads.get(reading.spread_id) or spreads[FALLBACK_SPREAD_ID]
        clar_context = {
            "name": user.name,
            "gender": db.gender_ru(user.gender),
            "question": reading.question,
            "spread_id": reading.spread_id,
            "spread": spread,
            "cards": [(c[0], c[1]) for c in reading.cards],
        }
        answer = await asyncio.to_thread(
            interpret_clarification, clar_context, clar_question)
        answer = _to_html(answer)
    except Exception:
        logger.exception("Не удалось раскрыть уточнение")
        await notice.edit_text(random.choice(ERROR_MESSAGES))
        return

    await notice.delete()
    for part in _fragments(answer):
        await callback.message.answer(part)
    if not remaining:
        await callback.message.answer(
            "Это были все уточнения к этому раскладу. Захочешь новый ответ – "
            "коснись «Сделать ещё один расклад» внизу. 🌙",
            reply_markup=main_keyboard(user.daily_card, again=True),
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
        reply_markup=main_keyboard(user.daily_card),
    )
