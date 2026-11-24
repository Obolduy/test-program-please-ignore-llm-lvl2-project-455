"""MCP Accounts: счета, карты, лимиты и контакты клиента (готовый
read-сервер стенда).

Ключевой приём сервера: субъект операции читается из проверенного токена
вызова (get_access_token() → subject/claims), а не из аргументов.
Идентификатора клиента нет в контракте инструментов — подменить нечего,
поле отсутствует: структурная защита от IDOR. Выбор своей карты — по
маске (последние 4 цифры), и сервер всё равно фильтрует по субъекту:
чужая маска возвращает not_found, а не чужие данные.

Инструмент оператора (get_client_overview) — единственное место, где
чужой идентификатор легитимен: он доступен только роли operator, и это
проверяет сервер по role из claims.
"""
from __future__ import annotations

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.shared.exceptions import MCPError

from core.config import RESOURCES
from core.server_auth import make_mcp_server
from services import store

FORBIDDEN_CODE = -32003  # прикладной код стенда: роль не подходит

accounts = make_mcp_server(
    "accounts",
    resource=RESOURCES["accounts"],
    instructions="Счета, карты, лимиты и контакты клиента. Субъект определяется токеном вызова.",
)


def _subject() -> tuple[str, dict]:
    at = get_access_token()
    assert at is not None, "инструмент вызван без аутентифицированного токена"
    return at.subject, at.claims


def _require_role(*roles: str) -> tuple[str, dict]:
    sub, claims = _subject()
    if claims.get("role") not in roles:
        raise MCPError(
            code=FORBIDDEN_CODE,
            message="forbidden: role required",
            data={"error": "forbidden", "required_roles": list(roles), "sub": sub},
        )
    return sub, claims


@accounts.tool()
def get_my_overview() -> dict:
    """Обзор вызывающего клиента: счета с балансами и карты списком (по токену вызова)."""
    sub, _ = _subject()
    rows = store.accounts(sub)
    if not rows:
        return {"error": "not_found"}
    return {
        "client_id": sub,
        "accounts": rows,
        "cards": store.cards(sub),
    }


@accounts.tool()
def get_my_cards() -> dict:
    """Карты вызывающего клиента: маска, продукт, статус, счёт, срок действия."""
    sub, _ = _subject()
    rows = store.cards(sub)
    if not rows:
        return {"error": "not_found"}
    return {"client_id": sub, "cards": rows}


@accounts.tool()
def get_card_limits(mask: str) -> dict:
    """Действующие лимиты по маске карты (последние 4 цифры) вызывающего клиента.

    Лимиты переводов: дневной и месячный. Чужая маска вернёт not_found —
    сервер ищет только среди карт токена.
    """
    sub, _ = _subject()
    # маска «как попало»: «•4417», «*4417», « 4417 » — значимы только цифры
    digits = "".join(ch for ch in str(mask) if ch.isdigit())
    card = store.card_by_mask(sub, digits)
    if card is None:
        return {
            "error": "not_found",
            "hint": "проверьте маску: последние 4 цифры карты из get_my_cards",
        }
    return {
        "client_id": sub,
        "mask": card["mask"],
        "product": card["product"],
        "status": card["status"],
        "daily_limit": float(card["daily_limit"]) if card["daily_limit"] is not None else None,
        "monthly_limit": float(card["monthly_limit"]) if card["monthly_limit"] is not None else None,
    }


@accounts.tool()
def get_my_contacts() -> dict:
    """Контактные данные вызывающего клиента: телефон и почта из профиля."""
    sub, _ = _subject()
    record = store.client(sub)
    if record is None:
        return {"error": "not_found"}
    return {
        "client_id": sub,
        "name": record["name"],
        "phone": record["phone"],
        "email": record["email"],
    }


@accounts.tool()
def get_client_overview(client_id: str) -> dict:
    """Обзор клиента для оператора поддержки (role=operator).

    Единственный инструмент с чужим идентификатором в аргументах:
    доступ проверяет сервер по роли из claims токена.
    """
    _sub, _claims = _require_role("operator")
    record = store.client(client_id)
    if record is None:
        return {"error": "not_found"}
    return {
        "client_id": client_id,
        "name": record["name"],
        "accounts": store.accounts(client_id),
        "cards": store.cards(client_id),
    }
