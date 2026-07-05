"""Оплата раскладов через встроенные платежи Telegram (provider-token ЮKassa).

Первый расклад бесплатный (см. db.DEFAULT_FREE_READINGS). Дальше пользователь
покупает пакеты раскладов; баланс хранится в users.free_readings.

Схема: показываем витрину пакетов → кнопка пакета шлёт Telegram-инвойс
(send_invoice с provider_token ЮKassa) → pre_checkout подтверждаем → по
successful_payment пополняем баланс. Пока provider_token не задан
(env YOOKASSA_PROVIDER_TOKEN) — показываем цены и заглушку вместо инвойса,
чтобы сделать скриншоты и подключить оплату позже.
"""
from __future__ import annotations

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from . import db
from .keyboards import BUY_READINGS, MAIN_KEYBOARD

logger = logging.getLogger("shamanka.payments")

router = Router(name="payments")

PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "").strip()
CURRENCY = "RUB"

# Пакеты: код, число раскладов, цена в КОПЕЙКАХ (Telegram считает в минимальных
# единицах валюты), название.
PACKAGES = [
    {"code": "p3", "readings": 3, "amount": 4900, "title": "3 расклада"},
    {"code": "p10", "readings": 10, "amount": 9900, "title": "10 раскладов"},
    {"code": "p100", "readings": 100, "amount": 29900, "title": "100 раскладов"},
]
PACKAGES_BY_CODE = {p["code"]: p for p in PACKAGES}


def _rub(amount_kop: int) -> str:
    return f"{amount_kop // 100} ₽"


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
                     f"<i>({one:.0f} ₽ за расклад)</i>".replace(".", ","))
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
            "Оплата вот-вот откроется – мы уже зажигаем этот огонь 🔥 "
            "Загляни чуть позже."
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


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message) -> None:
    payload = message.successful_payment.invoice_payload
    code = payload.split(":", 1)[1] if ":" in payload else ""
    pkg = PACKAGES_BY_CODE.get(code)
    if pkg is None:
        logger.error("Неизвестный payload оплаты: %s", payload)
        return
    balance = await db.add_readings(message.from_user.id, pkg["readings"])
    await message.answer(
        f"Огонь принял твою плату 🔥 +{pkg['readings']} раскладов.\n"
        f"Теперь у тебя {balance}. Коснись «Сделать расклад».",
        reply_markup=MAIN_KEYBOARD,
    )
