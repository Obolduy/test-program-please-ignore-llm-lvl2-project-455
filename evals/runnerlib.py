"""Общая механика прогонов: SSE-ход, сброс моков, чтение состояния.

Одна механика на два «экзамена»: eval-батарея (одноходовые сценарии с
фикстурами) и диалоговые сценарии data/dialogs (многоходовые живые
диалоги). Функции отсюда импортируют оба раннера — ассерты у каждого
свои.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config  # noqa: E402


def client(timeout: int = 300) -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, timeout=timeout)


async def reset(c: httpx.AsyncClient) -> None:
    await c.post(f"{config.STAND_URL}/mock/admin/reset")


async def apply_fixtures(c: httpx.AsyncClient, sub: str, fixtures: dict | None) -> dict:
    if not fixtures:
        return {}
    r = await c.post(
        f"{config.STAND_URL}/mock/admin/fixtures",
        json={"sub": sub, **fixtures},
    )
    r.raise_for_status()
    return r.json()


async def chat_turn(c: httpx.AsyncClient, session_id: str, message: str) -> list[dict]:
    """Один ход по SSE → список событий (каждое несёт trace_id хода)."""
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
                    events.append(json.loads(line[6:]))
    return [e for e in events if e.get("type") != "done"]


def reply_text(events: list[dict]) -> str:
    return "\n".join(e.get("text", "") for e in events if e.get("type") == "reply")


def trace_of(events: list[dict]) -> str:
    return next((e.get("trace_id", "") for e in events if e.get("trace_id")), "")


async def get_cards(c: httpx.AsyncClient, sub: str) -> list[dict]:
    r = await c.get(f"{config.STAND_URL}/mock/cards?sub={sub}")
    return r.json().get("cards", [])


async def get_limits(c: httpx.AsyncClient, sub: str) -> list[dict]:
    r = await c.get(f"{config.STAND_URL}/mock/limits?sub={sub}")
    return r.json().get("limits", [])


async def get_contacts(c: httpx.AsyncClient, sub: str) -> dict:
    r = await c.get(f"{config.STAND_URL}/mock/clients?sub={sub}")
    return r.json()


async def get_tickets(c: httpx.AsyncClient, sub: str) -> list[dict]:
    r = await c.get(f"{config.STAND_URL}/mock/tickets?sub={sub}")
    return r.json().get("tickets", [])


def sub_of(login: str) -> str:
    user = config.USERS.get(login)
    return user.sub if user else ""
