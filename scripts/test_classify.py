"""
scripts/test_classify.py — прогоняет пачку тестовых вопросов через
классификатор (Gemini via OpenRouter) и показывает, куда попал каждый.

Запуск из корня проекта:
    python scripts/test_classify.py
    python scripts/test_classify.py --limit 10          # только первые N
    python scripts/test_classify.py --model google/gemini-2.5-flash

Требует OPENROUTER_API_KEY в .env (или в окружении).

Набор из 30 вопросов сфокусирован на СПОРНЫХ случаях — там, где приоритеты
правил конфликтуют (формат «да/нет» против имени+чувства, выбор против
да/нет, родители в семью против психологии, выгорание, луна, бизнес и т.д.).
Колонка «ожид.» — это предполагаемая правильная маршрутизация; несовпадение
не обязательно баг классификатора, часто это сигнал, что правило в
classification.yaml стоит уточнить. Спорные строки помечены (!).
"""

from __future__ import annotations

import argparse
import os
import sys

# --- чтобы работал импорт src.* при запуске из любого каталога ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass  # python-dotenv не обязателен, если ключ уже в окружении

from src.classifier import classify, load_rules, _load_spread_names  # noqa: E402

# (вопрос, ожидаемый spread_id, спорный?, комментарий по спорности)
CASES: list[tuple[str, str, bool, str]] = [
    # --- P1: выбор из двух вариантов против да/нет ---
    ("Переехать в другой город или остаться здесь?", "vybor_mezhdu_putyami", True,
     "два варианта -> выбор, не да/нет"),
    ("Уйти с этой работы или остаться?", "vybor_mezhdu_putyami", True,
     "выбор перекрывает сферу карьеры"),
    ("Получу ли я эту должность?", "da_net", False, ""),
    ("Стоит ли мне брать ипотеку?", "da_net", False, ""),

    # --- P2: имя + чувство против формата да/нет ---
    ("Что чувствует ко мне Дима?", "mysli_lyubimogo", False, ""),
    ("Любит ли меня Катя?", "mysli_lyubimogo", True,
     "формат 'любит ли' похож на да/нет, но имя+чувство -> P2"),
    ("Нравлюсь ли я Олегу?", "mysli_lyubimogo", True, "имя+чувство важнее формата"),

    # --- родители: семья против психологии паттерна ---
    ("Как наладить отношения с мамой?", "vizit_k_predkam", True,
     "родитель как человек -> семья"),
    ("Почему я повторяю сценарии своих родителей?", "ten_yungianskiy", True,
     "паттерн -> психология, НЕ семья"),
    ("Мне тяжело общаться с отцом, что делать?", "vizit_k_predkam", True,
     "родитель как человек -> семья"),

    # --- выгорание: здоровье/баланс, не карьера ---
    ("Я выгорел на работе, совсем нет сил.", "balans_in_yan", True,
     "выгорание -> здоровье, НЕ карьера"),
    ("Постоянное истощение, ничего не хочу делать.", "balans_in_yan", True, "дисбаланс -> здоровье"),

    # --- луна: духовность, не планирование ---
    ("Как мне работать с энергией полнолуния?", "novolunie_polnolunie", True,
     "луна -> духовность, не время"),
    ("Что важно заложить в новолуние?", "novolunie_polnolunie", True, "луна -> духовность"),

    # --- бизнес/своё дело: мечта, не карьера ---
    ("Хочу открыть своё дело — что меня ждёт?", "rasklad_na_mechtu", True,
     "своё дело -> мечта, НЕ карьера"),
    ("Стоит ли запускать стартап?", "rasklad_na_mechtu", True,
     "тема мечты против формата 'стоит ли'"),

    # --- растерянность против выбора ---
    ("Совсем запутался, не знаю что делать.", "perekrestok", True,
     "нет двух явных вариантов -> перекрёсток, не выбор"),

    # --- отношения: узкие правила и общий fallback сферы ---
    ("Совместимы ли мы с ним?", "siren_i_klever", True,
     "тема совместимости против формата 'совместимы ли'"),
    ("Что сейчас происходит между нами в паре?", "vokzal_dlya_dvoih", False, ""),
    ("Есть ли будущее у наших отношений?", "budushee_pary", True,
     "формат против темы перспективы пары"),
    ("Мы постоянно ссоримся с партнёром.", "piramida", False, ""),
    ("Как пережить расставание?", "istselenie_serdtsa", False, ""),
    ("Когда я встречу свою любовь?", "privlechenie_lyubvi", False, ""),
    ("Расскажи про мою личную жизнь.", "lyubovnyy_treugolnik", True,
     "общий запрос сферы (P4), не должен уходить в узкое правило"),

    # --- прочие сферы для контроля ---
    ("В чём моё предназначение в этой жизни?", "proshlaya_zhizn", False, ""),
    ("Каким будет для меня этот год?", "godovoy_rasklad", False, ""),
    ("Почему у меня совсем нет денег?", "finansy", False, ""),
    ("Что делать с финансами, чтобы выправить ситуацию?", "situatsiya_deystvie_itog", True,
     "'что делать' -> конкретное действие, не общий запрос о деньгах"),
    ("Хочу разобраться в себе — кто я на самом деле?", "yakor_samopoznanie", False, ""),

    # --- fallback ---
    ("Просто расскажи, что меня ждёт.", "proshloe_nastoyashee_budushee", True,
     "слишком общий -> универсальный fallback P5"),
]


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="Прогон тестовых вопросов через классификатор.")
    parser.add_argument("--limit", type=int, default=len(CASES), help="сколько вопросов прогнать")
    parser.add_argument("--model", default=None, help="слаг модели OpenRouter (переопределяет дефолт)")
    args = parser.parse_args()

    if not os.getenv("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY не найден. Добавь ключ в .env и повтори.", file=sys.stderr)
        return 1

    # Грузим правила и имена один раз — не читаем YAML на каждый вопрос.
    try:
        rules = load_rules()
    except FileNotFoundError:
        print("Не найден config/classification.yaml — запусти из корня проекта.", file=sys.stderr)
        return 1
    spread_names = _load_spread_names()

    cases = CASES[: args.limit]
    q_w = 46  # ширина колонки вопроса
    print(f"Прогон {len(cases)} вопросов. Модель: "
          f"{args.model or os.getenv('OPENROUTER_CLASSIFIER_MODEL', 'дефолт (gemini-flash-lite)')}\n")
    print(f"{'#':>2}  {'вопрос':<{q_w}}  {'получено':<26}  {'ожид.':<26}  ре")
    print("-" * (q_w + 66))

    matches = 0
    disputed_rows: list[str] = []
    for i, (question, expected, disputed, note) in enumerate(cases, 1):
        try:
            got = classify(question, rules=rules, spread_names=spread_names, model=args.model)
        except Exception as exc:  # сеть/ключ/провайдер
            print(f"{i:>2}  {truncate(question, q_w):<{q_w}}  ОШИБКА: {exc}")
            continue

        ok = got == expected
        matches += ok
        mark = "✓ " if ok else "✗ "
        flag = "!" if disputed else " "
        print(f"{i:>2}{flag} {truncate(question, q_w):<{q_w}}  {got:<26}  {expected:<26}  {mark}")
        if disputed:
            disputed_rows.append(f"  {mark}{got:<26} <- {question}  ({note})")

    print("-" * (q_w + 66))
    print(f"Совпало с ожиданием: {matches}/{len(cases)}\n")

    if disputed_rows:
        print("Спорные случаи (как разрешились):")
        for row in disputed_rows:
            print(row)
        print("\nНесовпадения по спорным строкам — повод уточнить правило или "
              "note в classification.yaml, а не обязательно баг кода.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
