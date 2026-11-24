"""Аудит: append-only журнал в Postgres — основа разбора инцидентов.

Каждый исход контура — отдельная строка: кто, что, когда, с какими
аргументами, чем кончилось, состояние до и после, idempotency key,
сквозной trace_id хода. Виды исходов фиксированы (OUTCOMES): в журнал
попадают и неуспешные попытки — по ним восстанавливается «кто пытался
и что».

Журнал не редактируется: у модуля нет update/delete — только append
и чтение.
"""
from __future__ import annotations

import json
from typing import Any

from core import db

OUTCOMES = (
    "executed",
    "duplicate_suppressed",
    "rejected_by_user",
    "rejected_by_contract",
    "expired",
    "escalated",
    "denied",               # отказ в допуске: причина в detail
    "guardrail_unsafe",     # вход или выход признан небезопасным
    "guardrail_degraded",   # guardrail недоступен, решение оркестратора в detail
    "token_refreshed",      # 401 → перевыпуск токена, повтор
    "server_limit",         # лимит бизнес-системы (daily_limit)
    "error",
)


def append(
    who: str,
    what: str,
    outcome: str,
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    args: dict | None = None,
    before: Any = None,
    after: Any = None,
    idempotency_key: str | None = None,
    detail: str | None = None,
) -> None:
    db.execute(
        "insert into audit_log(trace_id, session_id, who, what, outcome, args, before, after,"
        " idempotency_key, detail) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            trace_id, session_id, who, what, outcome,
            _json(args), _json(before), _json(after),
            idempotency_key, detail,
        ),
    )


def tail(n: int = 20, who: str | None = None) -> list[dict]:
    sql = "select * from audit_log"
    params: tuple = ()
    if who:
        sql += " where who = %s"
        params = (who,)
    sql += " order by id desc limit %s"
    return db.fetchall(sql, (*params, n))


def trace(trace_id: str) -> list[dict]:
    """Все записи одного хода в хронологическом порядке."""
    return db.fetchall("select * from audit_log where trace_id = %s order by id", (trace_id,))


def _json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)
