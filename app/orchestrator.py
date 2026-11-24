"""Оркестратор хода: guardrail → карточка → план → гейт → агенты → редактор.

Ход — один запрос клиента. Пока жива карточка подтверждения, ходом
управляет детерминированный код («да» исполняет, «нет» отменяет, смена
темы обслуживается с живой карточкой) — LLM в этом контуре нет. Обычный
ход: guardrail на входе → план с гейтом → доменные агенты → guardrail на
выходе → ответ. Фронтенд получает события потоком SSE.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable

from app import audit, cards, guardrail_client, idempotency, planner
from app.editor import build_editor, editor_task
from app.llm import run_agent
from app.sessions import Session, pep_of
from app.workers import build_workers

Emit = Callable[[dict], Awaitable[None]]

REFUSAL_UNSAFE = (
    "Не могу выполнить эту просьбу. Если нужен человек — скажите, и я позову оператора."
)
REFUSAL_GUARDRAIL_DOWN_ACTION = (
    "Действия временно недоступны с моей стороны: не работает проверка безопасности. "
    "Могу ответить на вопросы по вашим счетам и картам или позвать оператора."
)


def _sources_of(runs: list[dict]) -> list[str]:
    """Источники базы знаний из tool-результатов агентов (для подписи ответа)."""
    found: list[str] = []
    for run in runs:
        for call in run.get("tools", []):
            result = call.get("result")
            if isinstance(result, dict):
                for hit in result.get("results", []):
                    if isinstance(hit, dict) and hit.get("source"):
                        found.append(hit["source"])
    return sorted(set(found))


async def run_turn(session: Session, message: str, emit: Emit) -> None:
    """Полный ход: guardrail → карточка/план → агенты → guardrail → ответ."""
    session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"

    # routing хода: трек приклеивает ход к провайдеру
    try:
        from inference import route_turn

        route_turn(session.trace_id, message)
    except Exception:  # noqa: BLE001 — routing не обязан быть настроен
        pass

    # 1) вход guardrail: небезопасная реплика не доходит до LLM
    verdict = await guardrail_client.check(message, mode="input")
    if not verdict.available:
        session.guard_state["ok"] = False  # fail-closed для действий хода
        audit.append(session.user.sub, "guardrail:input", "guardrail_degraded",
                     trace_id=session.trace_id, session_id=session.session_id)
    elif not verdict.safe:
        session.guard_state["ok"] = False
        audit.append(session.user.sub, "guardrail:input", "guardrail_unsafe",
                     trace_id=session.trace_id, session_id=session.session_id,
                     detail=verdict.category)
        await emit({"type": "reply", "text": REFUSAL_UNSAFE, "sources": []})
        return

    card = cards.active(session)
    if card is not None:
        if await _handle_card(session, card, message, emit):
            return

    try:
        # 2) план → агенты → редактор: любое падение модели — деградация,
        # не ошибка хода (исчерпан пул провайдеров и т.п.)
        plan = await _plan_of(session, message, emit)

        if plan.question and not plan.steps:
            await emit({"type": "reply", "text": plan.question, "sources": []})
            return

        runs = []
        card_before = cards.active(session)
        if plan.steps:
            workers = build_workers(session, agent_ids=tuple(dict.fromkeys(s.agent for s in plan.steps)))
            for step in plan.steps:
                run = await run_agent(workers[step.agent], step.task, trace_id=session.trace_id)
                runs.append({"agent": step.agent, "task": step.task, **run})
                fresh = cards.active(session)
                if fresh is not None and (card_before is None or fresh.pending_id != card_before.pending_id):
                    break  # карточка создана — ход завершаем: ждём подтверждение

        editor = build_editor(session)
        editor_run = await run_agent(editor, editor_task(message, runs), trace_id=session.trace_id)
        reply_text, sources = editor_run["report"], _sources_of(runs)
    except Exception as e:  # noqa: BLE001 — пул провайдеров исчерпан: деградация, не ошибка
        await _degrade_turn(session, message, emit, repr(e)[:160])
        return

    # 2) выход guardrail: ответ и карточка проверяются до показа
    fresh = cards.active(session)
    out_text = reply_text + ("\n" + json.dumps(fresh.view, ensure_ascii=False) if fresh else "")
    out_verdict = await guardrail_client.check(out_text, mode="output")
    if out_verdict.available and not out_verdict.safe:
        if fresh:
            cards.settle(session, fresh, "denied")
        audit.append(session.user.sub, "guardrail:output", "guardrail_unsafe",
                     trace_id=session.trace_id, session_id=session.session_id,
                     detail=out_verdict.category)
        await emit({"type": "reply", "text": REFUSAL_UNSAFE, "sources": []})
        return
    if not out_verdict.available:
        audit.append(session.user.sub, "guardrail:output", "guardrail_degraded",
                     trace_id=session.trace_id, session_id=session.session_id,
                     detail="fail-closed: действие не показано" if fresh else "чтение продолжено")
        if fresh:
            cards.settle(session, fresh, "denied")
            await emit({"type": "reply", "text": REFUSAL_GUARDRAIL_DOWN_ACTION, "sources": []})
            return

    if fresh:
        await emit({"type": "card", "pending_id": fresh.pending_id, "card": fresh.public_view(ttl_sec=120)})
    await emit({
        "type": "reply",
        "text": reply_text,
        "sources": sources,
    })


async def _degrade_turn(session: Session, message: str, emit: Emit, error: str) -> None:
    """Оба провайдера недоступны: тикет оператору вместо ошибки хода.

    Ход не теряется: обращение создаётся через action-MCP, аудит
    записывает причину, клиент получает честный текст. SSE события
    `error` здесь нет — это режим работы, а не поломка чата.
    """
    from inference.degrade import NONE_NOTICE
    from inference.providers import PROVIDERS

    mode_info = "none"
    try:
        from inference import degrade

        mode_info = degrade.check(PROVIDERS)["mode"]
    except Exception:  # noqa: BLE001 — без degrade-модуля считаем worst case
        pass
    audit.append(session.user.sub, "degrade", "escalated",
                 trace_id=session.trace_id, session_id=session.session_id,
                 detail=f"mode={mode_info}; {error}")
    try:
        result = await session.pool.call(
            "actions", "create_ticket",
            {"sub": session.user.sub,
             "subject": "авария: оба провайдера недоступны",
             "note": f"ход не обработан ({mode_info}). Сообщение клиента: {message}"},
        )
        ticket = result.get("ticket_id", "?")
        text = f"{NONE_NOTICE} Тикет {ticket}."
    except Exception:  # noqa: BLE001 — тикет-система тоже недоступна: честно говорим
        ticket = "?"
        text = f"{NONE_NOTICE} (тикет-система также недоступна — обратитесь в отделение.)"
        audit.append(session.user.sub, "degrade", "error",
                     trace_id=session.trace_id, session_id=session.session_id,
                     detail="create_ticket failed")
    await emit({"type": "reply", "text": text, "sources": []})


async def _handle_card(session: Session, card, message: str, emit: Emit) -> bool:
    """«Да — что да?»: классифицирует код; True — ход обслуживан без LLM."""
    answer = cards.classify_answer(message)
    if answer == "yes":
        await emit({"type": "plan", "agents": [f"исполнение подтверждённого: {card.action}"], "dropped": []})
        await emit({"type": "reply", "text": await execute_pending(session, card), "sources": []})
        return True
    if answer == "no":
        cards.settle(session, card, "rejected")
        audit.append(session.user.sub, card.action, "rejected_by_user",
                     trace_id=session.trace_id, session_id=session.session_id, args=card.details)
        await emit({"type": "reply", "text": "Хорошо, отменяю. Ничего не изменено.", "sources": []})
        return True
    if answer == "operator":
        cards.settle(session, card, "rejected")
        await emit({"type": "reply", "text": await escalate(session, card, "user_asked_operator"), "sources": []})
        return True
    return False  # смена темы: обычный ход, карточка остаётся живой


async def execute_pending(session: Session, card) -> str:
    """«Да» → PDP (ConfirmCard → Execute) → исполнение с idempotency key."""
    pep = pep_of(session)
    confirm_decision = await pep.confirm(card)
    if not confirm_decision.allowed:
        reason = confirm_decision.reason
        state = "expired" if reason in ("confirmation_expired", "card_not_prepared") else "denied"
        cards.settle(session, card, state)
        audit.append(session.user.sub, card.action, "denied",
                     trace_id=session.trace_id, session_id=session.session_id,
                     args=card.details, detail=f"pdp ConfirmCard {reason}")
        if reason == "confirmation_expired":
            return "Подтверждение истекло — попросите операцию заново, оформлю свежую карточку."
        if reason == "card_not_prepared":
            return "Карточка не найдена — операцию нужно запросить заново."
        if reason == "pdp_unavailable":
            return ("Действия временно недоступны с моей стороны: не работает проверка прав. "
                    "Могу ответить на вопросы или позвать оператора.")
        return f"Подтверждение не принято: {reason}."

    execute_decision = await pep.execute(card)
    if not execute_decision.allowed:
        cards.settle(session, card, "denied")
        reason = execute_decision.reason
        audit.append(session.user.sub, card.action, "denied",
                     trace_id=session.trace_id, session_id=session.session_id,
                     args=card.details, detail=f"pdp Execute {reason}")
        if reason == "session_limit":
            return ("Лимит исчерпан: не более двух операций изменения данных за сессию. "
                    "Могу позвать оператора — скажите, если нужно.")
        return f"Действие не прошло проверку прав: {reason}."

    cards.settle(session, card, "confirmed")
    key = f"{card.action}:{card.pending_id}"
    stored = await idempotency.execute(
        key, card.sub, card.action,
        lambda: session.pool.call(
            "actions",
            "execute_action",
            {
                "sub": card.sub,
                "action": card.action,
                "details_json": json.dumps(card.details, ensure_ascii=False),
                "pending_id": card.pending_id,
                "idempotency_key": key,
            },
        ),
    )
    result = stored.result
    outcome = result.get("outcome") or result.get("error") or "error"

    audit_args = {**card.details, "pending_id": card.pending_id}
    if stored.duplicate:
        audit.append(card.sub, card.action, "duplicate_suppressed",
                     trace_id=session.trace_id, session_id=session.session_id,
                     args=audit_args, idempotency_key=key)
        return "Эта операция уже исполнена — повтор не выполнялся."
    if outcome in ("executed", "toggled", "updated"):
        audit.append(card.sub, card.action, "executed",
                     trace_id=session.trace_id, session_id=session.session_id,
                     args=audit_args, before=result.get("before"), after=result.get("after"),
                     idempotency_key=key)
        view = card.public_view(ttl_sec=0)
        return f"Готово: {view.get('что', card.action)} выполнено."
    if outcome in ("card_already_blocked", "same_limit"):
        # состояние изменилось между prepare и «да»: честно докладываем
        audit.append(card.sub, card.action, "rejected_by_state",
                     trace_id=session.trace_id, session_id=session.session_id,
                     args=audit_args, detail=outcome)
        state_text = ("карта уже заблокирована." if outcome == "card_already_blocked"
                      else "такой лимит уже стоит.")
        return f"Пока ждали подтверждения, состояние изменилось: {state_text}"
    if outcome == "unknown_sub":
        audit.append(card.sub, card.action, "denied",
                     trace_id=session.trace_id, session_id=session.session_id,
                     args=audit_args, detail="unknown_sub")
        return "Действие не выполнено: клиент не найден."
    audit.append(card.sub, card.action, "rejected_by_contract",
                 trace_id=session.trace_id, session_id=session.session_id,
                 args=audit_args, detail=outcome)
    return f"Операция не состоялась: {outcome}."


async def escalate(session: Session, card, reason: str) -> str:
    """Эскалация оператору: тикет с контекстом, история не рассказывается дважды."""
    result = await session.pool.call(
        "actions",
        "create_ticket",
        {"sub": session.user.sub, "subject": f"эскалация: {card.action}", "note": f"причина: {reason}; карточка: {json.dumps(card.view, ensure_ascii=False)}"},
    )
    ticket_id = result.get("ticket_id", "?")
    audit.append(session.user.sub, "escalation", "escalated",
                 trace_id=session.trace_id, session_id=session.session_id,
                 args={"action": card.action, "reason": reason}, detail=ticket_id)
    return f"Передаю диалог оператору — тикет {ticket_id}, история сохранена."


async def _plan_of(session: Session, message: str, emit: Emit):
    """План хода с гейтом: что отброшено — видно в событии плана."""
    if session.planner is None:
        session.planner = planner.build_planner()
    raw_plan, session.thread = await planner.next_plan(
        session.planner, session.thread, message, trace_id=session.trace_id
    )
    plan, dropped = planner.gate(raw_plan)

    agents = [f"{s.agent}: {s.task}" for s in plan.steps]
    for reason in dropped:
        session.note(f"plan_dropped:{reason}")
    await emit({
        "type": "plan",
        "agents": agents or ["без агентов"],
        "dropped": dropped,
        "question": plan.question,
    })
    return plan
