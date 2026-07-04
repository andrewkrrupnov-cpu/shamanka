#!/usr/bin/env python3
"""Генератор ЗАГЛУШЕЧНЫХ картинок карт в assets/cards/ (00.png … 77.png).

Нужен, чтобы поток отправки альбома работал и его было видно, пока владелец не
зальёт настоящую колоду. Настоящие изображения просто кладутся в assets/cards/
с теми же именами (индекс карты в FULL_DECK, см. src/cards.py) — код их подхватит.

Запуск из корня проекта:
    pip install pillow
    python scripts/gen_placeholder_cards.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from src.deck import FULL_DECK, MAJOR_ARCANA  # noqa: E402

OUT = ROOT / "assets" / "cards"
W, H = 400, 640


def wrap(text: str, max_chars: int = 14) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if len(cur) + len(word) + 1 <= max_chars:
            cur = f"{cur} {word}".strip()
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, card in enumerate(FULL_DECK):
        is_major = card in MAJOR_ARCANA
        bg = (58, 42, 92) if is_major else (32, 46, 70)
        img = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(img)
        d.rectangle([12, 12, W - 12, H - 12], outline=(210, 190, 140), width=4)
        d.text((24, 24), f"{i:02d}", fill=(210, 190, 140))
        lines = wrap(card)
        y = H // 2 - len(lines) * 16
        for line in lines:
            d.text((W // 2 - len(line) * 9, y), line, fill=(238, 232, 214))
            y += 34
        tag = "Старший аркан" if is_major else "Младший аркан"
        d.text((24, H - 44), tag, fill=(170, 160, 190))
        img.save(OUT / f"{i:02d}.png")
    print(f"Готово: {len(FULL_DECK)} заглушек в {OUT}")


if __name__ == "__main__":
    main()
