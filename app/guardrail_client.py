"""Клиент guardrail-сервиса: verdikt вероятностный, решения — за кодом.

Guardrail не даёт и не отнимает прав: он фильтрует текст. Недоступность
сервиса — не крах: для действий fail-closed (guard_ok=False → политика
не пропустит Prepare), для чтения — деградация с пометкой в журнале.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config

TIMEOUT = 5


@dataclass
class Verdict:
    safe: bool
    category: str = ""
    available: bool = True


async def check(text: str, mode: str = "input") -> Verdict:
    """Проверка текста; недоступность — Verdict(safe=True, available=False):
    читание продолжается, действия блокируются флагом guard_ok."""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=TIMEOUT) as client:
            r = await client.post(
                f"{config.GUARDRAIL_URL}/check", json={"text": text, "mode": mode}
            )
        if r.status_code == 503:
            return Verdict(safe=True, available=False)
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPError:
        return Verdict(safe=True, available=False)
    return Verdict(
        safe=body.get("verdict") == "safe",
        category=str(body.get("category") or ""),
        available=True,
    )
