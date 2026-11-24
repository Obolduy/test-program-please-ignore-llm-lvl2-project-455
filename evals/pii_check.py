"""Проверка PII-анонимизатора на кейсах: точность и полнота.

    python -m evals.pii_check

Полнота — доля кейсов, где всё must_mask исчезло; точность — доля
кейсов, где must_keep уцелел и неперсональный текст не искажён.
Проверка зелёная, только когда обе доли равны 1: утечка одного телефона
уже повод не выпускать анонимизатор.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inference.anonymizer import mask_text  # noqa: E402

CASES_FILE = ROOT / "evals" / "pii_cases.json"


def main() -> int:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    recall_hits, precision_hits = 0, 0
    for case in cases:
        masked = mask_text(case["text"])
        leaked = [s for s in case["must_mask"] if s in masked]
        lost = [s for s in case["must_keep"] if s not in masked]
        ok_recall = not leaked
        ok_precision = not lost
        recall_hits += ok_recall
        precision_hits += ok_precision
        mark = "ok " if ok_recall and ok_precision else "FAIL"
        print(f"[{mark}] {case['id']}: {masked[:70]}"
              + (f" | утекло: {leaked}" if leaked else "")
              + (f" | искажено: {lost}" if lost else ""))
    n = len(cases) or 1
    print(f"\npii: полнота {recall_hits}/{n}, точность {precision_hits}/{n}")
    return 0 if recall_hits == len(cases) and precision_hits == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
