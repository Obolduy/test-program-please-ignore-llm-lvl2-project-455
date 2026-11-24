"""REST-интерфейс моков бизнес-систем (готовое).

Единственный вход для изменений данных клиента: action-MCP-инструменты
вызывают эти endpoints, страница оператора читает тикет-очередь.
Аутентификации нет — это внутренний сегмент сети стенда; границей
доверия в контуре служит action-MCP с проверкой JWT.
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from services import store


def _sub_of(request: Request) -> str:
    return request.query_params.get("sub", "")


async def clients_get(request: Request) -> JSONResponse:
    """GET /mock/clients?sub=... — профиль и контакты клиента."""
    sub = _sub_of(request)
    record = store.client(sub)
    if record is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({
        "sub": record["sub"],
        "name": record["name"],
        "phone": record["phone"],
        "email": record["email"],
    })


async def accounts_list(request: Request) -> JSONResponse:
    """GET /mock/accounts?sub=... — счета с балансами."""
    return JSONResponse({"accounts": store.accounts(_sub_of(request))})


async def cards_list(request: Request) -> JSONResponse:
    """GET /mock/cards?sub=... — карты со статусами."""
    return JSONResponse({"cards": store.cards(_sub_of(request))})


async def limits_list(request: Request) -> JSONResponse:
    """GET /mock/limits?sub=... — лимиты карт."""
    return JSONResponse({"limits": store.card_limits(_sub_of(request))})


async def contacts_update(request: Request) -> JSONResponse:
    """POST /mock/contacts {"sub", "phone"?, "email"?} — обновить контакты."""
    body = await request.json()
    result = store.set_contacts(
        body.get("sub", ""), phone=body.get("phone"), email=body.get("email")
    )
    return JSONResponse(result)


async def transactions_list(request: Request) -> JSONResponse:
    """GET /mock/transactions?sub=&limit= — последние операции."""
    limit = min(int(request.query_params.get("limit", "10")), 30)
    return JSONResponse({"transactions": store.recent_transactions(_sub_of(request), limit)})


async def commission_rules_list(request: Request) -> JSONResponse:
    """GET /mock/commission-rules?product= — правила комиссий."""
    return JSONResponse({"rules": store.commission_rules(request.query_params.get("product") or None)})


async def cards_block(request: Request) -> JSONResponse:
    """POST /mock/cards/block {"sub", "card_id", "idempotency_key"}.

    Идемпотентность — на стороне бизнес-системы: даже скомпрометированный
    вызывающий не обойдёт её оркестратором.
    """
    body = await request.json()
    result = store.block_card(
        sub=body.get("sub", ""),
        card_id=body.get("card_id", ""),
        idempotency_key=body.get("idempotency_key", ""),
    )
    return JSONResponse(result)


async def limits_set(request: Request) -> JSONResponse:
    """POST /mock/limits/set {"sub", "card_id", "period", "amount", "idempotency_key"}."""
    body = await request.json()
    result = store.set_card_limit(
        sub=body.get("sub", ""),
        card_id=body.get("card_id", ""),
        period=body.get("period", ""),
        amount=float(body.get("amount", 0)),
        idempotency_key=body.get("idempotency_key", ""),
    )
    return JSONResponse(result)


async def tickets_list(request: Request) -> JSONResponse:
    """GET /mock/tickets?sub= — очередь обращений (страница оператора)."""
    return JSONResponse({"tickets": store.tickets(_sub_of(request) or None)})


async def tickets_create(request: Request) -> JSONResponse:
    """POST /mock/tickets/create {"sub", "subject", "note"} — создать обращение."""
    body = await request.json()
    result = store.create_ticket(
        body.get("sub", ""), body.get("subject", ""), body.get("note", "")
    )
    return JSONResponse(result)


async def admin_reset(request: Request) -> JSONResponse:
    """POST /mock/admin/reset — вернуть демо-данные в исходное состояние."""
    from core import seed as seed_mod

    stats = seed_mod.seed()
    return JSONResponse({"ok": True, **stats})


async def admin_fixtures(request: Request) -> JSONResponse:
    """POST /mock/admin/fixtures {"sub", "cards"?, "limits"?, "contacts"?}.

    Адресные изменения состояния перед сценарием eval-батареи: статус
    карты, лимиты, контакты. Каждый прогон начинается с reset + fixtures —
    сценарии не зависят от порядка запуска.
    """
    body = await request.json()
    sub = body.get("sub", "")
    if not sub:
        return JSONResponse({"error": "sub_required"}, status_code=400)
    applied = store.apply_fixtures(sub, body)
    return JSONResponse({"ok": True, "applied": applied})


async def admin_counts(request: Request) -> JSONResponse:
    """GET /mock/admin/counts — размеры таблиц (smoke стенда)."""
    return JSONResponse(store.counts())


def mock_routes() -> list[Route]:
    return [
        Route("/mock/clients", clients_get, methods=["GET"]),
        Route("/mock/accounts", accounts_list, methods=["GET"]),
        Route("/mock/cards", cards_list, methods=["GET"]),
        Route("/mock/limits", limits_list, methods=["GET"]),
        Route("/mock/contacts", contacts_update, methods=["POST"]),
        Route("/mock/transactions", transactions_list, methods=["GET"]),
        Route("/mock/commission-rules", commission_rules_list, methods=["GET"]),
        Route("/mock/cards/block", cards_block, methods=["POST"]),
        Route("/mock/limits/set", limits_set, methods=["POST"]),
        Route("/mock/tickets", tickets_list, methods=["GET"]),
        Route("/mock/tickets/create", tickets_create, methods=["POST"]),
        Route("/mock/admin/reset", admin_reset, methods=["POST"]),
        Route("/mock/admin/fixtures", admin_fixtures, methods=["POST"]),
        Route("/mock/admin/counts", admin_counts, methods=["GET"]),
    ]
