"""Backend + оркестратор: HTTP-граница чата.

    uvicorn app.main:app --host 127.0.0.1 --port 8000

  POST /api/session                      {"user": "anna"} → открыть сессию
  POST /api/chat                         {"session_id", "message"} → SSE-поток хода
  POST /api/cards/{pending_id}/decision  подтверждение карточки (с контрактов)

События SSE хода: plan / card / reply / error / done.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app import orchestrator, permissions, sessions
from core import config


@asynccontextmanager
async def lifespan(app: FastAPI):
    from inference import setup_pool

    providers = setup_pool()  # пул провайдеров из .env (порядок LLM_PROVIDER_ORDER)
    config.MODEL_NAME = providers[0].model  # модель по умолчанию для сборки агентов
    yield


app = FastAPI(title="bank-chat-ai", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo: чат-фронтенд живёт на отдельном порту стенда
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "backend"}


@app.post("/api/session")
async def create_session(body: dict) -> dict:
    login = body.get("user", "")
    user = config.USERS.get(login)
    if user is None:
        raise HTTPException(status_code=400, detail=f"неизвестный пользователь: {login}")
    session = await sessions.open_session(login)
    visible = permissions.visible_tools(session.allowlist, session.pool.tool_names())
    return {
        "session_id": session.session_id,
        "name": user.name,
        "role": user.role,
        "visible_tools": sorted(visible),
    }


@app.post("/api/cards/{pending_id}/decision")
async def decide_card(pending_id: str, body: dict) -> dict:
    """Кнопка карточки: «да» исполняет, «нет» отменяет (детерминированный код)."""
    from app import cards

    session = sessions.get(body.get("session_id", ""))
    if session is None:
        raise HTTPException(status_code=404, detail="сессия не найдена")
    card = cards.active(session)
    if card is None or card.pending_id != pending_id:
        return {"outcome": "no_card", "text": "Карточка не найдена или истекла — попросите заново."}
    answer = body.get("answer")
    if answer == "yes":
        return {"outcome": "confirmed", "text": await orchestrator.execute_pending(session, card)}
    if answer == "no":
        cards.settle(session, card, "rejected")
        return {"outcome": "rejected", "text": "Хорошо, отменяю. Ничего не изменено."}
    raise HTTPException(status_code=400, detail="answer: yes|no")


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    session = sessions.get(body.get("session_id", ""))
    if session is None:
        raise HTTPException(status_code=404, detail="сессия не найдена — откройте новую")
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="пустое сообщение")

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: dict) -> None:
        # trace_id хода в каждом событии: eval-раннер связывает ожидания
        # сценария с tool_calls/audit конкретного прогона
        event["trace_id"] = session.trace_id
        await queue.put(event)

    async def run() -> None:
        try:
            await orchestrator.run_turn(session, message, emit)
        except Exception as e:  # noqa: BLE001 — сбой хода уходит событием, не падает поток
            await emit({"type": "error", "detail": str(e)[:200]})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def stream():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")
