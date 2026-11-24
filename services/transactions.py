"""MCP Transactions: операции и правила комиссий (готовый read-сервер
стенда).

Исполнение операций здесь не выставляется: write-путь контура идёт
через action-MCP поверх REST-моков, где стоят идемпотентность и
проверки предусловий. Чужой txn_id возвращает not_found: сервер ищет
операцию строго среди операций субъекта из токена.
"""
from __future__ import annotations

from mcp.server.auth.middleware.auth_context import get_access_token

from core.config import RESOURCES
from core.server_auth import make_mcp_server
from services import store

transactions = make_mcp_server(
    "transactions",
    resource=RESOURCES["transactions"],
    instructions="Операции по счетам и правила комиссий. Субъект определяется токеном вызова.",
)


def _subject() -> str:
    at = get_access_token()
    assert at is not None, "инструмент вызван без аутентифицированного токена"
    return at.subject


@transactions.tool()
def list_recent_transactions(limit: int = 10) -> dict:
    """Последние операции вызывающего клиента: дата, магазин, сумма, категория."""
    sub = _subject()
    rows = store.recent_transactions(sub, max(1, min(limit, 30)))
    return {"client_id": sub, "transactions": rows}


@transactions.tool()
def find_transactions(
    period: str = "",
    category: str = "",
    merchant: str = "",
    amount_min: float | None = None,
    amount_max: float | None = None,
    direction: str = "",
) -> dict:
    """Найти операции по фильтрам (все опциональны, кроме прав по токену).

    period — месяц «YYYY-MM» (например 2026-09); category — категория
    (Продукты, Комиссии, Подписки, Переводы…); merchant — магазин или
    получатель; amount_min/amount_max — границы суммы; direction — in
    (поступления) или out (списания).
    """
    sub = _subject()
    rows = store.find_transactions(
        sub,
        period=period or None,
        category=category or None,
        merchant=merchant or None,
        amount_min=amount_min,
        amount_max=amount_max,
        direction=direction or None,
    )
    return {"client_id": sub, "transactions": rows, "count": len(rows)}


@transactions.tool()
def get_transaction(txn_id: str) -> dict:
    """Одна операция по идентификатору (txn_id из find/list результатов)."""
    sub = _subject()
    row = store.transaction(sub, str(txn_id).strip())
    if row is None:
        return {"error": "not_found", "hint": "идентификатор берите из find_transactions"}
    return row


@transactions.tool()
def list_commission_rules(product: str = "") -> dict:
    """Правила комиссий банка: обслуживание карт, снятие наличных, переводы.

    product — название продукта (например, Дебетовая карта «Всё про»);
    пусто — все правила.
    """
    _sub = _subject()  # публичные правила, но без токена инструмент не вызвать
    rows = store.commission_rules(product or None)
    return {"rules": rows}
