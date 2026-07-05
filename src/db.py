"""Модели и запросы к БД (PostgreSQL, SQLAlchemy async).

Таблицы:
  users    — профиль пользователя + счётчик бесплатных раскладов (пейволл пока
             не активен, сервис бесплатный; поле заложено в схему на будущее).
  readings — история раскладов. Результат тяги фиксируется ЗДЕСЬ ДО генерации
             текста (требование CLAUDE.md), трактовка дописывается после.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Первый расклад — бесплатно. Дальше баланс пополняется покупкой пакетов.
# Поле free_readings теперь = остаток раскладов (бесплатные + купленные).
DEFAULT_FREE_READINGS = 1


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    # telegram user id
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    free_readings: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_FREE_READINGS
    )
    onboarded: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Подписка на ежедневную «карту дня».
    daily_card: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Reading(Base):
    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(primary_key=True)  # autoincrement
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), index=True, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    spread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spread_title: Mapped[str] = mapped_column(String(128), nullable=False)
    # Вытянутые карты: [["Солнце", "прямая"], ["10 Мечей", "перевёрнутая"], ...]
    cards: Mapped[list] = mapped_column(JSON, nullable=False)
    interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Promo(Base):
    __tablename__ = "promocodes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    readings: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# Инициализируется в bot.py при старте.
_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> None:
    global _engine, _sessionmaker
    _engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


async def create_tables() -> None:
    assert _engine is not None, "init_engine() не вызван"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all не добавляет новые колонки в существующие таблицы — доводим руками.
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "daily_card BOOLEAN NOT NULL DEFAULT FALSE"
        ))


# Пол хранится кодом ("male"/"female"/"other"); в промпт нужен русский род.
_GENDER_RU = {
    "male": "мужской", "мужской": "мужской", "м": "мужской",
    "female": "женский", "женский": "женский", "ж": "женский",
}


def gender_ru(code: str | None) -> str:
    return _GENDER_RU.get((code or "").strip().lower(), "не указан")


def session() -> AsyncSession:
    assert _sessionmaker is not None, "init_engine() не вызван"
    return _sessionmaker()


async def get_user(user_id: int) -> User | None:
    async with session() as s:
        return await s.get(User, user_id)


async def get_or_create_user(user_id: int) -> User:
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            user = User(id=user_id, free_readings=DEFAULT_FREE_READINGS)
            s.add(user)
            await s.commit()
            await s.refresh(user)
        return user


async def save_profile(user_id: int, name: str, gender: str) -> User:
    """Сохранить результат онбординга (имя + пол), пометить onboarded."""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            user = User(id=user_id, free_readings=DEFAULT_FREE_READINGS)
            s.add(user)
        user.name = name
        user.gender = gender
        user.onboarded = True
        await s.commit()
        await s.refresh(user)
        return user


async def create_reading(
    user_id: int,
    question: str,
    spread_id: str,
    spread_title: str,
    cards: list[tuple[str, str]],
) -> int:
    """Зафиксировать факт тяги ДО генерации текста. Возвращает id расклада."""
    async with session() as s:
        reading = Reading(
            user_id=user_id,
            question=question,
            spread_id=spread_id,
            spread_title=spread_title,
            cards=[[card, orient] for card, orient in cards],
        )
        s.add(reading)
        await s.commit()
        await s.refresh(reading)
        return reading.id


async def set_reading_text(reading_id: int, interpretation: str) -> None:
    """Дописать трактовку к уже сохранённому раскладу."""
    async with session() as s:
        reading = await s.get(Reading, reading_id)
        if reading is not None:
            reading.interpretation = interpretation
            await s.commit()


async def spend_reading(user_id: int) -> int:
    """Списать один расклад с баланса. Возвращает остаток или -1, если нечего."""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None or user.free_readings <= 0:
            return -1
        user.free_readings -= 1
        await s.commit()
        return user.free_readings


async def add_readings(user_id: int, n: int) -> int:
    """Пополнить баланс раскладов (после оплаты). Возвращает новый остаток."""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is None:
            return 0
        user.free_readings += n
        await s.commit()
        await s.refresh(user)
        return user.free_readings


async def set_daily_card(user_id: int, on: bool) -> None:
    """Подписать/отписать пользователя от ежедневной карты дня."""
    async with session() as s:
        user = await s.get(User, user_id)
        if user is not None:
            user.daily_card = on
            await s.commit()


async def list_daily_subscribers() -> list[User]:
    """Все, кто подписан на карту дня (для утренней рассылки)."""
    async with session() as s:
        res = await s.execute(select(User).where(User.daily_card.is_(True)))
        return list(res.scalars().all())


# Без похожих символов (O/0, I/1) — чтобы промокод не путали при пересылке.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


async def create_promo(readings: int, created_by: int | None = None) -> str:
    """Сгенерировать уникальный одноразовый промокод на N раскладов."""
    async with session() as s:
        for _ in range(10):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            if await s.get(Promo, code) is None:
                s.add(Promo(code=code, readings=readings, created_by=created_by))
                await s.commit()
                return code
    raise RuntimeError("не удалось сгенерировать уникальный промокод")


async def redeem_promo(code: str, user_id: int) -> tuple[bool, int | str]:
    """Активировать промокод: (True, N раскладов) или (False, причина).

    Одноразово и атомарно: строка блокируется FOR UPDATE, повторная активация
    невозможна. Причины: 'not_found', 'used', 'no_user'.
    """
    code = (code or "").strip().upper()
    if not code:
        return (False, "not_found")
    async with session() as s:
        promo = await s.get(Promo, code, with_for_update=True)
        if promo is None:
            return (False, "not_found")
        if promo.used_by is not None:
            return (False, "used")
        user = await s.get(User, user_id)
        if user is None:
            return (False, "no_user")
        promo.used_by = user_id
        promo.used_at = datetime.now(timezone.utc)
        user.free_readings += promo.readings
        await s.commit()
        return (True, promo.readings)
