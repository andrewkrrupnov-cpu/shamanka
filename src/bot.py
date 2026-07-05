"""Точка входа: инициализация БД, роутеры aiogram, long polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from . import db
from .config import load_config
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
    dp.include_router(reading_router)

    logger.info("Бот запускается (long polling)…")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено")
