"""Контролируемая деградация: отказ провайдера — режим, а не ошибка.

TODO(гибрид): check(providers) → {"mode": ok|local_only|cloud_only|none}
  живость — дешёвая проба /v1/models с таймаутом ~3с, кэш на ~20с;
  notice_for(mode, track) → текст для клиента ('' — штатный маршрут);
  NONE_NOTICE — текст для режима none, его импортирует оркестратор.
  Крючок в app/orchestrator.py::_degrade_turn уже вызывает вас:
  режим none → тикет оператору через actions-MCP + аудит.
Проверка: python tests/smoke.py 11 (группа «гибрид») + демо отказов.
"""
from __future__ import annotations


def check(providers) -> dict:
    raise NotImplementedError("TODO(гибрид)")


def notice_for(mode: str, track: str = "") -> str:
    raise NotImplementedError("TODO(гибрид)")
