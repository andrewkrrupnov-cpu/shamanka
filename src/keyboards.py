"""Постоянная reply-клавиатура бота (кнопки под полем ввода)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Текст кнопки; используется и как ярлык, и как фильтр в хендлерах.
MAKE_READING = "🔮 Сделать расклад"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=MAKE_READING)]],
    resize_keyboard=True,
    input_field_placeholder="Назови свой вопрос…",
)
