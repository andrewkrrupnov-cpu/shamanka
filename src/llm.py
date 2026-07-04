"""Адаптер к LLM (OpenRouter, модель Gemini).

ВЕСЬ код обращения к модели живёт здесь. На этом этапе — только каркас:
функции объявлены, но реализация появится в следующих диалогах
(классификатор + промпты трактовок). Бот пока онбордит и не зовёт LLM.
"""
from __future__ import annotations


async def classify(question: str) -> str:
    """question -> spread_id (по config/classification.yaml). Пока не реализовано."""
    raise NotImplementedError("classify() будет реализован в диалоге 3")


async def interpret(context: dict) -> str:
    """context -> текст трактовки. Пока не реализовано."""
    raise NotImplementedError("interpret() будет реализован в диалоге 5")
