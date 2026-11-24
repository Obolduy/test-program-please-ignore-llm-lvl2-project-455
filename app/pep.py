"""PEP: оркестратор спрашивает PDP — сам решений о write-пути не принимает.

Три запроса к политике на каждый карточный цикл:

  prepare(card)   карточка создаётся → Prepare (Allow фиксирует карточку
                  в журнале PDP; Deny — карточки не существует)
  confirm(card)   «да» пользователя → ConfirmCard (Allow открывает окно
                  для Execute; TTL карточки проверяет PDP по wall clock)
  execute(card)   исполнение → Execute (окно formerly ConfirmCard + лимит)

Недоступность PDP — fail-closed для write-пути: без решения действие
не происходит; чтение продолжает работать.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config

TIMEOUT = 5


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


class Pep:
    """Клиент PDP для одной сессии чата."""

    def __init__(self, session_id: str, claims: dict, guard_state: dict | None = None) -> None:
        self.session_id = session_id
        # субъектные факты из проверенного токена: sub/role + scopes списком
        self.subject = {
            "sub": claims.get("sub", ""),
            "role": claims.get("role", ""),
            "scopes": claims.get("scope", "").split(),
        }
        self.guard_state = guard_state or {"ok": True}  # verdikt guardrail текущего хода
        self._opened = False

    async def _open(self) -> None:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=TIMEOUT) as client:
                r = await client.post(
                    f"{config.PDP_URL}/session",
                    json={"session_id": self.session_id, "subject": self.subject},
                )
            r.raise_for_status()
            self._opened = True
        except httpx.HTTPError:
            self._opened = False

    async def _ask(self, path: str, action: str, card) -> Decision:
        if not self._opened:
            await self._open()
            if not self._opened:
                return Decision(False, "pdp_unavailable")
        payload = {
            "session_id": self.session_id,
            "action": action,
            "input": {
                "action": card.action,
                "pending_id": card.pending_id,
                "details": json.dumps(card.details, ensure_ascii=False),
                "guard_ok": bool(self.guard_state.get("ok", True)),
                **self.subject,
            },
        }
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=TIMEOUT) as client:
                r = await client.post(f"{config.PDP_URL}{path}", json=payload)
            r.raise_for_status()
            body = r.json()
        except httpx.HTTPError:
            return Decision(False, "pdp_unavailable")
        return Decision(allowed=body.get("decision") == "Allow", reason=body.get("reason", ""))

    async def prepare(self, card) -> Decision:
        return await self._ask("/authorize", "Prepare", card)

    async def confirm(self, card) -> Decision:
        return await self._ask("/confirm", "ConfirmCard", card)

    async def execute(self, card) -> Decision:
        return await self._ask("/authorize", "Execute", card)
