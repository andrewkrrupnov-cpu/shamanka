"""Постоянные reply-клавиатуры бота (кнопки под полем ввода)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Тексты кнопок; используются и как ярлыки, и как фильтры в хендлерах.
MAKE_READING = "🔮 Сделать расклад"
MAKE_ANOTHER = "🔮 Сделать ещё один расклад"

# Обе кнопки запускают один и тот же сценарий расклада.
READING_BUTTONS = {MAKE_READING, MAKE_ANOTHER}

# Стартовая клавиатура (после онбординга и у вернувшихся).
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=MAKE_READING)]],
    resize_keyboard=True,
    input_field_placeholder="Коснись кнопки, чтобы начать…",
)

# Клавиатура после готового расклада.
AGAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=MAKE_ANOTHER)]],
    resize_keyboard=True,
    input_field_placeholder="Коснись кнопки для нового расклада…",
)
