"""Гейт батареи: метрики прогона против порогов из thresholds.json.

    python -m evals.gate                    # сверить runs/last
    python -m evals.gate --run <путь>       # сверить конкретный прогон

Порог — договорённость команды: изменение thresholds.json ревьюится
как код. Гейт молчит про стиль и формулировки — только проверяемые
исходы: outcome, инструменты, схема, отказы.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

THRESHOLDS_FILE = ROOT / "evals" / "thresholds.json"

# Сравнимые доли: ключ метрики → подпись для отчёта
RATIO_KEYS = {
    "outcome_pass": "доля сценариев с верным исходом",
    "tool_hit_rate": "попадание в ожидаемые инструменты",
    "schema_validity": "валидность вызовов (без ошибок контрактов)",
    "refusal_accuracy": "отказ на атаках",
}


def load_thresholds() -> dict:
    return json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))


def check_thresholds(run_dir: Path) -> dict:
    """Сверить metrics.json прогона с порогами → verdict: pass | fail."""
    metrics = json.loads((Path(run_dir) / "metrics.json").read_text(encoding="utf-8"))["metrics"]
    thresholds = load_thresholds()
    lines: list[str] = []
    verdict = "pass"
    for key, caption in RATIO_KEYS.items():
        rule = thresholds.get(key, {})
        minimum = float(rule.get("min_ratio", 0))
        actual = float(metrics.get(key, 0))
        ok = actual >= minimum
        mark = "ok " if ok else "FAIL"
        line = f"{mark} {key}: {actual} >= {minimum} — {caption}"
        if key == "outcome_pass" and metrics.get("outcome_count"):
            line += f" ({metrics['outcome_count']})"
        lines.append(line)
        if not ok:
            verdict = "fail"
    baseline = thresholds.get("baseline_run", "")
    if baseline:
        lines.append(f"baseline: {baseline}")
    return {"verdict": verdict, "lines": lines, "metrics": metrics}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=str(ROOT / "runs" / "last"))
    args = parser.parse_args()
    run_dir = Path(args.run)
    if not (run_dir / "metrics.json").exists():
        print(f"нет metrics.json в {run_dir} — сначала прогон: python -m evals.run_evals")
        raise SystemExit(1)
    verdict = check_thresholds(run_dir)
    print(f"гейт [{run_dir.name}]: {verdict['verdict']}")
    for line in verdict["lines"]:
        print(f"  {line}")
    raise SystemExit(0 if verdict["verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()
