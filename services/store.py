"""Состояние моков бизнес-систем: SQL-слой поверх Postgres.

В проде это АБС банка, карточный процессинг и система обращений — со
своими хранилищами. Один и тот же слой обслуживает read-MCP-инструменты
и REST-интерфейс моков; эффект действий сразу виден в read-запросах:
заблокированная карта видна в get_my_cards, новый лимит — в лимитах.
"""
from __future__ import annotations

import json

from core import db

# Исполнение write-операций идёт через idempotency-таблицу: повтор с тем
# же ключом не выполняет операцию второй раз (защита дублей на стороне
# бизнес-системы, а не только оркестратора).


def _dict_row(cur) -> dict | None:
    """Строка курсора → словарь: голый курсор psycopg возвращает кортежи."""
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


# ── Клиенты и контакты ───────────────────────────────────────────────────

def client(sub: str) -> dict | None:
    return db.fetchone("SELECT * FROM clients WHERE sub = %s", (sub,))


def clients_all_names() -> list[str]:
    """Имена всех клиентов: словарь для PII-анонимизатора."""
    return [r["name"] for r in db.fetchall("SELECT name FROM clients ORDER BY name")]


def set_contacts(sub: str, *, phone: str | None, email: str | None) -> dict:
    row = db.fetchone(
        "update clients set"
        " phone = coalesce(%s, phone),"
        " email = coalesce(%s, email)"
        " where sub = %s returning *",
        (phone, email, sub),
    )
    return {"outcome": "updated" if row else "not_found", "phone": row["phone"] if row else None,
            "email": row["email"] if row else None}


# ── Счета, карты, лимиты ─────────────────────────────────────────────────

def accounts(sub: str) -> list[dict]:
    return db.fetchall(
        "SELECT account_id, kind, title, balance, currency FROM accounts"
        " WHERE sub = %s ORDER BY account_id",
        (sub,),
    )


def cards(sub: str) -> list[dict]:
    return db.fetchall(
        "SELECT c.card_id, c.mask, c.product, c.status, c.expires, a.title AS account"
        " FROM cards c JOIN accounts a ON a.account_id = c.account_id"
        " WHERE c.sub = %s ORDER BY c.mask",
        (sub,),
    )


def card_by_mask(sub: str, mask: str) -> dict | None:
    return db.fetchone(
        "SELECT c.card_id, c.mask, c.product, c.status, c.expires, a.title AS account,"
        " l.daily_limit, l.monthly_limit"
        " FROM cards c JOIN accounts a ON a.account_id = c.account_id"
        " LEFT JOIN card_limits l ON l.card_id = c.card_id"
        " WHERE c.sub = %s AND c.mask = %s",
        (sub, mask),
    )


def card_limits(sub: str) -> list[dict]:
    return db.fetchall(
        "SELECT c.mask, c.product, l.daily_limit, l.monthly_limit"
        " FROM cards c JOIN card_limits l ON l.card_id = c.card_id"
        " WHERE c.sub = %s ORDER BY c.mask",
        (sub,),
    )


def _execute_idempotent(
    cur, *, key: str, sub: str, action: str, view: dict
) -> dict:
    """Запись результата идемпотентной операции: известный ключ → duplicate."""
    known = cur.execute(
        "SELECT result FROM idempotency WHERE key = %s", (key,)
    ).fetchone()
    if known:
        return {**known[0], "outcome": "duplicate"}
    cur.execute(
        "INSERT INTO idempotency(key, sub, action, result) VALUES (%s, %s, %s, %s)",
        (key, sub, action, json.dumps(view, ensure_ascii=False)),
    )
    return {"outcome": "executed", **view}


def block_card(*, sub: str, card_id: str, idempotency_key: str) -> dict:
    """Исполнение блокировки карты: идемпотентно.

    Исходы: executed / duplicate / card_already_blocked / not_found.
    Идемпотентность проверяется первой: повтор с тем же ключом —
    duplicate, даже если состояние уже изменила первая попытка.
    """
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT mask, product, status FROM cards WHERE card_id = %s AND sub = %s",
            (card_id, sub),
        )
        card = _dict_row(cur)
        if card is None:
            return {"outcome": "not_found"}
        if card["status"] == "blocked":
            known = cur.execute(
                "SELECT result FROM idempotency WHERE key = %s", (idempotency_key,)
            ).fetchone()
            if known:
                return {**known[0], "outcome": "duplicate"}
            return {"outcome": "card_already_blocked", "mask": card["mask"]}
        cur.execute("UPDATE cards SET status = 'blocked' WHERE card_id = %s", (card_id,))
        result = _execute_idempotent(
            cur, key=idempotency_key, sub=sub, action="block_card",
            view={"before": "active", "after": "blocked", "mask": card["mask"],
                  "product": card["product"]},
        )
        conn.commit()
        return result


def set_card_limit(
    *, sub: str, card_id: str, period: str, amount: float, idempotency_key: str
) -> dict:
    """Исполнение изменения лимита: идемпотентно.

    period — daily | monthly. Исходы: executed / duplicate /
    same_limit / not_found. unknown_period отсекается контрактами.
    Идемпотентность проверяется первой — как в block_card.
    """
    column = {"daily": "daily_limit", "monthly": "monthly_limit"}.get(period)
    if column is None:
        return {"outcome": "unknown_period", "available": ["daily", "monthly"]}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.card_id, c.mask, c.product, l." + column + " AS current"
            " FROM cards c LEFT JOIN card_limits l ON l.card_id = c.card_id"
            " WHERE c.card_id = %s AND c.sub = %s",
            (card_id, sub),
        )
        card = _dict_row(cur)
        if card is None:
            return {"outcome": "not_found"}
        if float(card["current"]) == float(amount):
            known = cur.execute(
                "SELECT result FROM idempotency WHERE key = %s", (idempotency_key,)
            ).fetchone()
            if known:
                return {**known[0], "outcome": "duplicate"}
            return {"outcome": "same_limit", "mask": card["mask"], "current": float(card["current"])}
        cur.execute(
            f"UPDATE card_limits SET {column} = %s WHERE card_id = %s",
            (amount, card_id),
        )
        result = _execute_idempotent(
            cur, key=idempotency_key, sub=sub, action="set_transfer_limit",
            view={"before": float(card["current"]), "after": float(amount),
                  "mask": card["mask"], "period": period},
        )
        conn.commit()
        return result


# ── Операции ─────────────────────────────────────────────────────────────

def recent_transactions(sub: str, limit: int = 10) -> list[dict]:
    return db.fetchall(
        "SELECT txn_id, to_char(ts, 'YYYY-MM-DD HH24:MI') AS ts, amount, direction,"
        " merchant, category, description, status"
        " FROM transactions WHERE sub = %s ORDER BY ts DESC LIMIT %s",
        (sub, limit),
    )


def find_transactions(
    sub: str, *, period: str | None = None, category: str | None = None,
    merchant: str | None = None, amount_min: float | None = None,
    amount_max: float | None = None, direction: str | None = None, limit: int = 20,
) -> list[dict]:
    """Поиск операций по фильтрам; все условия опциональны, кроме субъекта.

    Фильтры мягкие: категория и магазин ищутся по подстроке без учёта
    регистра, период — префиксом («2026-09» ловит весь месяц).
    """
    where, params = ["sub = %s"], [sub]
    if period:
        where.append("to_char(ts, 'YYYY-MM') LIKE %s")
        params.append(f"{period}%")
    if category:
        where.append("category ILIKE %s")
        params.append(f"%{category}%")
    if merchant:
        where.append("merchant ILIKE %s")
        params.append(f"%{merchant}%")
    if amount_min is not None:
        where.append("amount >= %s")
        params.append(amount_min)
    if amount_max is not None:
        where.append("amount <= %s")
        params.append(amount_max)
    if direction in ("in", "out"):
        where.append("direction = %s")
        params.append(direction)
    params.append(limit)
    return db.fetchall(
        "SELECT txn_id, to_char(ts, 'YYYY-MM-DD HH24:MI') AS ts, amount, direction,"
        " merchant, category, description, status"
        " FROM transactions WHERE " + " AND ".join(where) + " ORDER BY ts DESC LIMIT %s",
        tuple(params),
    )


def transaction(sub: str, txn_id: str) -> dict | None:
    """Операция строго своего субъекта: чужой txn_id → not_found."""
    return db.fetchone(
        "SELECT txn_id, to_char(ts, 'YYYY-MM-DD HH24:MI') AS ts, amount, direction,"
        " merchant, category, description, status"
        " FROM transactions WHERE sub = %s AND txn_id = %s",
        (sub, txn_id),
    )


def commission_rules(product: str | None = None) -> list[dict]:
    if product:
        rows = db.fetchall(
            "SELECT rule_id, product, code, description, amount_rub"
            " FROM commission_rules WHERE product = %s OR product = 'все' ORDER BY rule_id",
            (product,),
        )
    else:
        rows = db.fetchall(
            "SELECT rule_id, product, code, description, amount_rub"
            " FROM commission_rules ORDER BY rule_id"
        )
    return rows


# ── Обращения ────────────────────────────────────────────────────────────

def create_ticket(sub: str, subject: str, note: str) -> dict:
    row = db.fetchone(
        "insert into tickets(id, sub, subject, note)"
        " values ('t_' || lpad((floor(random() * 1679616))::text, 4, '0'), %s, %s, %s)"
        " returning id",
        (sub, subject, note),
    )
    return {"ticket_id": row["id"], "status": "open"}


def tickets(sub: str | None = None) -> list[dict]:
    if sub:
        rows = db.fetchall(
            "SELECT id, sub, subject, note, status,"
            " to_char(created_at, 'YYYY-MM-DD HH24:MI') AS created_at"
            " FROM tickets WHERE sub = %s ORDER BY created_at DESC",
            (sub,),
        )
    else:
        rows = db.fetchall(
            "SELECT id, sub, subject, note, status,"
            " to_char(created_at, 'YYYY-MM-DD HH24:MI') AS created_at"
            " FROM tickets ORDER BY created_at DESC"
        )
    return rows


def counts() -> dict:
    out = {}
    for table in ("clients", "accounts", "cards", "transactions", "commission_rules",
                  "kb_chunks", "tickets", "audit_log"):
        row = db.fetchone(f"SELECT count(*) AS n FROM {table}")
        out[table] = int(row["n"])
    return out


# ── Фикстуры eval-батареи: адресные изменения состояния перед прогоном ───

def admin_set_card_status(sub: str, mask: str, status: str) -> dict:
    row = db.fetchone(
        "update cards set status = %s where sub = %s and mask = %s returning mask, status",
        (status, sub, mask),
    )
    return row or {"error": "not_found"}


def admin_set_limit(sub: str, mask: str, period: str, amount: float) -> dict:
    column = {"daily": "daily_limit", "monthly": "monthly_limit"}.get(period)
    if column is None:
        return {"error": "unknown_period"}
    row = db.fetchone(
        f"update card_limits l set {column} = %s from cards c"
        " where c.card_id = l.card_id and c.sub = %s and c.mask = %s"
        " returning c.mask, l." + column + " as value",
        (amount, sub, mask),
    )
    return row or {"error": "not_found"}


def admin_set_contacts(sub: str, phone: str | None = None, email: str | None = None) -> dict:
    return set_contacts(sub, phone=phone, email=email)


def apply_fixtures(sub: str, fixtures: dict) -> dict:
    """Применить блок fixtures сценария: cards/limits/contacts."""
    applied = {}
    for card in fixtures.get("cards", []):
        applied[f"card:{card['mask']}"] = admin_set_card_status(sub, str(card["mask"]), card["status"])
    for limit in fixtures.get("limits", []):
        applied[f"limit:{limit['mask']}:{limit['period']}"] = admin_set_limit(
            sub, str(limit["mask"]), limit["period"], float(limit["amount"])
        )
    if "contacts" in fixtures:
        contacts = fixtures["contacts"]
        applied["contacts"] = admin_set_contacts(sub, contacts.get("phone"), contacts.get("email"))
    return applied
