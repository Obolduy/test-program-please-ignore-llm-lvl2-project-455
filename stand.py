"""Стенд готового: все сервисы, которые студент НЕ пишет, одним процессом.

    python stand.py            # http://127.0.0.1:8100

Что внутри:

  /token, /jwks.json                demo-issuer (core/issuer.py)
  /accounts/mcp /transactions/mcp   read-MCP-серверы
  /kb/mcp                           (services/accounts|transactions|kb.py)
  /mock/...                         REST-моки банка (services/rest.py)
  /                                 чат-фронтенд (web/index.html) + страница оператора

Каждый read-MCP-сервер проверяет Bearer сам: 401 с WWW-Authenticate и
resource_metadata ставит middleware SDK по настройкам server_auth.

Перед запуском: make infra && make migrate && make seed.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from core import config
from core.issuer import issuer_routes
from services import accounts, kb, rest, transactions

WEB_DIR = Path(__file__).resolve().parent / "web"


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "stand"})


async def config_js(request: Request) -> Response:
    """Адрес бэкенда для веб-чата: BACKEND_PORT из .env, как у make api."""
    return Response(f"const BACKEND = '{config.BACKEND_URL}';\n", media_type="text/javascript")


@asynccontextmanager
async def lifespan(app):
    """Session managers обязаны жить в event loop сервера (uvicorn)."""
    managers = [s.session_manager.run() for s in (accounts.accounts, transactions.transactions, kb.kb)]
    for m in managers:
        await m.__aenter__()
    try:
        yield
    finally:
        for m in managers:
            await m.__aexit__(None, None, None)


def build_app() -> Starlette:
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/config.js", config_js, methods=["GET"]),
        *issuer_routes(),
        *rest.mock_routes(),
        Mount("/accounts", app=accounts.accounts.streamable_http_app()),
        Mount("/transactions", app=transactions.transactions.streamable_http_app()),
        Mount("/kb", app=kb.kb.streamable_http_app()),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


if __name__ == "__main__":
    uvicorn.run(
        build_app(),
        host="127.0.0.1",
        port=config.STAND_PORT,
        log_level="warning",
    )
