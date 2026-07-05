"""Карта дня: одна случайная карта + короткое предсказание на день.

- Кнопка «🌙 Карта дня» — тянет карту прямо сейчас (бесплатно). Карта детерминирована
  на пару (пользователь, дата), поэтому в течение дня одна и та же.
- Тумблер подписки: «Подключить/Отключить карту дня» (users.daily_card).
- Утренняя рассылка подписчикам — фоновым циклом daily_loop().
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message

from . import cards as card_images
from . import db, deck, llm
from .keyboards import DAILY_CARD, DAILY_OFF, DAILY_ON, main_keyboard

logger = logging.getLogger("shamanka.daily")

router = Router(name="daily")

DAILY_HOUR_UTC = 6  # ~09:00 МСК — время утренней рассылки
_NOT_ONBOARDED = "Мы ещё не сидели у одного огня. Набери /start – и я узнаю тебя 🌙"


def _daily_seed(user_id: int, day: date) -> int:
    """Стабильный сид на пару (пользователь, дата) — карта дня не меняется за день."""
    h = hashlib.sha256(f"{user_id}:{day.isoformat()}".encode()).hexdigest()
    return int(h[:8], 16)


def _caption(card: str, orient: str, prediction: str) -> str:
    rev = " (перевёрнутая)" if orient == deck.REVERSED else ""
    header = f"🌙 <b>Карта дня — {card}{rev}</b>"
    body = html.escape(prediction.replace("**", ""), quote=False)
    return f"{header}\n\n{body}"


async def _send_daily(bot, user: db.User) -> None:
    """Вытянуть карту дня для пользователя и отправить картинку + предсказание."""
    card, orient = deck.draw(1, seed=_daily_seed(user.id, date.today()))[0]
    prediction = await asyncio.to_thread(
        llm.daily_card, card, orient,
        name=user.name or "", gender=db.gender_ru(user.gender),
    )
    caption = _caption(card, orient, prediction)
    inp = card_images.card_input(card, orient)  # перевёрнутую повернёт на 180°
    if inp is not None and len(caption) <= 1024:
        await bot.send_photo(user.id, inp, caption=caption)
    else:
        if inp is not None:
            await bot.send_photo(user.id, inp)
        await bot.send_message(user.id, caption)


@router.message(F.text == DAILY_CARD)
async def on_daily_card(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(_NOT_ONBOARDED)
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        await _send_daily(message.bot, user)
    except Exception:
        logger.exception("Карта дня не открылась для %s", user.id)
        await message.answer(
            "Дым сегодня густой, карта дня не открылась – загляни чуть позже 🌙"
        )


@router.message(F.text == DAILY_ON)
async def on_subscribe(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(_NOT_ONBOARDED)
        return
    await db.set_daily_card(user.id, True)
    await message.answer(
        "🔔 Готово. Каждое утро я буду присылать тебе карту дня.",
        reply_markup=main_keyboard(True),
    )


@router.message(F.text == DAILY_OFF)
async def on_unsubscribe(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(_NOT_ONBOARDED)
        return
    await db.set_daily_card(user.id, False)
    await message.answer(
        "🔕 Больше не буду присылать карту дня. Захочешь вернуть – кнопка ниже.",
        reply_markup=main_keyboard(False),
    )


async def daily_loop(bot) -> None:
    """Фоновый цикл: раз в сутки в DAILY_HOUR_UTC рассылает карту дня подписчикам."""
    while True:
        now = datetime.utcnow()
        target = now.replace(hour=DAILY_HOUR_UTC, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            subs = await db.list_daily_subscribers()
            logger.info("Рассылка карты дня: %d подписчиков", len(subs))
            for user in subs:
                try:
                    await _send_daily(bot, user)
                    await asyncio.sleep(0.05)  # мягкий троттлинг под лимиты Telegram
                except Exception:
                    logger.exception("Карта дня не доставлена %s", user.id)
        except Exception:
            logger.exception("Сбой утренней рассылки карты дня")
