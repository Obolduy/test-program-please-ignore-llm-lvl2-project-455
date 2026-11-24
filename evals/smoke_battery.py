"""Мини-батарея после подъёма: 3 сценария, ~минута.

    python -m evals.smoke_battery

«Стенд поднялся» ≠ «модель работает»: этот прогон проверяет, что
поведение совпадает с выпущенной версией — чтение отвечает, действие
уходит под подтверждение, атака отсекается. Зелёный smoke-батареи —
условие присоединения провайдера к работе (выпуск).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import runnerlib as rl  # noqa: E402
from evals.run_evals import load_scenarios, run_scenario  # noqa: E402

SMOKE_SET = {"read_balance", "action_block_card", "refuse_foreign"}


async def main_async() -> int:
    from inference import setup_pool

    setup_pool()
    scenarios = [s for s in load_scenarios() if s["id"] in SMOKE_SET]
    failed = 0
    async with rl.client() as c:
        for scenario in scenarios:
            r = await run_scenario(c, scenario)
            mark = "ok " if r["ok"] else "FAIL"
            print(f"[{mark}] {r['id']}: {r['outcome']}"
                  + ("" if r["ok"] else " ← " + "; ".join(r["failures"])[:140]))
            failed += 0 if r["ok"] else 1
    print(f"\nsmoke-батарея: {len(scenarios) - failed}/{len(scenarios)} зелёные")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
