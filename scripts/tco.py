"""TCO-калькулятор: локаль против облака против гибрида, точка break-even.

    python -m scripts.tco --hardware-cost 150000 --power-w 450 --kwh-price 6 \
        --amort-years 3 --dialogs-per-month 50000
    python -m scripts.tco --rent-monthly 25000 --dialogs-per-month 50000
    python -m scripts.tco --from-llm-calls        # фактические средние из llm_calls

Выход — стоимость диалога на трёх конфигурациях и объём диалогов в
месяц, после которого локаль дешевле облака (break-even). Числа — из
ваших замеров и llm_calls, не из брошюр.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOURS_PER_MONTH = 24 * 30
DEFAULT_TOKENS_PER_TURN = 900  # in+out на вызов, калибруется по llm_calls
DEFAULT_CALLS_PER_DIALOG = 4


def _avg_from_llm_calls() -> tuple[float, int]:
    """(токенов на вызов, вызовов на диалог) по факту llm_calls."""
    from core import db

    row = db.fetchone(
        "select avg(input_tokens + output_tokens) as tok, count(*) as calls from llm_calls"
    )
    tok = float(row["tok"]) if row and row["tok"] else DEFAULT_TOKENS_PER_TURN
    dialogs = db.fetchone("select count(distinct trace_id) as n from llm_calls where trace_id <> ''")
    calls = DEFAULT_CALLS_PER_DIALOG
    if dialogs and dialogs["n"]:
        calls = max(1.0, float(row["calls"]) / float(dialogs["n"]))
    return tok, round(calls)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hardware-cost", type=float, default=150000,
                        help="разовая покупка железа, ₽ (или --rent-monthly)")
    parser.add_argument("--rent-monthly", type=float, default=0,
                        help="аренда GPU-сервера в месяц, ₽")
    parser.add_argument("--power-w", type=float, default=450, help="потребление стенда, Вт")
    parser.add_argument("--kwh-price", type=float, default=6.0, help="₽ за кВт·ч")
    parser.add_argument("--amort-years", type=float, default=3, help="амортизация железа, лет")
    parser.add_argument("--dialogs-per-month", type=float, default=50000)
    parser.add_argument("--tokens-per-call", type=float, default=0, help="in+out, шт; 0 — из llm_calls")
    parser.add_argument("--calls-per-dialog", type=float, default=0, help="0 — из llm_calls")
    parser.add_argument("--from-llm-calls", action="store_true", help="средние из llm_calls")
    parser.add_argument("--out", default="", help="путь для markdown (по умолчанию stdout)")
    args = parser.parse_args()

    # TODO(экономика): посчитайте стоимость трёх конфигураций и break-even.
    # Токены на диалог = tokens_per_call × calls_per_dialog; без флагов средние
    # берутся из llm_calls через _avg_from_llm_calls().
    # Облако: диалоги × токены / 1e6 × смешанная цена, где смешанная цена
    # = 0.8 × LLM_CLOUD_PRICE_IN_RUB_PER_1M + 0.2 × LLM_CLOUD_PRICE_OUT_RUB_PER_1M
    # (по умолчанию 60 и 180).
    # Локаль: фиксированные в месяц (железо / (amort_years × 12) или аренда,
    # плюс power_w / 1000 × HOURS_PER_MONTH × kwh_price) + диалоги × токены / 1e6
    # × LLM_LOCAL_RUB_PER_1M (по умолчанию 2).
    # Гибрид: доли маршрутов из гибрида.
    # Выведите markdown-таблицу «фиксированные / переменные / итого / ₽ за диалог»
    # и строку break-even, а с --out запишите её в файл. Суммы печатайте целыми
    # рублями без разделителей разрядов (3940, а не 3 940): автопроверка ищет числа
    # в выводе по подстроке.
    raise NotImplementedError("TODO(экономика)")

if __name__ == "__main__":
    main()
