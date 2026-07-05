"""Онбординг новых пользователей через FSM: имя → пол.

На старте — короткая легенда от лица Шаманки (у костра, образно). Никаких плашек
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
from .keyboards import main_keyboard

router = Router(name="onboarding")

LEGEND = (
    "Садись ближе к огню. 🌙\n\n"
    "Я – <b>Шаманка</b>. Я видела много лун и много дорог, и каждая приводила "
    "кого-то ко мне. Вот и ты здесь – не случайно.\n\n"
    "Назови своё имя – огонь запомнит его."
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
            f"Снова ты, {user.name}. Огонь помнит тебя.\n"
            "Коснись «Сделать расклад» внизу, когда будешь готова.",
            reply_markup=main_keyboard(user.daily_card),
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
        f"{name}. Огонь принял твоё имя.\n"
        "Скажи, каким словом тебя окликать – так речь ляжет верно:",
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
        f"Вот и всё, {name}. Теперь между нами есть нить. 🌿\n\n"
        "Первый расклад – мой дар тебе. Коснись «Сделать расклад» внизу, когда "
        "захочешь заглянуть в карты – о любви, дороге, деле или тревоге.",
        reply_markup=main_keyboard(False),
    )
    await callback.answer()


@router.message(Onboarding.waiting_gender)
async def gender_needs_button(message: Message) -> None:
    await message.answer(
        "Коснись одной из тропок ниже – огонь ждёт ответа 👇",
        reply_markup=GENDER_KEYBOARD,
    )
