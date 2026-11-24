"""Отчёт по рантайму: llm_calls за период — латентность, токены, стоимость.

    python -m scripts.metrics_report                  # за последние сутки
    python -m scripts.metrics_report --hours 168      # за неделю
    python -m scripts.metrics_report --server http://127.0.0.1:8081
                                      # + живые счётчики llama-server

Стоимость: облако — цены из .env (LLM_CLOUD_PRICE_IN/OUT), локаль —
оценка владения (LLM_LOCAL_RUB_PER_1M, электричество+амортизация).
Это отчёт по факту: числа берутся из llm_calls, не из модели.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402


def _percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    return ordered[int(p / 100 * (len(ordered) - 1))] if ordered else 0.0


def report(hours: int) -> None:
    # TODO(экономика): агрегаты llm_calls за последние hours часов по провайдеру и модели.
    # Вызовы, суммы input_tokens и output_tokens, p50 и p95 latency_ms через
    # _percentile, стоимость по ценам LLM_CLOUD_PRICE_IN/OUT_RUB_PER_1M для облака
    # и LLM_LOCAL_RUB_PER_1M для локали.
    raise NotImplementedError("TODO(экономика)")


def server_metrics(base_url: str) -> None:
    """Живые счётчики llama-server (/metrics, prometheus-текст); иначе skip."""
    try:
        import httpx

        with httpx.Client(trust_env=False, timeout=5) as client:
            r = client.get(f"{base_url.rstrip('/')}/metrics")
        if r.status_code != 200:
            print(f"\n/metrics недоступен на {base_url} (стек без prometheus-эндпоинта) — skip")
            return
    except Exception:  # noqa: BLE001
        print(f"\n/metrics недоступен на {base_url} — skip")
        return
    interesting = {}
    for line in r.text.splitlines():
        for key in ("llamacpp:prompt_tokens_total", "llamacpp:prediction_tokens_total",
                    "llamacpp:prompt_tokens_seconds_total", "llamacpp:prediction_seconds_total"):
            if line.startswith(key):
                interesting[key.split(":")[1].replace("_total", " total")] = line.split()[-1]
    print(f"\nживые счётчики llama-server ({base_url}):")
    for key, value in interesting.items():
        print(f"  {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--server", default="", help="http://127.0.0.1:8081 — добавить /metrics")
    args = parser.parse_args()
    report(args.hours)
    if args.server:
        server_metrics(args.server)


if __name__ == "__main__":
    main()
