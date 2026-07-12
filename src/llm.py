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


def _proxies() -> dict | None:
    """Прокси для запросов к OpenRouter (env OPENROUTER_PROXY).

    Нужен, если Cloudflare OpenRouter блокирует IP сервера (напр. РФ-VPS отдаёт
    403 «Access denied by security policy»). Поддерживает http(s):// и socks5://.
    Пусто — ходим напрямую.
    """
    p = os.getenv("OPENROUTER_PROXY", "").strip()
    return {"http": p, "https": p} if p else None


PROMPT_MARKER = re.compile(r"(?m)^=====\s*USER\s*=====\s*$")

# Медицинские/консультационные дисклеймеры убраны по решению владельца — Саира
# говорит от себя, без оговорок. Множество оставлено пустым (и как совместимость
# с тестами): health_note никогда не подставляется.
HEALTH_DISCLAIMER_SPREADS: set[str] = set()

HEALTH_NOTE = ""

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
                    timeout: int = 120, retries: int = 2,
                    max_tokens: int | None = None) -> str:
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
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    for attempt in range(retries + 1):
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload,
                              timeout=timeout, proxies=_proxies())
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


# Лимит одного сообщения Telegram — на него ориентируется разбивка в reading.py.
TELEGRAM_LIMIT = 4096
# Ответ Шаманки дробится на 4–7 фрагментов-сообщений, поэтому объём можно давать
# щедрее (примерно вдвое против прежнего) — больше конкретики по картам.
INTERPRET_MAX_TOKENS = 2400


def interpret(context: dict, *, model: str | None = None,
              temperature: float = 0.9, api_key: str | None = None) -> str:
    """По (вопрос, расклад, уже вытянутые карты) вернуть трактовку голосом Шаманки.

    context = {name, gender, question, spread_id, spread (dict из spreads.yaml),
    cards (список пар из deck.draw)}. Карты уже вытянуты — здесь не тянем и в БД
    не пишем. Позиции берутся из spread['positions'] через build_layout().

    Возвращает текст, разбитый на фрагменты строками `---` (одна мысль = одно
    сообщение). Разбивку на сообщения делает reading.py. Температура выше — для
    более живого, образного языка.
    """
    system, user_tpl = load_prompt()
    layout = build_layout(context["spread"], context["cards"])
    user_msg = _render_user(user_tpl, context, layout)
    return call_openrouter(system, user_msg, model=model,
                           temperature=temperature, api_key=api_key,
                           max_tokens=INTERPRET_MAX_TOKENS)


# classify() (вопрос -> spread_id) намеренно живёт в src/classifier.py, а не здесь:
# llm.py — только транспорт. classifier.py вызывает openrouter_chat() ниже.


def openrouter_chat(
    messages: list[dict],
    *,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 512,
    response_format: dict | None = None,
    timeout: int = 60,
) -> str:
    """Низкоуровневый транспорт: один вызов chat-completions в OpenRouter.

    messages         — список {"role": ..., "content": ...} в формате OpenAI.
    model            — слаг модели, напр. "google/gemini-2.5-flash-lite".
    response_format  — напр. {"type": "json_object"} для структурированного вывода.
                       Не все провайдеры его принимают — вызывающий код должен быть
                       готов к обычному тексту (см. classifier._extract_id).
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY не задан. Положи ключ в .env "
            "(строка OPENROUTER_API_KEY=sk-or-...) и загрузи через python-dotenv."
        )

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if os.getenv("OPENROUTER_REFERER"):
        headers["HTTP-Referer"] = os.environ["OPENROUTER_REFERER"]
    if os.getenv("OPENROUTER_TITLE"):
        headers["X-Title"] = os.environ["OPENROUTER_TITLE"]

    resp = requests.post(OPENROUTER_URL, json=payload, headers=headers,
                         timeout=timeout, proxies=_proxies())
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
#  Карта дня
# --------------------------------------------------------------------------- #
_DAILY_SYSTEM = (
    "Ты — Шаманка, древняя мудрая ведунья у костра. Человеку выпала «карта дня». "
    "Дай короткое предсказание-настрой на СЕГОДНЯ по этой карте: 2–4 предложения, "
    "тёплым образным, но ЯСНЫМ голосом (стихии, огонь, нити, дым — в меру). Прямо "
    "скажи, на что настроиться, чего беречься или что впустить сегодня. Обращайся "
    "на «ты», согласуй род по полю «Пол». Учитывай положение карты (прямая — сила "
    "открыта; перевёрнутая — скрыта, зреет). НЕ здоровайся, без смайлов с лицами, "
    "тире только «–». Не пиши заголовок и не называй карту повторно — только само "
    "предсказание."
)


def daily_card(card: str, orient: str, *, name: str, gender: str,
               model: str | None = None, api_key: str | None = None) -> str:
    """Короткое предсказание на день по одной карте, голосом Шаманки."""
    user = (f"Имя: {name}\nПол: {gender}\nКарта дня: {card} ({orient})\n"
            "Дай короткое предсказание-настрой на сегодня.")
    return call_openrouter(_DAILY_SYSTEM, user, model=model, temperature=0.9,
                           api_key=api_key, max_tokens=400)
