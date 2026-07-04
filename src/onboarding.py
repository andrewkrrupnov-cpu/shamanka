"""Онбординг новых пользователей через FSM: имя → пол.

Плашка «развлекательный формат, 18+» показывается на старте (требование этики,
см. docs/spreads_source.md, раздел «Этика»).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import db

router = Router(name="onboarding")

DISCLAIMER = (
    "🔮 <b>Шаманка</b> — расклады Таро.\n\n"
    "⚠️ Развлекательный формат, 18+. Это не медицинская, психологическая "
    "или финансовая консультация."
)

GENDER_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Женский", callback_data="gender:female"),
            InlineKeyboardButton(text="Мужской", callback_data="gender:male"),
        ],
        [InlineKeyboardButton(text="Не указывать", callback_data="gender:other")],
    ]
)

GENDER_LABELS = {"female": "женский", "male": "мужской", "other": "не указан"}


class Onboarding(StatesGroup):
    waiting_name = State()
    waiting_gender = State()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    user = await db.get_or_create_user(message.from_user.id)
    if user.onboarded:
        await message.answer(
            f"С возвращением, {user.name}! ✨\n"
            "Напиши свой вопрос — и я сделаю расклад."
        )
        return

    await state.set_state(Onboarding.waiting_name)
    await message.answer(DISCLAIMER)
    await message.answer("Как тебя зовут?")


@router.message(Onboarding.waiting_name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name or len(name) > 64:
        await message.answer("Напиши, пожалуйста, имя (до 64 символов).")
        return
    await state.update_data(name=name)
    await state.set_state(Onboarding.waiting_gender)
    await message.answer(
        f"Приятно познакомиться, {name}! Укажи свой пол:",
        reply_markup=GENDER_KEYBOARD,
    )


@router.callback_query(Onboarding.waiting_gender, F.data.startswith("gender:"))
async def got_gender(callback: CallbackQuery, state: FSMContext) -> None:
    gender = callback.data.split(":", 1)[1]
    data = await state.get_data()
    name = data.get("name", "друг")

    await db.save_profile(callback.from_user.id, name=name, gender=gender)
    await state.clear()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Готово, {name}! 🌙\n"
        "Напиши свой вопрос — и я сделаю расклад.\n\n"
        "<i>(Трактовки подключим позже — сейчас это каркас.)</i>"
    )
    await callback.answer()


@router.message(Onboarding.waiting_gender)
async def gender_needs_button(message: Message) -> None:
    await message.answer("Выбери пол кнопкой ниже 👇", reply_markup=GENDER_KEYBOARD)
