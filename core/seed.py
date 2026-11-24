"""Сид демо-данных в Postgres: пользователи, клиенты, счета, карты,
лимиты, операции, комиссии, обращения, база знаний (статьи + векторы
из снапшота).

    python -m core.seed

Пересоздаёт доменные таблицы целиком (идемпотентно): удобно и для
первого запуска, и для `make seed` в любой момент.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from core import db
from core.config import ROOT

DEMO_FILE = ROOT / "data" / "demo.yaml"
SNAPSHOT_FILE = ROOT / "kb" / "vectors.json"


def _vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def seed() -> dict:
    demo = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))

    with db.connect() as conn, conn.cursor() as cur:
        # домен банка: чистая перезаливка; контурные таблицы сессий,
        # карточек и идемпотентности тоже сбрасываются — reset = исходное демо
        for table in (
            "transactions", "card_limits", "cards", "accounts",
            "commission_rules", "tickets", "clients", "kb_chunks",
            "idempotency", "pending_cards", "sessions",
            "users",
        ):
            cur.execute(f"delete from {table}")

        # все пользователи контура (включая оператора без клиентской записи)
        for user in _users():
            cur.execute(
                "insert into users(login, sub, name, role, scopes)"
                " values (%s, %s, %s, %s, %s)"
                " on conflict (login) do update set sub = excluded.sub,"
                " role = excluded.role, scopes = excluded.scopes",
                (user["login"], user["sub"], user["name"], user["role"], user["scopes"]),
            )

        for c in demo["clients"]:
            cur.execute(
                "insert into clients(sub, login, name, phone, email)"
                " values (%s, %s, %s, %s, %s)",
                (c["sub"], c["login"], c["name"], c.get("phone"), c.get("email")),
            )

        for a in demo["accounts"]:
            cur.execute(
                "insert into accounts(account_id, sub, kind, title, balance)"
                " values (%s, %s, %s, %s, %s)",
                (a["account_id"], a["sub"], a["kind"], a["title"], a["balance"]),
            )

        for card in demo["cards"]:
            cur.execute(
                "insert into cards(card_id, sub, account_id, mask, product, status, expires)"
                " values (%s, %s, %s, %s, %s, %s, %s)",
                (card["card_id"], card["sub"], card["account_id"], str(card["mask"]),
                 card["product"], card["status"], card["expires"]),
            )

        for lim in demo["card_limits"]:
            cur.execute(
                "insert into card_limits(card_id, daily_limit, monthly_limit)"
                " values (%s, %s, %s)",
                (lim["card_id"], lim["daily_limit"], lim["monthly_limit"]),
            )

        for t in demo["transactions"]:
            cur.execute(
                "insert into transactions(txn_id, sub, account_id, ts, amount, direction,"
                " merchant, category, description)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (t["txn_id"], t["sub"], t["account_id"], t["ts"], t["amount"],
                 t["direction"], t["merchant"], t["category"], t.get("description", "")),
            )

        for r in demo["commission_rules"]:
            cur.execute(
                "insert into commission_rules(rule_id, product, code, description, amount_rub)"
                " values (%s, %s, %s, %s, %s)",
                (r["rule_id"], r["product"], r["code"], r["description"], r.get("amount_rub")),
            )

        for tk in demo.get("tickets", []):
            cur.execute(
                "insert into tickets(id, sub, subject, note, status)"
                " values (%s, %s, %s, %s, %s)",
                (tk["id"], tk["sub"], tk["subject"], tk["note"], tk["status"]),
            )

        # база знаний: статьи + векторы из снапшота
        articles = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        for a in articles:
            cur.execute(
                "insert into kb_chunks(source, title, body, embedding)"
                " values (%s, %s, %s, %s::vector)",
                (a["source"], a["title"], a["body"], _vector_literal(a["vector"])),
            )

        conn.commit()

    return {
        "users": len(demo["clients"]),
        "accounts": len(demo["accounts"]),
        "transactions": len(demo["transactions"]),
        "kb_chunks": len(articles),
    }


def _users() -> list[dict]:
    from core.config import USERS

    return [
        {"login": u.login, "sub": u.sub, "name": u.name, "role": u.role, "scopes": u.scopes}
        for u in USERS.values()
    ]


if __name__ == "__main__":
    print("seed:", seed())
