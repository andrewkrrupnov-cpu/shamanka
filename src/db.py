"""Модели и запросы к БД (PostgreSQL, SQLAlchemy async).

Таблицы:
  users    — профиль пользователя + счётчик бесплатных раскладов (пейволл пока
             не активен, сервис бесплатный; поле заложено в схему на будущее).
  readings — история раскладов. Результат тяги фиксируется ЗДЕСЬ ДО генерации
             текста (требование CLAUDE.md), трактовка дописывается после.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Сколько бесплатных раскладов даётся новому пользователю (пейволл — позже).
DEFAULT_FREE_READINGS = 3


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
