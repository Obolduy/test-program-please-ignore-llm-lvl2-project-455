"""Action-MCP: инструменты действий.

    uvicorn actions.server:app --host 127.0.0.1 --port 8220

Один MCP-сервер — OAuth 2.1 resource server: каждый вызов несёт Bearer,
сервер проверяет подпись/издателя/адресата сам (core/server_auth.py), а
субъект операции читает из проверенного токена (get_access_token()).
Идентификатора клиента в контрактах нет — подменить нечего:
структурная защита от IDOR.

Инструменты:

  prepare_block_card(mask)                  — карточка блокировки карты (для LLM)
  prepare_set_transfer_limit(mask, period,  — карточка лимита переводов (для LLM)
    amount)
  prepare_contacts_update(phone, email)     — карточка контактов (для LLM)
  execute_action(action, details_json,      — ИСПОЛНЕНИЕ: в видимом наборе
    pending_id, idempotency_key)              LLM его нет, вызывает только
                                              код оркестратора после «да»
  create_ticket(subject, note)              — обращение оператору (для LLM)

Субъект приходит в claims токена: попытка протащить чужой sub в
аргументах отсекается ещё барьером оркестратора, а сервер ищет карту
строго среди карт субъекта — двойная защита.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.routing import Mount

from actions import service as actions_service
from actions.contracts import ActionError
from core.config import ACTIONS_PORT, RESOURCES
from core.server_auth import make_mcp_server

ACTIONS = make_mcp_server(
    "actions",
    resource=RESOURCES["actions"],
    instructions=(
        "Действия клиента. prepare_* ничего не исполняет: возвращает "
        "карточку для подтверждения пользователем. Исполнение происходит "
        "вне агента — после явного «да». Субъект определяется токеном вызова."
    ),
)


def _subject() -> str:
    at = get_access_token()
    assert at is not None, "инструмент вызван без аутентифицированного токена"
    return at.subject


def _tool_error(e: ActionError) -> MCPError:
    """Ошибка действия — tool-результат, а не падение: код + подсказки."""
    raise MCPError(code=-32002, message=e.code, data=e.to_tool_result())


@ACTIONS.tool()
async def prepare_block_card(mask: str) -> dict:
    """Подготовить блокировку карты: проверяет предусловия и возвращает карточку.

    НИЧЕГО не исполняет: блокировка произойдёт только после явного «да»
    пользователя. mask — последние 4 цифры карты, как их назвал
    пользователь; если карт несколько и пользователь не уточнил —
    перечисли свои карты и спроси, какую блокировать.
    """
    try:
        return {"ok": True, **await actions_service.prepare_block_card(_subject(), mask)}
    except ActionError as e:
        _tool_error(e)


@ACTIONS.tool()
async def prepare_set_transfer_limit(mask: str, period: str, amount: int) -> dict:
    """Подготовить изменение лимита переводов по карте.

    НИЧЕГО не исполняет: изменение произойдёт только после явного «да».
    mask — последние 4 цифры карты; period — «daily» (дневной) или
    «monthly» (месячный); amount — новая сумма в рублях целым числом.
    """
    try:
        return {"ok": True, **await actions_service.prepare_set_transfer_limit(_subject(), mask, period, amount)}
    except ActionError as e:
        _tool_error(e)


@ACTIONS.tool()
async def prepare_contacts_update(phone: str | None = None, email: str | None = None) -> dict:
    """Подготовить обновление контактов (телефон и/или почта).

    НИЧЕГО не исполняет: обновление произойдёт только после явного «да»
    пользователя. Передай только то, что меняется.
    """
    try:
        return {"ok": True, **await actions_service.prepare_contacts_update(_subject(), phone, email)}
    except ActionError as e:
        _tool_error(e)


@ACTIONS.tool()
async def execute_action(action: str, details_json: str, pending_id: str, idempotency_key: str) -> dict:
    """ИСПОЛНИТЬ подтверждённое действие.

    Инструмент не для агента: вызывает код оркестратора после явного
    «да» пользователя по карточке pending_id. Идемпотентен по ключу:
    повтор с тем же ключом не исполняет операцию второй раз.
    """
    try:
        return await actions_service.execute_action(_subject(), action, details_json, idempotency_key)
    except ActionError as e:
        _tool_error(e)


@ACTIONS.tool()
async def create_ticket(subject: str, note: str) -> dict:
    """Создать обращение живому оператору поддержки (эскалация).

    subject — тема в несколько слов, note — суть обращения клиента.
    Возвращает номер тикета.
    """
    return await actions_service.create_ticket(_subject(), subject, note)


@asynccontextmanager
async def lifespan(app):
    manager = ACTIONS.session_manager.run()
    await manager.__aenter__()
    try:
        yield
    finally:
        await manager.__aexit__(None, None, None)


app = Starlette(
    routes=[Mount("/actions", app=ACTIONS.streamable_http_app())],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=ACTIONS_PORT, log_level="warning")
