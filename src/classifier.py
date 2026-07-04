"""
src/classifier.py — классификация вопроса пользователя в один spread_id.

Как работает:
  1. Читаем config/classification.yaml — источник правил, отсортированных
     по приоритету (P1 высший … P5 fallback).
  2. Строим системный промпт: правила по порядку приоритета + механика
     «сверху вниз, первое совпадение побеждает» + пометки по спорным случаям.
  3. Отправляем вопрос в Gemini через OpenRouter (src.llm.openrouter_chat).
  4. Разбираем ответ и валидируем: spread_id обязан быть из известного набора.
     Если модель вернула мусор — откатываемся на fallback (правило с triggers=["*"]).

Важно: правила НЕ захардкожены. Они всегда берутся из YAML в рантайме,
поэтому код работает ровно с той версией classification.yaml, что лежит
в репозитории (даже если её правили после генерации).

Функция классификации LLM-овая осознанно: триггеры в правилах — это
естественно-языковые фразы и шаблоны с [имя], а спорные случаи разводятся
по смыслу. Тупое сопоставление подстрок тут не сработает; поэтому решение
о раскладе принимает лёгкая модель, а правила служат ей инструкцией.
"""

from __future__ import annotations

import json
import os
import re

import yaml

from .llm import openrouter_chat

# Дешёвая GA-модель, которой с запасом хватает на классификацию.
# Альтернативы: "google/gemini-flash-latest" (всегда свежий Flash),
# "google/gemini-2.5-flash" (умнее и дороже). Переопределяется через env.
DEFAULT_MODEL = "google/gemini-2.5-flash-lite"

CLASSIFICATION_YAML = "config/classification.yaml"
SPREADS_YAML = "config/spreads.yaml"

SYSTEM_TEMPLATE = """\
Ты — классификатор запросов для Таро-бота «Шаманка». Твоя задача: по вопросу \
пользователя выбрать РОВНО ОДИН расклад и вернуть его spread_id.

Механика (соблюдай строго):
- Правила ниже отсортированы по приоритету: P1 — высший, P5 — низший fallback.
- Проверяй сверху вниз. ПЕРВОЕ подходящее правило побеждает, ниже не смотри.
- Если в вопросе есть два явных варианта выбора (А или Б; уйти/остаться; \
переехать туда или сюда) — это ВЫБОР, а не да/нет.
- Спорные случаи разрешай строго по пометкам «ВАЖНО».
- Никогда не проси уточнений и не рассуждай вслух. Всегда ровно один расклад.
- Если ничего конкретного не подходит — верни fallback (правило P5).

Правила:
{rules_block}

Ответь СТРОГО в формате JSON без markdown и пояснений:
{{"spread_id": "<одно из: {ids}>"}}"""


# --------------------------------------------------------------------------- #
#  Загрузка правил
# --------------------------------------------------------------------------- #
def load_rules(path: str = CLASSIFICATION_YAML) -> list[dict]:
    """Читает classification.yaml и возвращает список правил, отсортированный
    по приоритету. Порядок правил ВНУТРИ одного приоритета сохраняется —
    он кодирует тонкости вроде «выбор проверяем раньше да/нет»."""
    with open(path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    if not isinstance(rules, list):
        raise ValueError(
            f"{path}: ожидался список правил верхнего уровня, получено {type(rules).__name__}"
        )
    rules.sort(key=lambda r: r.get("priority", 99))  # sort стабилен
    return rules


def _valid_ids(rules: list[dict]) -> set[str]:
    return {r["spread_id"] for r in rules if r.get("spread_id")}


def _fallback_id(rules: list[dict]) -> str:
    """Правило-fallback: с triggers == ['*'] либо последнее по приоритету."""
    for r in rules:
        trig = r.get("triggers")
        if trig in ("*", ["*"]):
            return r["spread_id"]
    return rules[-1]["spread_id"] if rules else ""


def _load_spread_names(path: str = SPREADS_YAML) -> dict[str, str]:
    """Человекочитаемые имена раскладов для промпта. Best-effort: точную
    схему spreads.yaml не знаем, пробуем разумные варианты, при отсутствии
    файла тихо возвращаем пустой словарь (промпт обойдётся id-шниками)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (FileNotFoundError, OSError):
        return {}

    names: dict[str, str] = {}
    spreads = data.get("spreads", data) if isinstance(data, dict) else data
    if isinstance(spreads, dict):
        for sid, val in spreads.items():
            names[sid] = (val.get("name") or val.get("title") or "") if isinstance(val, dict) else ""
    elif isinstance(spreads, list):
        for item in spreads:
            if isinstance(item, dict) and item.get("id"):
                names[item["id"]] = item.get("name") or item.get("title") or ""
    return names


# --------------------------------------------------------------------------- #
#  Промпт
# --------------------------------------------------------------------------- #
def build_system_prompt(rules: list[dict], spread_names: dict[str, str] | None = None) -> str:
    spread_names = spread_names or {}
    lines: list[str] = []
    for r in rules:
        pid = r.get("spread_id", "")
        name = spread_names.get(pid, "")
        header = f"[P{r.get('priority', '?')}] {pid}" + (f" — {name}" if name else "")
        lines.append(header)
        if r.get("intent"):
            lines.append(f"    когда: {r['intent']}")
        trig = r.get("triggers")
        if trig and trig not in ("*", ["*"]):
            trig_list = trig if isinstance(trig, list) else [trig]
            lines.append("    триггеры: " + "; ".join(str(t) for t in trig_list))
        if r.get("note"):
            lines.append(f"    ВАЖНО: {r['note']}")
    return SYSTEM_TEMPLATE.format(
        rules_block="\n".join(lines),
        ids=", ".join(sorted(_valid_ids(rules))),
    )


# --------------------------------------------------------------------------- #
#  Разбор ответа модели
# --------------------------------------------------------------------------- #
def _extract_id(raw: str) -> str:
    """Достаёт spread_id из ответа модели: сперва как JSON, затем запасным
    разбором первого id-подобного токена."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("spread_id"):
            return str(data["spread_id"]).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"[a-z][a-z0-9_]+", raw)
    return m.group(0) if m else ""


# --------------------------------------------------------------------------- #
#  Главная функция
# --------------------------------------------------------------------------- #
def classify(
    question: str,
    *,
    rules: list[dict] | None = None,
    spread_names: dict[str, str] | None = None,
    model: str | None = None,
) -> str:
    """Принимает текст вопроса, возвращает ровно один spread_id.

    rules / spread_names можно передать заранее загруженными (полезно, чтобы
    не читать YAML на каждый вызов — например, в тест-скрипте на 30 вопросов).
    """
    if rules is None:
        rules = load_rules()
    if spread_names is None:
        spread_names = _load_spread_names()

    system = build_system_prompt(rules, spread_names)
    model = model or os.getenv("OPENROUTER_CLASSIFIER_MODEL", DEFAULT_MODEL)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question.strip()},
    ]

    # Пытаемся получить структурированный JSON; если провайдер не принимает
    # response_format — повторяем обычным вызовом (разбор всё равно устойчив).
    try:
        raw = openrouter_chat(
            messages, model=model, temperature=0.0, max_tokens=40,
            response_format={"type": "json_object"},
        )
    except Exception:
        raw = openrouter_chat(messages, model=model, temperature=0.0, max_tokens=40)

    spread_id = _extract_id(raw)
    if spread_id not in _valid_ids(rules):
        spread_id = _fallback_id(rules)  # гарантия однозначного валидного ответа
    return spread_id


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python -m src.classifier \"текст вопроса\"")
        raise SystemExit(1)
    print(classify(" ".join(sys.argv[1:])))
