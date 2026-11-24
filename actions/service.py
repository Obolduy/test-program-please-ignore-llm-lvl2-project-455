"""Логика действий поверх REST-моков: предусловия, карточки, исполнение.

prepare_* ничего не исполняют: проверяют предусловия и собирают данные
карточки («какую карту, что изменится, какие последствия»). Исполнение —
execute_action: его вызывает только код оркестратора после явного «да»,
с idempotency key.

Субъект (sub) сервер получает из JWT-токена вызова: идентичности в
контрактах нет — подменить нечего, структурная защита от IDOR.
"""
from __future__ import annotations

import json

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config
from actions.contracts import (
    ActionError,
    normalize_amount,
    normalize_email,
    normalize_mask,
    normalize_period,
    normalize_phone,
)

TIMEOUT = 10


async def _call(method: str, path: str, json_body: dict | None = None) -> dict:
    async with httpx.AsyncClient(trust_env=False, timeout=TIMEOUT) as client:
        r = await client.request(method, f"{config.STAND_URL}{path}", json=json_body)
        r.raise_for_status()
        return r.json()


async def _client(sub: str) -> dict:
    try:
        return await _call("GET", f"/mock/clients?sub={sub}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise ActionError("unknown_sub", "клиент не найден") from e
        raise


async def _cards(sub: str) -> list[dict]:
    return (await _call("GET", f"/mock/cards?sub={sub}"))["cards"]


def _label(card: dict) -> str:
    return f"{card['product']} •{card['mask']}"


async def prepare_block_card(sub: str, mask_raw: str) -> dict:
    """Карточка блокировки карты: нормализация → предусловия → view."""
    cards = await _cards(sub)
    card = normalize_mask(mask_raw, cards)
    if card["status"] == "blocked":
        raise ActionError(
            "card_already_blocked",
            f"карта {_label(card)} уже заблокирована",
            current="blocked",
        )
    return {
        "action": "block_card",
        "details": {"card_id": card["card_id"], "mask": card["mask"]},
        "view": {
            "что": "блокировка карты",
            "карта": _label(card),
            "последствия": "автоплатежи и подписки по карте остановятся; "
            "разблокировка или перевыпуск — через оператора, перевыпуск занимает 3–5 рабочих дней",
        },
    }


async def prepare_set_transfer_limit(sub: str, mask_raw: str, period_raw: str, amount_raw) -> dict:
    """Карточка изменения лимита переводов: было → станет."""
    cards = await _cards(sub)
    card = normalize_mask(mask_raw, cards)
    period = normalize_period(period_raw)
    amount = normalize_amount(amount_raw)
    current = (await _call("GET", f"/mock/limits?sub={sub}"))["limits"]
    row = next((l for l in current if str(l["mask"]) == card["mask"]), None)
    current_value = float(row[f"{period}_limit"]) if row else 0
    if current_value == float(amount):
        raise ActionError(
            "same_limit",
            f"лимит {_label(card)} уже {int(current_value)} ₽",
            period=period,
            current=int(current_value),
        )
    period_label = "дневной" if period == "daily" else "месячный"
    return {
        "action": "set_transfer_limit",
        "details": {"card_id": card["card_id"], "mask": card["mask"], "period": period, "amount": amount},
        "view": {
            "что": "изменение лимита переводов",
            "карта": _label(card),
            "период": period_label,
            "было": f"{int(current_value):,} ₽".replace(",", " "),
            "станет": f"{amount:,} ₽".replace(",", " "),
            "вступает в силу": "сразу после подтверждения",
        },
    }


async def prepare_contacts_update(sub: str, phone_raw: str | None, email_raw: str | None) -> dict:
    """Карточка обновления контактов: нормализация и непустота изменения."""
    current = await _client(sub)
    phone = normalize_phone(phone_raw)
    email = normalize_email(email_raw)
    if phone is None and email is None:
        raise ActionError(
            "nothing_to_update",
            "не передано ни телефона, ни почты",
            matches='phone="+7 900 123-45-67" или email="name@example.ru"',
        )
    view = {"что": "обновление контактных данных"}
    details: dict = {}
    if phone and phone != current["phone"]:
        view["телефон"] = f"{current['phone'] or '—'} → {phone}"
        details["phone"] = phone
    if email and email != current["email"]:
        view["почта"] = f"{current['email'] or '—'} → {email}"
        details["email"] = email
    if not details:
        raise ActionError("same_contacts", "новые контакты совпадают с текущими", current=current)
    return {"action": "contacts_update", "details": details, "view": view}


async def execute_action(sub: str, action: str, details_json: str, idempotency_key: str) -> dict:
    """Исполнение подтверждённого действия через REST-моки.

    Идемпотентность блокировки и лимита держит сама бизнес-система
    (duplicate); дедупликацию контактов ведёт оркестратор (app/idempotency.py).
    """
    try:
        details = json.loads(details_json) if details_json and details_json.strip() else {}
    except (TypeError, ValueError):
        raise ActionError("bad_details", "details_json — не JSON")

    if action == "block_card":
        return await _call(
            "POST", "/mock/cards/block",
            {"sub": sub, "card_id": details.get("card_id", ""), "idempotency_key": idempotency_key},
        )
    if action == "set_transfer_limit":
        return await _call(
            "POST", "/mock/limits/set",
            {"sub": sub, "card_id": details.get("card_id", ""),
             "period": details.get("period", ""), "amount": details.get("amount", 0),
             "idempotency_key": idempotency_key},
        )
    if action == "contacts_update":
        return await _call(
            "POST", "/mock/contacts",
            {"sub": sub, "phone": details.get("phone"), "email": details.get("email")},
        )
    raise ActionError(
        "unknown_action", f"действие «{action}» не обслуживается",
        available=["block_card", "set_transfer_limit", "contacts_update"],
    )


async def create_ticket(sub: str, subject: str, note: str) -> dict:
    """Обращение оператору: действие без карточки — низкий риск, но под аудитом."""
    return await _call("POST", "/mock/tickets/create", {"sub": sub, "subject": subject, "note": note})
