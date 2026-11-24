"""Обёртки MCP-инструментов для агентов: вызов с JWT + учёт трейса.

Два приёма, которые здесь живут:

  машинно-читаемая ошибка  MCPError от action-MCP (код + data с подсказками
                           available/matches/current) превращается в JSON
                           tool-результат — модель исправляет свой вызов,
                           а не роняет сессию;
  крючок prepare           успешный prepare_* создаёт карточку подтверждения,
                           допущенную политикой (app.cards + PEP): модель
                           видит «карточка готова, спроси подтверждение»,
                           но исполнить сама не может.

Барьеры прав: wrap_tools фильтрует набор по allowlist (барьер 1), admit
проверяет операцию в момент вызова (барьер 2). Каждый tool-вызов модели
пишется в tool_calls, каждый отказ — в аудит.
"""
from __future__ import annotations

import json

from agents import FunctionTool
from agents.run_context import RunContextWrapper
from mcp.shared.exceptions import MCPError

from app import audit, cards, permissions
from app.sessions import Session, pep_of


def wrap_mcp_tool(session: Session, name: str) -> FunctionTool:
    """Обёртка одного MCP-инструмента для агента этой сессии."""

    async def invoke(ctx: RunContextWrapper, input_json: str) -> str:
        import time

        t0 = time.perf_counter()

        def log_call(args: dict, outcome: str) -> None:
            """Каждый tool-вызов модели — в tool_calls (трейс контура)."""
            from core import db

            db.execute(
                "insert into tool_calls(trace_id, tool, args, outcome, latency_ms)"
                " values (%s, %s, %s, %s, %s)",
                (
                    session.trace_id or None, name,
                    json.dumps(args, ensure_ascii=False)[:400], outcome,
                    int((time.perf_counter() - t0) * 1000),
                ),
            )

        try:
            args = json.loads(input_json) if input_json and input_json.strip() else {}
        except (TypeError, ValueError):
            log_call({}, "bad_arguments")
            return json.dumps(
                {"error": "bad_arguments", "detail": "аргументы — не JSON; повтори вызов по схеме"},
                ensure_ascii=False,
            )
        # барьер 2: допуск операции в момент вызова, до ухода на сервер
        decision = permissions.admit(session.allowlist, name, args)
        if not decision.allowed:
            log_call(args, f"denied:{decision.reason[:40]}")
            audit.append(
                session.user.sub, name, "denied",
                trace_id=session.trace_id, session_id=session.session_id,
                args=args, detail=f"barrier=2 {decision.reason}",
            )
            return json.dumps(
                {"error": "denied", "reason": decision.reason,
                 "detail": "операция недоступна этому пользователю; предложи оператора, если нужно"},
                ensure_ascii=False,
            )
        session.note(name)
        try:
            result = await session.pool.call(session.pool.resource_of(name), name, args)
        except MCPError as e:
            data = e.data if isinstance(e.data, dict) else {}
            log_call(args, str(getattr(e, "message", "tool_error"))[:40])
            return json.dumps(
                {"error": getattr(e, "message", "tool_failed")[:60], **data},
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001 — сбой сервера становится данными для модели
            log_call(args, "tool_failed")
            return json.dumps(
                {"error": "tool_failed", "detail": str(e)[:160]},
                ensure_ascii=False,
            )

        log_call(args, "ok")
        result = await _maybe_create_card(session, name, result)
        return json.dumps(result, ensure_ascii=False)

    return FunctionTool(
        name=name,
        description=session.pool.tool_description(name),
        params_json_schema=session.pool.tool_schema(name),
        on_invoke_tool=invoke,
    )


async def _maybe_create_card(session: Session, name: str, result: dict) -> dict:
    """Успешный prepare_* → карточка подтверждения, допущенная политикой.

    Карточку создаёт код, но существовать она может только с Allow от
    PDP: Deny — карточки нет, модель получает отказ с причиной.
    """
    if not name.startswith("prepare_") or not (isinstance(result, dict) and result.get("ok")):
        return result
    card = cards.create(session, result["action"], result["details"], result["view"])
    decision = await pep_of(session).prepare(card)
    if not decision.allowed:
        cards.settle(session, card, "denied")
        audit.append(
            session.user.sub, card.action, "denied",
            trace_id=session.trace_id, session_id=session.session_id,
            args=card.details, detail=f"pdp Prepare {decision.reason}",
        )
        return {
            "card_denied": True,
            "reason": decision.reason,
            "next": "честно перескажи пользователю, что операция не прошла проверку прав; "
            "предложи оператора, если он настаивает",
        }
    return {
        "card_created": True,
        "card": card.public_view(ttl_sec=120),
        "next": "перескажи карточку пользователю и спроси подтверждение «да/нет»; "
        "сам ты исполнить не можешь — исполнение после «да»",
    }


def wrap_tools(session: Session, names: list[str]) -> list[FunctionTool]:
    """Барьер 1: собрать обёртки только для инструментов allowlist сессии."""
    known = set(session.pool.tool_names())
    visible = set(permissions.visible_tools(session.allowlist, sorted(known)))
    return [wrap_mcp_tool(session, n) for n in names if n in visible]
