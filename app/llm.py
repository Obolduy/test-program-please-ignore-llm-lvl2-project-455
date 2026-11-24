"""Сборка агентов на модели из inference-пула + учёт LLM-вызовов сессии.

output_type подключается нестрогой схемой (AgentOutputSchema со
strict_json_schema=False): строгий режим JSON-схем слишком требователен
к локальным и небольшим моделям.

Кто обслужил вызов — решает inference (inference.run): фактический
провайдер и версия модельного слоя пишутся в llm_calls рядом с моделью
и токенами. Отсюда начинаются контроль рантайма и экономика: «та ли
версия отвечает» и «сколько стоит сценарий» считаются по этим строкам.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from agents import Agent, AgentOutputSchema, ModelSettings

from core import config


def build_agent(name: str, instructions: str, tools: list | None = None, output_type=None):
    """Агент на модели из пула inference; output_type — pydantic-схема или None."""
    schema = AgentOutputSchema(output_type, strict_json_schema=False) if output_type else None
    return Agent(
        name=name,
        instructions=instructions,
        tools=list(tools or []),
        output_type=schema,
        model=config.MODEL_NAME,
        model_settings=ModelSettings(temperature=config.LLM_TEMPERATURE),
    )


def _attr(raw, key):
    return raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)


_CAPTURE_SEQ = 0


def _slug(text: str) -> str:
    keep = re.sub(r"[^a-zа-я0-9]+", "-", (text or "").lower()).strip("-")
    return keep[:40] or "call"


def _capture(trace_id, agent_label, provider, task, result, latency_ms, usage) -> None:
    """Дамп вызова в LLM_CAPTURE_DIR для датасета дообучения (шаг дистилляции).

    llm_calls хранит счётчики, но не тексты: захват закрывает эту дыру на
    время сборки датасета. Ручка выключена по умолчанию и всегда безопасна
    для хода — любая ошибка записи молча пропускается.
    """
    out = os.environ.get("LLM_CAPTURE_DIR", "")
    if not out:
        return
    global _CAPTURE_SEQ
    try:
        capture_dir = Path(out)
        capture_dir.mkdir(parents=True, exist_ok=True)
        _CAPTURE_SEQ += 1
        final = result.final_output
        dump = getattr(final, "model_dump", None)
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "trace_id": trace_id,
            "agent": agent_label,
            "provider": provider.name,
            "model": provider.model,
            "input": task,
            "thread": result.to_input_list(),
            "final_output": dump() if dump and not isinstance(final, str) else final,
            "input_tokens": usage.input_tokens or 0,
            "output_tokens": usage.output_tokens or 0,
            "latency_ms": latency_ms,
        }
        name = f"{_CAPTURE_SEQ:04d}_{trace_id or 'notrace'}_{_slug(agent_label)}.json"
        (capture_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:  # noqa: BLE001 — захват не может уронить ход
        pass


async def run_logged(agent, task, *, trace_id: str = ""):
    """Вызов агента через inference с записью в llm_calls → result.

    Провайдера выбирает inference (пул + sticky по trace_id); каждый
    вызов пишется в llm_calls (провайдер, версия, модель, токены,
    латентность) — фундамент стоимости сценария и разбора инцидентов.
    Через неё идут и доменные агенты, и планировщик.
    """
    import time

    from core import db

    from inference import run, version_of

    t0 = time.perf_counter()
    result, provider = await run(agent, task, trace_id=trace_id, agent_id=agent.name)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    usage = result.context_wrapper.usage
    _capture(trace_id, f"{provider.name}:{agent.name}", provider, task, result, latency_ms, usage)
    db.execute(
        "insert into llm_calls(trace_id, agent, model, provider, model_version,"
        " input_tokens, output_tokens, latency_ms)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            trace_id or None, f"{provider.name}:{agent.name}", provider.model,
            provider.name, version_of(provider),
            usage.input_tokens or 0, usage.output_tokens or 0, latency_ms,
        ),
    )
    return result


async def run_agent(agent, task: str, *, trace_id: str = "") -> dict:
    """Один запуск агента: доклад + его tool-вызовы для трейса.

    Возвращает {"report", "tools": [{"tool", "args", "result"}], "seconds"}.
    """
    import time

    t0 = time.perf_counter()
    result = await run_logged(agent, task, trace_id=trace_id)

    outputs = {}
    for item in result.new_items:
        if item.type == "tool_call_output_item":
            outputs[_attr(item.raw_item, "call_id")] = item.output

    tools = []
    for item in result.new_items:
        if item.type != "tool_call_item":
            continue
        call_id = _attr(item.raw_item, "call_id")
        args = _attr(item.raw_item, "arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except (TypeError, ValueError):
                args = {"raw": args[:80]}
        output = outputs.get(call_id)
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (TypeError, ValueError):
                pass
        tools.append(
            {
                "tool": item.tool_name or _attr(item.raw_item, "name"),
                "args": args or {},
                "result": output,
            }
        )

    report = result.final_output
    if not isinstance(report, str):
        report = json.dumps(report, ensure_ascii=False, default=str)
    return {"report": report, "tools": tools, "seconds": time.perf_counter() - t0}
