"""Идемпотентность исполнения на стороне оркестратора.

Ключ — подтверждение (pending_id): одно «да» — одна операция, сколько
бы раз его ни доставили (двойной клик, ретрай сети, повторный «да»).
Неудачный исход не фиксируется: ключ не должен запирать повторную
попытку после сбоя.

Для блокировки карты и лимита дедупликация есть и на стороне
бизнес-системы — два барьера идемпотентности; для контактов этот слой
единственный.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core import db


@dataclass
class StoredResult:
    duplicate: bool
    result: Any


async def execute(key: str, sub: str, action: str, fn: Callable[[], Awaitable[dict]]) -> StoredResult:
    """Исполнить fn под ключом; повтор с тем же ключом вернёт сохранённое."""
    row = db.fetchone("select result from idempotency where key = %s", (key,))
    if row is not None and row["result"] is not None:
        return StoredResult(duplicate=True, result=row["result"])

    result = await fn()
    if result.get("outcome") in (None, "error", "bad_arguments") or result.get("error"):
        return StoredResult(duplicate=False, result=result)  # сбой — ключ не занимаем

    db.execute(
        "insert into idempotency(key, sub, action, result) values (%s, %s, %s, %s)"
        " on conflict (key) do nothing",
        (key, sub, action, json.dumps(result, ensure_ascii=False)),
    )
    return StoredResult(duplicate=False, result=result)
