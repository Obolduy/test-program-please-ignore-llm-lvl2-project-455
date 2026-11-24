from __future__ import annotations

import dataclasses
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

ROOT = Path(__file__).resolve().parent.parent


def _cooldown_sec() -> float:
    return float(os.environ.get("LLM_FAILOVER_COOLDOWN_SEC", "60"))


def _retryable(error: Exception) -> bool:
    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return True
    return isinstance(error, APIStatusError) and (error.status_code == 429 or error.status_code >= 500)


@dataclass
class Provider:
    name: str
    model: str
    client: Any
    cooldown_until: float = 0.0

    def live(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def cool_down(self) -> None:
        self.cooldown_until = time.monotonic() + _cooldown_sec()

    def describe(self) -> dict:
        return {"name": self.name, "model": self.model}


PROVIDERS: list[Provider] = []
TRACE_PROVIDERS: dict[str, str] = {}


def _build(name: str) -> Provider | None:
    prefix = f"LLM_{name.upper()}_"
    base_url = os.environ.get(prefix + "BASE_URL", "")
    model = os.environ.get(prefix + "MODEL", "")
    if not base_url or not model:
        return None
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=os.environ.get(prefix + "API_KEY") or "none",
        http_client=httpx.AsyncClient(trust_env=False, timeout=180),
    )
    return Provider(name, model, client)


def setup_pool() -> list[Provider]:
    order = [n.strip() for n in os.environ.get("LLM_PROVIDER_ORDER", "cloud,local").split(",") if n.strip()]
    PROVIDERS.clear()
    for name in order:
        provider = _build(name)
        if provider is None:
            print(f"inference: провайдер {name} пропущен, нет LLM_{name.upper()}_BASE_URL или LLM_{name.upper()}_MODEL")
            continue
        PROVIDERS.append(provider)
    if not PROVIDERS:
        raise SystemExit("inference: не настроен ни один провайдер")
    return PROVIDERS


def enabled() -> bool:
    return bool(PROVIDERS)


def resolve(trace_id: str, agent_id: str = "") -> Provider:
    if len(TRACE_PROVIDERS) > 1000:
        TRACE_PROVIDERS.clear()
    pinned = TRACE_PROVIDERS.get(trace_id)
    for provider in PROVIDERS:
        if provider.name == pinned:
            return provider
    chosen = next((p for p in PROVIDERS if p.live()), PROVIDERS[0])
    if trace_id:
        TRACE_PROVIDERS[trace_id] = chosen.name
    return chosen


def route_turn(trace_id: str, message: str) -> str:
    raise NotImplementedError("TODO(малые модели): подключается к inference.router")


def version_of(provider: Provider) -> str:
    try:
        data = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
        release = data.get("releases", {}).get(data.get("current", ""), {})
        if release.get("provider", {}).get("name") == provider.name:
            return data.get("current", "")
    except (OSError, ValueError):
        pass
    return ""


async def run(agent, run_input, *, trace_id: str = "", agent_id: str = "") -> tuple[Any, Provider]:
    from agents import OpenAIChatCompletionsModel, Runner

    first = resolve(trace_id, agent_id)
    queue = [first] + [p for p in PROVIDERS if p is not first and p.live()]
    last_error: Exception | None = None
    for provider in queue:
        model = OpenAIChatCompletionsModel(model=provider.model, openai_client=provider.client)
        try:
            result = await Runner.run(dataclasses.replace(agent, model=model), run_input)
        except Exception as error:
            if not _retryable(error):
                raise
            provider.cool_down()
            last_error = error
            continue
        if trace_id:
            TRACE_PROVIDERS[trace_id] = provider.name
        return result, provider
    raise last_error or RuntimeError("пул провайдеров пуст")
