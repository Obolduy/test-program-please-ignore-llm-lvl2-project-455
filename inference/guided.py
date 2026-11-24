"""Guided decoding: структурированный выход для локальной модели.

TODO(guided): guided_chat(messages, schema) → dict
  один вызов локального провайдера с response_format json_schema
  (все четыре стека поддерживают: llama-server, LM Studio, Ollama,
  vLLM); 400 от сервера (стек не понял schema) → фолбэк «просто верни
  JSON» с parse-проверкой; конфиг — LLM_LOCAL_BASE_URL/MODEL/STACK,
  стек: явное значение или авто-детект по порту (11434 → ollama,
  1234 → lmstudio, 8000 → vllm, прочее → llama.cpp).
  Smoke 8 вызывает и два помощника guided_chat.
  _client_config() → (base_url, api_key, stack) из LLM_LOCAL_*.
  _loads(content) → dict снимает забор ```json и бросает ValueError
  на ответе, который не разбирается как JSON.

Проверка: python tests/smoke.py 8 (группа «guided»); в бою
guided_chat использует inference.router (промпт-режим) и repair-план
для невалидного Plan (подключение — app/planner.py, по желанию).
"""
from __future__ import annotations


def _client_config() -> tuple[str, str, str]:
    raise NotImplementedError("TODO(guided)")


def _loads(content: str) -> dict:
    raise NotImplementedError("TODO(guided)")


async def guided_chat(messages: list[dict], schema: dict) -> dict:
    raise NotImplementedError("TODO(guided)")
