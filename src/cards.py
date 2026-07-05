"""Картинки карт и отправка их альбомом в Telegram.

Файлы лежат в assets/cards/ и называются по индексу карты в FULL_DECK:
00.<ext> — FULL_DECK[0] (Шут), 01 — Маг, … 77 — Король Жезлов. Расширение любое
из (jpg, jpeg, png, webp). Так имя файла не зависит от кириллицы и регистра.

Если картинок нет (владелец ещё не залил колоду) — альбом просто не отправляется,
бот присылает только текст. Перевёрнутую карту показываем перевёрнутой и на
картинке — поворотом на 180° на лету (см. card_input).
"""
from __future__ import annotations

import io
from pathlib import Path

from aiogram.types import BufferedInputFile, FSInputFile, InputMediaPhoto, Message
from PIL import Image

from .deck import FULL_DECK, REVERSED

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


def card_input(card: str, orient: str):
    """Файл картинки карты для отправки. Перевёрнутую поворачиваем на 180°.

    Прямая — отдаём путь как есть (FSInputFile). Перевёрнутая — крутим на лету
    через PIL и отдаём байтами (BufferedInputFile). None — если картинки нет.
    """
    path = image_for(card)
    if path is None:
        return None
    if orient != REVERSED:
        return FSInputFile(path)
    with Image.open(path) as im:
        fmt = im.format or "PNG"
        rotated = im.transpose(Image.Transpose.ROTATE_180)
        buf = io.BytesIO()
        rotated.save(buf, format=fmt)
    return BufferedInputFile(buf.getvalue(), filename=f"{path.stem}_rev.{fmt.lower()}")


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def send_album(
    message: Message, drawn: list[tuple[str, str]], *, caption: str | None = None
) -> bool:
    """Отправить вытянутые карты альбомом. True — если что-то отправили.

    caption (HTML) — подпись под альбомом; ставится на первую картинку первой
    группы, поэтому список карт идёт ОДНИМ сообщением с картинками, без дубля.
    Перевёрнутые карты поворачиваются на 180°. Карты без картинок пропускаются.
    Альбом бьётся на группы по 10 (лимит TG).
    """
    inputs = [inp for card, orient in drawn
              if (inp := card_input(card, orient)) is not None]
    if not inputs:
        return False
    first_batch = True
    for batch in _chunk(inputs, _MEDIA_GROUP_LIMIT):
        media = []
        for i, inp in enumerate(batch):
            if first_batch and i == 0 and caption:
                media.append(InputMediaPhoto(
                    media=inp, caption=caption, parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(media=inp))
        await message.answer_media_group(media)
        first_batch = False
    return True
