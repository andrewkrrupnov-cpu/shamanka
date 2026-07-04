"""Картинки карт и отправка их альбомом в Telegram.

Файлы лежат в assets/cards/ и называются по индексу карты в FULL_DECK:
00.<ext> — FULL_DECK[0] (Шут), 01 — Маг, … 77 — Король Жезлов. Расширение любое
из (jpg, jpeg, png, webp). Так имя файла не зависит от кириллицы и регистра.

Если картинок нет (владелец ещё не залил колоду) — альбом просто не отправляется,
бот присылает только текст. Ориентация карты передаётся в подписи и в тексте
трактовки; изображение показываем «как есть» (без физического переворота).
"""
from __future__ import annotations

from pathlib import Path

from aiogram.types import FSInputFile, InputMediaPhoto, Message

from .deck import FULL_DECK

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "cards"
_EXTS = ("jpg", "jpeg", "png", "webp")

# Telegram: не больше 10 медиа в одном альбоме.
_MEDIA_GROUP_LIMIT = 10


def image_for(card: str) -> Path | None:
    """Путь к картинке карты или None, если файла нет."""
    try:
        idx = FULL_DECK.index(card)
    except ValueError:
        return None
    for ext in _EXTS:
        path = ASSETS_DIR / f"{idx:02d}.{ext}"
        if path.exists():
            return path
    return None


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def send_album(
    message: Message, drawn: list[tuple[str, str]], *, caption: str | None = None
) -> bool:
    """Отправить вытянутые карты альбомом. True — если что-то отправили.

    caption (HTML) — подпись под альбомом; ставится на первую картинку первой
    группы, поэтому список карт идёт ОДНИМ сообщением с картинками, без дубля.
    Карты без картинок пропускаются. Альбом бьётся на группы по 10 (лимит TG).
    """
    paths = [p for card, _ in drawn if (p := image_for(card)) is not None]
    if not paths:
        return False
    first_batch = True
    for batch in _chunk(paths, _MEDIA_GROUP_LIMIT):
        media = []
        for i, path in enumerate(batch):
            if first_batch and i == 0 and caption:
                media.append(InputMediaPhoto(
                    media=FSInputFile(path), caption=caption, parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(media=FSInputFile(path)))
        await message.answer_media_group(media)
        first_batch = False
    return True
