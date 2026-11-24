"""Пул провайдеров модели: облако и локаль под одним контрактом.

    # .env: порядок пула и параметры каждого провайдера
    LLM_PROVIDER_ORDER=cloud,local
    LLM_CLOUD_BASE_URL=...   LLM_CLOUD_MODEL=...   LLM_CLOUD_API_KEY=...
    LLM_LOCAL_BASE_URL=...   LLM_LOCAL_MODEL=...   LLM_LOCAL_STACK=llama.cpp

TODO(провайдеры): реализуйте по контракту, который ждёт контур
(см. вызовы в app/llm.py, app/planner.py, app/main.py, tests/smoke.py):

  Provider(name, model, client) — cooldown_until, live(), cool_down(),
    describe() → {"name", "model"} для манифеста прогона батареи (evals/run_evals.py)
    ретраим только 429 / 5xx / таймаут / сетевую ошибку; 400/401/403 —
    ошибка вызова, провайдера не меняем; кулдаун LLM_FAILOVER_COOLDOWN_SEC.
  setup_pool() → список Provider по LLM_PROVIDER_ORDER из .env;
    провайдер без LLM_<ИМЯ>_* пропускается; пустой пул — SystemExit.
  resolve(trace_id, agent_id) → Provider: sticky по trace_id — ход
    живёт на одной модели; без решения — первый живой.
  run(agent, run_input, *, trace_id, agent_id) → (результат Runner, Provider):
    OpenAIChatCompletionsModel(model=…, openai_client=…) на копии агента
    (dataclasses.replace), 429/5xx → следующий провайдер.
  route_turn(trace_id, message) → track: классификатор трека (малые модели)
    приклеивает ход к провайдеру; при ROUTING_MODE=off не решает.

Подсказки: клиент — AsyncOpenAI(base_url, api_key,
http_client=httpx.AsyncClient(trust_env=False, timeout=180)).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Provider:
    name: str
    model: str
    client: Any
    cooldown_until: float = 0.0


PROVIDERS: list[Provider] = []
TRACE_PROVIDERS: dict[str, str] = {}


def setup_pool() -> list[Provider]:
    raise NotImplementedError("TODO(провайдеры)")


def enabled() -> bool:
    raise NotImplementedError("TODO(провайдеры)")


def resolve(trace_id: str, agent_id: str = "") -> Provider:
    raise NotImplementedError("TODO(провайдеры)")


def route_turn(trace_id: str, message: str) -> str:
    raise NotImplementedError("TODO(малые модели): подключается к inference.router")


def version_of(provider: Provider) -> str:
    """Версия модельного слоя для провайдера (выпуск: из versions.json)."""
    try:
        data = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
        release = data.get("releases", {}).get(data.get("current", ""), {})
        if release.get("provider", {}).get("name") == provider.name:
            return data.get("current", "")
    except (OSError, ValueError):
        pass
    return ""


async def run(agent, run_input, *, trace_id: str = "", agent_id: str = "") -> tuple[Any, Provider]:
    raise NotImplementedError("TODO(провайдеры)")
