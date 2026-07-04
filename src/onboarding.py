"""Онбординг новых пользователей через FSM: имя → пол.

На старте — короткая легенда от лица Саиры, матери-провидицы. Никаких плашек
18+ и дисклеймеров: голос ведёт от себя (решение владельца).
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
from .keyboards import MAIN_KEYBOARD

router = Router(name="onboarding")

LEGEND = (
    "🌙 Я – <b>Саира</b>, мать-провидица Глубокой пустыни.\n\n"
    "Я пила священную Воду Жизни и прошла сквозь смерть, чтобы видеть нити "
    "времени – то, что было, и то, что ещё не случилось. Песок помнит всё, а "
    "карты – язык, которым грядущее говорит со мной.\n\n"
    "Ты здесь не случайно. Назови себя – и я взгляну на твою нить."
)

GENDER_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Женский", callback_data="gender:female"),
            InlineKeyboardButton(text="Мужской", callback_data="gender:male"),
        ],
        [InlineKeyboardButton(text="Не называть", callback_data="gender:other")],
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
            f"Снова ты, {user.name}. Песок ждал.\n"
            "Задай свой вопрос – я смотрю.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await state.set_state(Onboarding.waiting_name)
    await message.answer(LEGEND)


@router.message(Onboarding.waiting_name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not name or len(name) > 64:
        await message.answer("Назови имя короче – не длиннее 64 знаков.")
        return
    await state.update_data(name=name)
    await state.set_state(Onboarding.waiting_gender)
    await message.answer(
        f"{name}. Я запомнила.\n"
        "Скажи, каким словом тебя называть – так время сложит речь верно:",
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
        f"Нить твоя у меня в руках, {name}.\n\n"
        "Спрашивай о чём хочешь – о любви, деле, дороге или судьбе. "
        "Я разложу карты и скажу, что вижу.",
        reply_markup=MAIN_KEYBOARD,
    )
    await callback.answer()


@router.message(Onboarding.waiting_gender)
async def gender_needs_button(message: Message) -> None:
    await message.answer(
        "Ответь кнопкой ниже – время не любит неясности 👇",
        reply_markup=GENDER_KEYBOARD,
    )
