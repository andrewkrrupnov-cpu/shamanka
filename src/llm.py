"""Адаптер к LLM (OpenRouter / Gemini) — ЕДИНСТВЕННОЕ место обращения к модели.

Здесь: загрузка промпта трактовки, сборка раскладки по фиксированным позициям,
вызов модели с ретраями и interpret(). Карты приходят уже вытянутыми
(см. src/deck.py) — здесь их НЕ тянут и в БД не пишут.

classify() (вопрос -> spread_id) — задача Диалога 3, здесь только заглушка.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "interpret.md"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"
PROMPT_MARKER = re.compile(r"(?m)^=====\s*USER\s*=====\s*$")

# Расклады, где ОБЯЗАТЕЛЕН медицинский дисклеймер. Определяется по spread_id,
# а НЕ по полю sphere: telo_razum_duh ловит «самочувствие/здоровье», хотя его
# sphere — «психология» (см. classification.yaml, docs/spreads_source.md ч.2).
HEALTH_DISCLAIMER_SPREADS = {"balans_in_yan", "telo_razum_duh"}

HEALTH_NOTE = (
    "HEALTH: тема касается здоровья/самочувствия – обязателен "
    "медицинский дисклеймер в блоке 5."
)

_prompt_cache: tuple[str, str] | None = None


def load_prompt(path: Path | None = None) -> tuple[str, str]:
    """Загрузить промпт и разбить на (system, user) по строке-маркеру ===== USER =====."""
    global _prompt_cache
    if path is None and _prompt_cache is not None:
        return _prompt_cache
    p = path or PROMPT_PATH
    text = p.read_text(encoding="utf-8")
    m = PROMPT_MARKER.search(text)
    if not m:
        raise ValueError(f"В промпте нет строки-маркера '===== USER =====': {p}")
    system_part = text[:m.start()]
    user_part = text[m.end():]
    # Отрезаем markdown-шапку промпта до первой строки-разделителя '---'.
    hm = re.search(r"(?m)^---\s*$", system_part)
    if hm:
        system_part = system_part[hm.end():]
    result = (system_part.strip(), user_part.strip())
    if path is None:
        _prompt_cache = result
    return result


def build_layout(spread: dict, cards) -> str:
    """Строки 'N. позиция: Карта (положение)' строго по позициям из spreads.yaml.

    Позиции берутся ТОЛЬКО из spread['positions'] в фиксированном порядке —
    нейросеть их не меняет и не придумывает. Число карт обязано совпасть с
    числом позиций, иначе ошибка.
    """
    positions = spread["positions"]
    if len(cards) != len(positions):
        raise ValueError(f"карт {len(cards)} != позиций {len(positions)}")
    lines = []
    for i, (pos, (card, orient)) in enumerate(zip(positions, cards), 1):
        lines.append(f"{i}. {pos}: {card} ({orient})")
    return "\n".join(lines)


def _health_note(spread_id: str) -> str:
    return HEALTH_NOTE if spread_id in HEALTH_DISCLAIMER_SPREADS else ""


def _render_user(user_tpl: str, context: dict, layout: str) -> str:
    spread = context["spread"]
    values = {
        "name": context.get("name", ""),
        "gender": context.get("gender", ""),
        "question": context.get("question", ""),
        "spread_title": spread["title"],
        "spread_summary": spread["summary"],
        "sphere": spread.get("sphere", ""),
        "layout": layout,
        "health_note": _health_note(context["spread_id"]),
    }
    out = user_tpl
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def call_openrouter(system: str, user: str, *, model: str | None = None,
                    temperature: float = 0.7, api_key: str | None = None,
                    timeout: int = 120, retries: int = 2) -> str:
    """Один вызов OpenRouter chat/completions. Ретраи на 429/5xx и таймаут/сеть."""
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Не задан OPENROUTER_API_KEY (env или аргумент).")
    model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://local.test/shamanka",
        "X-Title": "shamanka-interpret",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    for attempt in range(retries + 1):
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload,
                              timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        if (r.status_code == 429 or r.status_code >= 500) and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        try:
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as e:
            raise RuntimeError(f"Не разобрал ответ OpenRouter: {e}")
    raise RuntimeError("call_openrouter: исчерпаны попытки")


def interpret(context: dict, *, model: str | None = None,
              temperature: float = 0.7, api_key: str | None = None) -> str:
    """По (вопрос, расклад, уже вытянутые карты) вернуть трактовку из 5 блоков.

    context = {name, gender, question, spread_id, spread (dict из spreads.yaml),
    cards (список пар из deck.draw)}. Карты уже вытянуты — здесь не тянем и в БД
    не пишем. Позиции берутся из spread['positions'] через build_layout().
    """
    system, user_tpl = load_prompt()
    layout = build_layout(context["spread"], context["cards"])
    user_msg = _render_user(user_tpl, context, layout)
    return call_openrouter(system, user_msg, model=model,
                           temperature=temperature, api_key=api_key)


def classify(question: str) -> str:
    """question -> spread_id. Не входит в это задание (Диалог 3)."""
    raise NotImplementedError("classify() будет реализован в диалоге 3")
