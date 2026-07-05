"""Постоянные reply-клавиатуры бота (всегда видимое меню под полем ввода)."""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Тексты кнопок; используются и как ярлыки, и как фильтры в хендлерах.
MAKE_READING = "🔮 Сделать расклад"
MAKE_ANOTHER = "🔮 Сделать ещё один расклад"
DAILY_CARD = "🌙 Карта дня"
DAILY_ON = "🔔 Подключить карту дня"
DAILY_OFF = "🔕 Отключить карту дня"
BUY_READINGS = "💫 Купить расклады"
GIFT_READINGS = "🎁 Подарить расклады"
PROFILE = "👤 Мой профиль"
ACTIVATE_PROMO = "🎟 Активировать промокод"

# Кнопки, запускающие сценарий расклада.
READING_BUTTONS = {MAKE_READING, MAKE_ANOTHER}

# Все ярлыки меню — чтобы не путать их с вводом (промокод, вопрос и т.п.).
ALL_BUTTONS = {
    MAKE_READING, MAKE_ANOTHER, DAILY_CARD, DAILY_ON, DAILY_OFF,
    BUY_READINGS, GIFT_READINGS, PROFILE, ACTIVATE_PROMO,
}


def main_keyboard(daily_on: bool = False, *, again: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню. daily_on — подписан ли пользователь на карту дня (тумблер
    переключается). again=True — после расклада («ещё один»)."""
    reading_btn = MAKE_ANOTHER if again else MAKE_READING
    daily_toggle = DAILY_OFF if daily_on else DAILY_ON
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=reading_btn)],
            [KeyboardButton(text=DAILY_CARD), KeyboardButton(text=daily_toggle)],
            [KeyboardButton(text=BUY_READINGS), KeyboardButton(text=GIFT_READINGS)],
            [KeyboardButton(text=PROFILE), KeyboardButton(text=ACTIVATE_PROMO)],
        ],
        resize_keyboard=True,
        is_persistent=True,  # меню всегда открыто, не прячется в контролах Telegram
        input_field_placeholder="Коснись кнопки меню…",
    )
