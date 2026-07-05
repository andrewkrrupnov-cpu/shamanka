"""Точка входа: инициализация БД, роутеры aiogram, long polling."""
from __future__ import annotations

import asyncio
import functools
import logging
import socket

import aiohttp

# Этот VPS фильтрует часть IP Telegram, а DNS отдаёт заблокированный IPv4 и
# нероутируемый IPv6 → aiohttp зависает/таймаутит на старте. Заставляем ВСЕ
# TCP-соединения aiogram идти по IPv4 и резолвить через getaddrinfo (уважает
# /etc/hosts, куда docker-compose пинит рабочий IP api.telegram.org).
_orig_connector_init = aiohttp.TCPConnector.__init__


@functools.wraps(_orig_connector_init)
def _ipv4_connector_init(self, *args, **kwargs):
    kwargs.setdefault("family", socket.AF_INET)
    kwargs.setdefault("resolver", aiohttp.ThreadedResolver())
    _orig_connector_init(self, *args, **kwargs)


aiohttp.TCPConnector.__init__ = _ipv4_connector_init

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from . import db
from .config import load_config
from .daily import daily_loop
from .daily import router as daily_router
from .onboarding import router as onboarding_router
from .payments import router as payments_router
from .reading import router as reading_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("shamanka")

# Экран до нажатия «Старт»: описание бота (setMyDescription) и короткое описание.
# Картинку этого экрана Bot API менять не даёт — только @BotFather вручную.
BOT_DESCRIPTION = (
    "🌙 Шаманка — глубокие расклады Таро у костра на краю времён.\n\n"
    "Ты задаёшь вопрос — о любви, дороге, деле или судьбе, — а я сама выбираю "
    "узор расклада под твою задачу. Для каждого вопроса рождается своя "
    "методология, а не один шаблон на всех: карты ложатся так, как велит именно "
    "твой вопрос.\n\n"
    "Что выпало — прочту глубоко и по делу, тёплым, но ясным голосом.\n\n"
    "Нажми «Старт» и подойди ближе к огню."
)
BOT_SHORT_DESCRIPTION = (
    "Глубокие расклады Таро у костра. Для каждого вопроса — свой узор расклада, "
    "выбранный под твою задачу. 🌙"
)


async def _apply_bot_profile(bot: Bot) -> None:
    """Обновить описание бота (экран до «Старт»). Не критично для запуска."""
    try:
        await asyncio.wait_for(bot.set_my_description(BOT_DESCRIPTION), timeout=15)
        await asyncio.wait_for(
            bot.set_my_short_description(BOT_SHORT_DESCRIPTION), timeout=15
        )
        logger.info("Описание бота обновлено")
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось обновить описание бота (%s)", e)


async def main() -> None:
    config = load_config()

    db.init_engine(config.database_url)
    await db.create_tables()
    logger.info("БД готова, таблицы созданы")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    # Порядок важен: онбординг (FSM-состояния) раньше, чтобы во время знакомства
    # сообщения не перехватывал общий обработчик вопросов.
    dp.include_router(onboarding_router)
    dp.include_router(payments_router)  # раньше reading: ловит кнопку «Купить» и оплату
    dp.include_router(daily_router)     # кнопки «Карта дня» и подписки
    dp.include_router(reading_router)

    asyncio.create_task(daily_loop(bot))  # утренняя рассылка карты дня подписчикам

    await _apply_bot_profile(bot)  # описание экрана до «Старт»

    logger.info("Бот запускается (long polling)…")
    # На этом VPS первый запрос к Telegram изредка залипает — не даём delete_webhook
    # заблокировать старт (сам polling дальше переживает сетевые сбои и ретраит).
    try:
        await asyncio.wait_for(
            bot.delete_webhook(drop_pending_updates=True), timeout=15
        )
    except Exception as e:  # noqa: BLE001 — старту важнее дойти до polling
        logger.warning("delete_webhook не удался (%s) — продолжаем в polling", e)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено")
