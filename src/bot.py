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
