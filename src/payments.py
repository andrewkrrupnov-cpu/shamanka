"""Оплата, подарочные промокоды, профиль/баланс.

- Первый расклад бесплатный (db.DEFAULT_FREE_READINGS). Дальше — покупка пакетов
  (встроенные платежи Telegram, provider-token ЮKassa).
- «Подарить расклады»: оплата 299 ₽ → одноразовый промокод на 100 раскладов +
  готовое к пересылке сообщение с кнопкой-ссылкой активации.
- «Активировать промокод»: ввод кода → начисление раскладов.
- «Профиль»: остаток раскладов и статус подписки на карту дня.

Пока YOOKASSA_PROVIDER_TOKEN пуст — показываем цены и заглушку вместо инвойса.
"""
from __future__ import annotations

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from . import db
from .keyboards import (
    ACTIVATE_PROMO,
    ALL_BUTTONS,
    BUY_READINGS,
    GIFT_READINGS,
    PROFILE,
    main_keyboard,
)

logger = logging.getLogger("shamanka.payments")

router = Router(name="payments")

PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "").strip()
CURRENCY = "RUB"

# Пакеты для себя: код, число раскладов, цена в КОПЕЙКАХ, название.
PACKAGES = [
    {"code": "p3", "readings": 3, "amount": 4900, "title": "3 расклада"},
    {"code": "p10", "readings": 10, "amount": 9900, "title": "10 раскладов"},
    {"code": "p100", "readings": 100, "amount": 29900, "title": "100 раскладов"},
]
PACKAGES_BY_CODE = {p["code"]: p for p in PACKAGES}

# Подарок: сколько раскладов и цена.
GIFT_COUNT = 100
GIFT_AMOUNT = 29900

_NOT_ONBOARDED = "Мы ещё не сидели у одного огня. Набери /start – и я узнаю тебя 🌙"


class Activation(StatesGroup):
    waiting_code = State()


def _rub(amount_kop: int) -> str:
    return f"{amount_kop // 100} ₽"


# --------------------------------------------------------------------------- #
#  Профиль / баланс
# --------------------------------------------------------------------------- #
@router.message(F.text == PROFILE)
async def on_profile(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(_NOT_ONBOARDED)
        return
    daily = "включена 🔔" if user.daily_card else "выключена 🔕"
    edit_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🔄 Обновить информацию", callback_data="profile:edit")]])
    await message.answer(
        "👤 <b>Мой профиль</b>\n\n"
        f"Имя: <b>{user.name}</b>\n"
        f"🔮 Раскладов на балансе: <b>{user.free_readings}</b>\n"
        f"🌙 Карта дня: {daily}",
        reply_markup=edit_kb,
    )


# --------------------------------------------------------------------------- #
#  Покупка пакетов для себя
# --------------------------------------------------------------------------- #
def paywall_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{p['title']} — {_rub(p['amount'])}",
                              callback_data=f"buy:{p['code']}")]
        for p in PACKAGES
    ])


def paywall_text(balance: int) -> str:
    head = ("🔮 <b>Твои расклады закончились</b>" if balance <= 0
            else "🔮 <b>Купить расклады</b>")
    lines = [head, "",
             "Первый расклад – мой дар. Дальше выбери, сколько дорог тебе открыть:",
             ""]
    for p in PACKAGES:
        one = p["amount"] / p["readings"] / 100
        lines.append(f"▪️ <b>{p['title']}</b> — {_rub(p['amount'])}  "
                     f"<i>({one:.0f} ₽ за расклад)</i>")
    lines += ["", "Оплата картой прямо в Telegram – расклады зачислятся сразу. ✨"]
    return "\n".join(lines)


async def show_paywall(message: Message, balance: int) -> None:
    await message.answer(paywall_text(balance), reply_markup=paywall_keyboard())


@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    await show_paywall(message, user.free_readings if user else 0)


@router.message(F.text == BUY_READINGS)
async def btn_buy(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    await show_paywall(message, user.free_readings if user else 0)


@router.callback_query(F.data.startswith("buy:"))
async def on_buy(callback: CallbackQuery) -> None:
    pkg = PACKAGES_BY_CODE.get(callback.data.split(":", 1)[1])
    if pkg is None:
        await callback.answer("Пакет не найден")
        return
    await callback.answer()
    if not PROVIDER_TOKEN:
        await callback.message.answer(
            "Оплата вот-вот откроется – мы уже зажигаем этот огонь 🔥 Загляни позже."
        )
        return
    await callback.message.answer_invoice(
        title=f"Шаманка: {pkg['title']}",
        description=f"{pkg['readings']} раскладов Таро от Шаманки.",
        payload=f"pkg:{pkg['code']}",
        provider_token=PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=[LabeledPrice(label=pkg["title"], amount=pkg["amount"])],
    )


# --------------------------------------------------------------------------- #
#  Подарить расклады (промокод)
# --------------------------------------------------------------------------- #
@router.message(F.text == GIFT_READINGS)
async def on_gift(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=f"Подарить {GIFT_COUNT} раскладов — {_rub(GIFT_AMOUNT)}",
        callback_data="gift:buy")]])
    await message.answer(
        "🎁 <b>Подарить расклады</b>\n\n"
        f"Оплати {_rub(GIFT_AMOUNT)} – и я создам уникальный промокод на "
        f"{GIFT_COUNT} раскладов. Перешлёшь его другу, а он активирует в боте.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "gift:buy")
async def gift_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    if not PROVIDER_TOKEN:
        await callback.message.answer(
            "Оплата вот-вот откроется – мы уже зажигаем этот огонь 🔥 Загляни позже."
        )
        return
    await callback.message.answer_invoice(
        title=f"Шаманка: подарок {GIFT_COUNT} раскладов",
        description=f"Промокод на {GIFT_COUNT} раскладов Таро в подарок.",
        payload="gift:p100",
        provider_token=PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=[LabeledPrice(label=f"Подарок: {GIFT_COUNT} раскладов",
                             amount=GIFT_AMOUNT)],
    )


async def _send_gift_message(message: Message, code: str) -> None:
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start=promo_{code}"
    gift_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🔮 Активировать", url=link)]])
    await message.answer("Готово! 🎁 Перешли сообщение ниже тому, кого хочешь одарить:")
    await message.answer(
        "🎁 <b>Вам подарили доступ к раскладам Шаманки!</b>\n\n"
        f"Промокод: <code>{code}</code>\n"
        f"Внутри — {GIFT_COUNT} раскладов Таро.\n\n"
        "Нажмите кнопку ниже, чтобы активировать в боте 🌙",
        reply_markup=gift_kb,
    )


# --------------------------------------------------------------------------- #
#  Активация промокода
# --------------------------------------------------------------------------- #
@router.message(F.text == ACTIVATE_PROMO)
async def on_activate(message: Message, state: FSMContext) -> None:
    user = await db.get_user(message.from_user.id)
    if user is None or not user.onboarded:
        await message.answer(_NOT_ONBOARDED)
        return
    await state.set_state(Activation.waiting_code)
    await message.answer("Пришли промокод – и я открою тебе расклады 🌙")


@router.message(Activation.waiting_code, F.text, ~F.text.in_(ALL_BUTTONS),
                ~F.text.startswith("/"))
async def on_promo_code(message: Message, state: FSMContext) -> None:
    await state.clear()
    ok, res = await db.redeem_promo(message.text, message.from_user.id)
    user = await db.get_user(message.from_user.id)
    kb = main_keyboard(user.daily_card if user else False)
    if ok:
        await message.answer(
            f"🎁 Промокод принят – тебе открыто +{res} раскладов!\n"
            f"Теперь у тебя {user.free_readings if user else res}. "
            "Коснись «Сделать расклад».",
            reply_markup=kb,
        )
    elif res == "used":
        await message.answer("Этот промокод уже использован 🌙", reply_markup=kb)
    else:
        await message.answer(
            "Такого промокода нет – проверь и пришли ещё раз через «Активировать "
            "промокод» 🌙", reply_markup=kb,
        )


# --------------------------------------------------------------------------- #
#  Приём оплаты (для себя и подарок)
# --------------------------------------------------------------------------- #
@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message) -> None:
    kind, _, code = message.successful_payment.invoice_payload.partition(":")

    if kind == "gift":
        promo = await db.create_promo(GIFT_COUNT, created_by=message.from_user.id)
        await _send_gift_message(message, promo)
        return

    pkg = PACKAGES_BY_CODE.get(code)
    if pkg is None:
        logger.error("Неизвестный payload оплаты: %s", message.successful_payment.invoice_payload)
        return
    balance = await db.add_readings(message.from_user.id, pkg["readings"])
    user = await db.get_user(message.from_user.id)
    await message.answer(
        f"Огонь принял твою плату 🔥 +{pkg['readings']} раскладов.\n"
        f"Теперь у тебя {balance}. Коснись «Сделать расклад».",
        reply_markup=main_keyboard(user.daily_card if user else False),
    )
