"""Дверь гейта в CI: `pytest evals/ -m gate` падает, если runs/last красный.

Сам прогон батареи здесь не выполняется — тест сверяет последний
зафиксированный прогон с thresholds.json. Прогон делает make gate
(run_evals --gate); в CI-пайплайне с живым провайдером можно совместить:

    pytest evals/ -m gate          # гейт по runs/last
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.gate import check_thresholds  # noqa: E402

pytestmark = pytest.mark.gate


def test_last_run_passes_thresholds() -> None:
    last = ROOT / "runs" / "last"
    assert (last / "metrics.json").exists(), (
        "нет runs/last — сначала прогон батареи: make eval"
    )
    verdict = check_thresholds(last)
    assert verdict["verdict"] == "pass", "\n".join(verdict["lines"])
