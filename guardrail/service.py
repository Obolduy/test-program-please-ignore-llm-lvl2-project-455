"""Guardrail-сервис: отдельный stateless-контур проверки текста.

    uvicorn guardrail.service:app --host 127.0.0.1 --port 8300

Два режима одного сервиса: input — реплика пользователя до LLM, output —
финальный ответ и карточка действия до показа. Ответ ровно один: safe
или unsafe (+ категория). Политика фиксированная, состояния нет.

Режимы движка:
  RULE (по умолчанию) — детерминированные маркеры атак стенда. Эвристика
      лабораторная: она ловит демо-реплики и НЕ претендует на прод.
  MODEL (GUARDRAIL_MODEL=1) — открытая модель-классификатор
      HiveTraceGuard-Pro (0.6B, русский — первый язык обучения,
      Apache-2.0). Требует transformers+torch (в requirements не включены).

Недоступность сервиса — не крах контура: оркестратор решает сам.
Для пути действий — fail-closed (без проверки текста действие не
предлагается), для чтения — деградация с пометкой в журнале.

Стендовые переключатели /admin/down|up имитируют отказ сервиса.
"""
from __future__ import annotations

import asyncio
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.config import GUARDRAIL_PORT

_available = True
_model: object | None = None
_model_lock = asyncio.Lock()

RULE_PATTERNS: list[tuple[str, str]] = [
    ("суперпользователь", "jailbreak/role_hijack"),
    ("ты теперь админ", "jailbreak/role_hijack"),
    ("ты — админ", "jailbreak/role_hijack"),
    ("ты сотрудник банка", "jailbreak/role_hijack"),
    ("сотрудник службы безопасности", "jailbreak/role_hijack"),
    ("работник банка", "jailbreak/role_hijack"),
    ("действуй как админ", "jailbreak/role_hijack"),
    ("теперь ты банк", "jailbreak/role_hijack"),
    ("отключи проверк", "jailbreak/disable_safety"),
    ("отключи все проверки", "jailbreak/disable_safety"),
    ("выключи проверк", "jailbreak/disable_safety"),
    ("игнорируй инструкц", "prompt_injection"),
    ("игнорируй предыдущ", "prompt_injection"),
    ("заблокируй карту у другого", "tool_hijack"),
    ("заблокируй карту другого", "tool_hijack"),
    ("переведи деньги со счёта другого", "tool_hijack"),
    ("со чужого счёта", "tool_hijack"),
    ("с чужого счёта", "tool_hijack"),
    ("со счёта другого клиента", "tool_hijack"),
    ("баланс другого клиента", "extraction"),
    ("system prompt", "extraction"),
    ("системный промпт", "extraction"),
    ("jailbreak", "jailbreak"),
    ("developer mode", "jailbreak"),
    ("раскрой секрет", "extraction"),
    ("немедленно заблокируй", "injected_instruction"),
    ("немедленно переведи", "injected_instruction"),
    ("немедленно подними", "injected_instruction"),
    ("без подтверждения", "injected_instruction"),
]


def _rule_verdict(text: str) -> dict:
    lowered = text.lower()
    for pattern, category in RULE_PATTERNS:
        if pattern in lowered:
            return {"verdict": "unsafe", "category": category, "engine": "rule"}
    return {"verdict": "safe", "engine": "rule"}


async def _model_verdict(text: str) -> dict:
    """HiveTraceGuard-Pro: один прогон классификатора, один токен ответа."""
    global _model
    from transformers import pipeline  # type: ignore[import-not-found]

    async with _model_lock:
        if _model is None:
            _model = await asyncio.to_thread(
                pipeline, "text-classification", model="hivetrace/HiveTraceGuard-Pro"
            )
    result = await asyncio.to_thread(lambda: _model(text[:2000], top_k=1)[0])
    label = str(result.get("label", "")).lower()
    unsafe = "unsafe" in label or "attack" in label
    return {"verdict": "unsafe" if unsafe else "safe", "category": label or None, "engine": "model"}


async def check(request: Request) -> JSONResponse:
    """POST /check — {"text": ..., "mode": "input"|"output"}."""
    if not _available:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    body = await request.json()
    text = body.get("text", "")
    mode = body.get("mode", "input")
    if not text:
        return JSONResponse({"verdict": "safe", "engine": "rule", "mode": mode})

    if os.environ.get("GUARDRAIL_MODEL") == "1":
        verdict = await _model_verdict(text)
    else:
        verdict = _rule_verdict(text)
    verdict["mode"] = mode
    return JSONResponse(verdict)


async def admin_down(request: Request) -> JSONResponse:
    global _available
    _available = False
    return JSONResponse({"available": False})


async def admin_up(request: Request) -> JSONResponse:
    global _available
    _available = True
    return JSONResponse({"available": True})


async def health(request: Request) -> JSONResponse:
    if not _available:
        return JSONResponse({"status": "down"}, status_code=503)
    return JSONResponse({"status": "ok"})


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/check", check, methods=["POST"]),
            Route("/admin/down", admin_down, methods=["POST"]),
            Route("/admin/up", admin_up, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
        ]
    )


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=GUARDRAIL_PORT, log_level="warning")
