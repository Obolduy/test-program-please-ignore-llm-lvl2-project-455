"""Диалоговые сценарии на живой LLM: матрица успех/отказ/атака/лимит.

    python scripts/run_scenarios.py            # все сценарии
    python scripts/run_scenarios.py 30 80      # по префиксам имён

Сценарий — YAML в data/dialogs: пользователь, реплики и ожидания. Раннер
открывает сессию backend, гонит реплики по SSE, собирает события и
проверяет ожидания; состояние моков (статус карт, лимиты, контакты,
тикет-очередь) сверяется после хода. Перед каждым сценарием моки
сбрасываются.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402

DIALOGS = ROOT / "data" / "dialogs"
RESULTS: list[tuple[str, bool, str]] = []


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, timeout=300)


async def chat_turn(c: httpx.AsyncClient, session_id: str, message: str) -> list[dict]:
    """Один ход по SSE → список событий."""
    events: list[dict] = []
    async with c.stream(
        "POST", f"{config.BACKEND_URL}/api/chat",
        json={"session_id": session_id, "message": message},
    ) as r:
        buf = ""
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                line = next((l for l in raw.split("\n") if l.startswith("data: ")), None)
                if line:
                    import json

                    events.append(json.loads(line[6:]))
    return [e for e in events if e.get("type") != "done"]


def _get_text(events: list[dict]) -> str:
    replies = [e.get("text", "") for e in events if e.get("type") == "reply"]
    return "\n".join(replies)


async def check_expectations(c: httpx.AsyncClient, scenario_user: str, turn: dict,
                             turn_events: list[dict]) -> tuple[bool, str]:
    """Ожидания реплики — по событиям этого хода; состояние — по мокам сейчас.

    state без sub проверяется на sub пользователя сценария: объект — его.
    """
    expect = turn.get("expect", {}) or {}
    user = config.USERS.get(scenario_user, None)
    default_sub = user.sub if user else "u_4f2a"
    plan_events = [e for e in turn_events if e.get("type") == "plan"]
    cards = [e for e in turn_events if e.get("type") == "card"]
    text = _get_text(turn_events)

    for token in expect.get("plan_has", []) or []:
        if not any(token in a for e in plan_events for a in e.get("agents", [])):
            return False, f"в плане нет «{token}»"
    if "card" in expect:
        if expect["card"] and not cards:
            return False, "ожидалась карточка — не пришла"
        if not expect["card"] and cards:
            return False, "карточка не ожидалась, но пришла"
    for needle in expect.get("reply_contains", []) or []:
        if needle.lower() not in text.lower():
            return False, f"в ответе нет «{needle}» (ответ: {text[:120]})"
    for needle in expect.get("reply_not_contains", []) or []:
        if needle.lower() in text.lower():
            return False, f"в ответе лишнее «{needle}»"

    state = expect.get("state", {}) or {}
    if "card_status" in state:
        want = state["card_status"]
        sub = state.get("sub", default_sub)
        r = await c.get(f"{config.STAND_URL}/mock/cards?sub={sub}")
        card = next((k for k in r.json().get("cards", []) if str(k["mask"]) == str(want["mask"])), None)
        actual = card["status"] if card else "not_found"
        if actual != want["status"]:
            return False, f"карта •{want['mask']}: {actual}, ожидалось {want['status']}"
    if "limit" in state:
        want = state["limit"]
        sub = state.get("sub", default_sub)
        r = await c.get(f"{config.STAND_URL}/mock/limits?sub={sub}")
        row = next((l for l in r.json().get("limits", []) if str(l["mask"]) == str(want["mask"])), None)
        actual = row[f"{want['period']}_limit"] if row else None
        if float(actual or 0) != float(want["amount"]):
            return False, (f"лимит {want['period']} •{want['mask']}: {actual}, "
                           f"ожидалось {want['amount']}")
    if "contacts" in state:
        want = state["contacts"]
        sub = state.get("sub", default_sub)
        r = await c.get(f"{config.STAND_URL}/mock/clients?sub={sub}")
        body = r.json()
        for key in ("phone", "email"):
            if key in want and body.get(key) != want[key]:
                return False, f"контакт {key}: {body.get(key)}, ожидалось {want[key]}"
    if "tickets_min" in state:
        sub = state.get("sub", default_sub)
        r = await c.get(f"{config.STAND_URL}/mock/tickets?sub={sub}")
        if len(r.json().get("tickets", [])) < state["tickets_min"]:
            return False, f"тикетов меньше {state['tickets_min']}"
    provider = turn.get("expect", {}).get("provider")
    if provider:
        trace_id = next((e.get("trace_id", "") for e in turn_events if e.get("trace_id")), "")
        if not trace_id:
            return False, "нет trace_id в событиях хода"
        from core import db

        rows = db.fetchall("select distinct provider from llm_calls where trace_id = %s", (trace_id,))
        served = {r["provider"] for r in rows}
        if provider not in served:
            return False, f"ход обслужен {sorted(served)}, ожидался {provider}"
    return True, ""


async def run_scenario(c: httpx.AsyncClient, path: Path) -> None:
    scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
    name = scenario.get("name", path.stem)
    await c.post(f"{config.STAND_URL}/mock/admin/reset")

    r = await c.post(f"{config.BACKEND_URL}/api/session", json={"user": scenario["user"]})
    session_id = r.json()["session_id"]

    all_events: list[dict] = []
    for turn in scenario["turns"]:
        events = await chat_turn(c, session_id, turn["say"])
        all_events.extend(events)
        ok, why = await check_expectations(c, scenario["user"], turn, events)
        if not ok:
            RESULTS.append((name, False, f"реплика «{turn['say'][:40]}»: {why}"))
            return

    RESULTS.append((name, True, ""))


async def main() -> int:
    wanted = sys.argv[1:]
    files = sorted(DIALOGS.glob("*.yaml"))
    async with client() as c:
        for path in files:
            if wanted and not any(path.stem.startswith(w) for w in wanted):
                continue
            try:
                await run_scenario(c, path)
            except Exception as e:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                RESULTS.append((path.stem, False, repr(e)[:160]))

    print("\n──── матрица сценариев ────")
    for name, ok, why in RESULTS:
        print(f"[{'ok ' if ok else 'FAIL'}] {name} {why}")
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nитого: {len(RESULTS) - len(failed)}/{len(RESULTS)} зелёные")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
