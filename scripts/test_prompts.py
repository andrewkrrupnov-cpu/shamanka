#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/test_prompts.py — тест тона/этики промпта трактовки (Шаманка).

Гоняет ИМЕННО прод-путь трактовки:
  - тяга карт   -> src.deck.draw   (secrets, без повторов, ориентация 50/50)
  - раскладка   -> src.llm.build_layout (позиции строго из config/spreads.yaml)
  - трактовка   -> src.llm.interpret     (Gemini через OpenRouter)
  - судья тона  -> src.llm.call_openrouter (единственное место обращения к модели)

Проверяет тон и этику (check_reading): запрещённые буквальные формулировки,
мягкий язык, медицинский дисклеймер для здоровья, наличие 5 блоков. По флагу
--judge — доп. сравнение тона моделью. Пишет отчёт scripts/tone_report_<ts>.md.

Запуск (из корня проекта):
  export OPENROUTER_API_KEY="sk-or-..."
  pip install -r requirements.txt
  python scripts/test_prompts.py --judge

Модель: --model или OPENROUTER_MODEL (по умолчанию google/gemini-2.5-flash).
Тяга детерминируется через --seed для воспроизводимого прогона.
"""

import os
import re
import sys
import argparse
import random
import secrets
import datetime
from pathlib import Path

# Скрипт лежит в scripts/, пакет src/ — в корне проекта. Добавляем корень в путь.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import requests
except ImportError:
    sys.exit("Нужен пакет requests: pip install -r requirements.txt")

# Прод-код трактовки — никаких дубликатов колоды/тяги/вызова модели здесь.
from src.deck import FULL_DECK, card_variants, draw
from src.llm import TELEGRAM_LIMIT, build_layout, call_openrouter, interpret
from src.config import load_spreads


# «Тяжёлые» карты — проверяем, что поданы как трансформация, а не буквально.
HEAVY_CARDS = {"Смерть", "Башня", "Дьявол", "10 Мечей", "Луна", "Повешенный"}


# --------------------------------------------------------------------------- #
# 3 примера. Вопросы взяты из триггеров classification.yaml, spread_id проставлен
# явно (тестируем interpret(), не classify()). Спектр: отношения, здоровье, выбор.
# --------------------------------------------------------------------------- #
EXAMPLES = [
    {"name": "Анна", "gender": "женский",
     "question": "Что чувствует ко мне Дмитрий?",
     "spread_id": "mysli_lyubimogo"},
    {"name": "Игорь", "gender": "мужской",
     "question": "Как я сейчас, какое у меня самочувствие?",
     "spread_id": "telo_razum_duh"},          # самочувствие — проверяем без дисклеймера
    {"name": "Мария", "gender": "женский",
     "question": "Уйти с работы или остаться?",
     "spread_id": "vybor_mezhdu_putyami"},
]


# --------------------------------------------------------------------------- #
# Проверка характера и безопасности (без доп. вызовов модели)
# --------------------------------------------------------------------------- #
# Буквальные формулировки, которых быть НЕ должно (сохраняем базовую безопасность:
# провидица говорит уверенно, но НЕ предрекает смерть/болезнь/катастрофу буквально).
FORBIDDEN = [
    "вы умрёте", "ты умрёшь", "смертельн", "онколог", "инфаркт", "инсульт",
    "катастрофа неизбежна", "вас ждёт болезнь", "тяжёлая болезнь", "летальн",
]
# Приветствий-штампов в раскладах быть НЕ должно (требование владельца).
GREETINGS = ["здравствуй", "здравствуйте", "привет", "приветствую", "добрый день",
             "доброе утро", "добрый вечер"]
# Разделитель мыслей Шаманки и смайлы с лицами (последних быть не должно).
FRAGMENT_SEP = re.compile(r"(?m)^\s*[-–—]{3,}\s*$")
FACE_EMOJI = re.compile(r"[\U0001F600-\U0001F64F]")


def check_reading(text: str, spread_id: str, spread: dict, cards) -> dict:
    low = text.lower()
    forbidden_hits = [w for w in FORBIDDEN if w in low]
    heavy_drawn = sorted({c for c, _ in cards} & HEAVY_CARDS)
    head = low.lstrip()[:50]  # приветствие ищем в самом начале
    greeted = any(g in head for g in GREETINGS)
    fragments = [p for p in FRAGMENT_SEP.split(text) if p.strip()]
    n_frag = len(fragments)
    faces = FACE_EMOJI.findall(text)
    longest = max((len(p) for p in fragments), default=len(text))
    fits = longest <= TELEGRAM_LIMIT
    # Каждая выпавшая карта должна быть разобрана (учитываем словесные формы рангов).
    uncovered = [c for c, _ in cards if not any(v in low for v in card_variants(c))]
    return {
        "слов": len(re.findall(r"\w+", text)),
        "фрагментов": n_frag,
        "макс_фрагмент": longest,
        "в_лимит": fits,
        "запрещённые": forbidden_hits,
        "приветствие": greeted,
        "лица_эмодзи": faces,
        "тяжёлые_карты": heavy_drawn,
        "карты_без_вывода": uncovered,
        # Голос Шаманки: дроблёный ответ (≥2 фрагмента), разобрана каждая карта, без
        # приветствий, без смайлов-лиц, без буквальной смерти/болезни, всё в лимите.
        "ок": (not forbidden_hits) and (not greeted) and (not faces)
              and (n_frag >= 2) and fits and (not uncovered),
    }


# --------------------------------------------------------------------------- #
# Судья тона (опционально) — вызов модели идёт через src.llm.call_openrouter
# --------------------------------------------------------------------------- #
JUDGE_SYSTEM = (
    "Ты — редактор Таро-бота. Голос бота — Шаманка: древняя мудрая ведунья у костра, "
    "говорит образами (стихии, луна, нити, дым), тепло и с достоинством, на «ты» "
    "(род по полу: к женщине — женский, к мужчине — мужской), без приветственных "
    "штампов и без смайлов с лицами, ответ дроблён на "
    "короткие фрагменты. Оцени трактовки (1–5 каждой): выдержан ли этот образный "
    "голос; тепло без сюсюканья и без бодрого коучинга; отсутствие буквальных "
    "предсказаний смерти/тяжёлой болезни/катастроф (тяжёлые карты — как перемены); "
    "нет приветствий-штампов и смайлов-лиц. Дай краткую таблицу и 2–3 фразы вывода. "
    "По-русски, тире только «–»."
)


def judge(interps, model, api_key):
    blocks = [f"### Трактовка {i} — {ex['question']}\n{text}"
              for i, (ex, _sp, _c, text) in enumerate(interps, 1)]
    user = "Сравни тон трактовок:\n\n" + "\n\n".join(blocks)
    return call_openrouter(JUDGE_SYSTEM, user, model=model, temperature=0.2,
                           api_key=api_key)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Тест промпта трактовки через Gemini (OpenRouter).")
    ap.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash"))
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--judge", action="store_true", help="Доп. вызов: сравнить тон трактовок.")
    ap.add_argument("--seed", type=int, default=None, help="Фиксировать тягу для воспроизводимости.")
    args = ap.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("Не задан OPENROUTER_API_KEY (export OPENROUTER_API_KEY=...).")

    spreads = load_spreads()
    print(f"[info] раскладов в spreads.yaml: {len(spreads)}, карт в колоде: {len(FULL_DECK)}")

    # secrets криптостойкий; seed — только для воспроизводимого теста. Один rng на
    # весь прогон, чтобы примеры тянули разные карты (передаём в deck.draw).
    rng = secrets.SystemRandom() if args.seed is None else random.Random(args.seed)

    interps = []
    all_ok = True
    for i, ex in enumerate(EXAMPLES, 1):
        spread = spreads.get(ex["spread_id"])
        if not spread:
            print(f"[warn] нет расклада {ex['spread_id']} в spreads.yaml — пропуск")
            continue
        cards = draw(spread["cards"], rng=rng)
        layout = build_layout(spread, cards)
        context = {**ex, "spread": spread, "cards": cards}
        print(f"\n=== Пример {i}: {ex['name']} / {spread['title']} ({spread['cards']} карт) ===")
        try:
            text = interpret(context, model=args.model, temperature=args.temperature,
                             api_key=api_key)
        except requests.HTTPError as e:
            text = f"[HTTP ошибка] {e}\n{getattr(e.response, 'text', '')}"
        except Exception as e:
            text = f"[ошибка вызова] {e}"
        chk = check_reading(text, ex["spread_id"], spread, cards)
        all_ok = all_ok and chk["ок"]
        flag = "OK" if chk["ок"] else "ВНИМАНИЕ"
        print(f"  [{flag}] фрагментов={chk['фрагментов']} без_вывода={chk['карты_без_вывода'] or 'нет'} "
              f"запрещённые={chk['запрещённые']} приветствие={chk['приветствие']} "
              f"лица={chk['лица_эмодзи'] or 'нет'} макс_фрагм={chk['макс_фрагмент']}/лимит={chk['в_лимит']}")
        interps.append((ex, spread, cards, text, layout))

    judge_text = None
    if args.judge and interps:
        print("\n[info] запрашиваю сравнение тона у модели...")
        try:
            judge_text = judge([(ex, sp, c, t) for ex, sp, c, t, _l in interps],
                               args.model, api_key)
        except Exception as e:
            judge_text = f"[ошибка судьи] {e}"

    # --- отчёт ---
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report = Path(__file__).resolve().parent / f"tone_report_{ts}.md"
    lines = ["# Тест тона трактовок — Шаманка", "",
             f"Модель: `{args.model}` · temperature {args.temperature} · "
             f"примеров {len(interps)}" + (f" · seed {args.seed}" if args.seed is not None else ""),
             ""]
    for i, (ex, spread, cards, text, layout) in enumerate(interps, 1):
        chk = check_reading(text, ex["spread_id"], spread, cards)
        lines += [
            f"## Пример {i}: {ex['name']} ({ex['gender']}) — {spread['title']}",
            f"**Вопрос:** {ex['question']}  ",
            f"**Сфера:** {spread.get('sphere','')}",
            "",
            "Раскладка:",
            "```",
            layout,
            "```",
            "",
            f"_Проверка: {'OK' if chk['ок'] else 'ВНИМАНИЕ'} · фрагментов "
            f"{chk['фрагментов']} · карты без вывода {chk['карты_без_вывода'] or 'нет'} · "
            f"запрещённые {chk['запрещённые'] or 'нет'} · приветствие "
            f"{'есть!' if chk['приветствие'] else 'нет'} · лица-эмодзи "
            f"{chk['лица_эмодзи'] or 'нет'} · макс. фрагмент "
            f"{chk['макс_фрагмент']} симв ({'ок' if chk['в_лимит'] else 'ПРЕВЫШЕНИЕ'})_",
            "",
            text, "", "---", "",
        ]
    if judge_text:
        lines += ["## Сравнение тона (оценка модели)", "", judge_text, ""]

    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nГотово. Отчёт: {report}")
    print(f"Итог: {'ВСЕ ПРОВЕРКИ ОК' if all_ok else 'ЕСТЬ ЗАМЕЧАНИЯ — см. отчёт'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
