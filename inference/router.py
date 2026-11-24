"""Routing-классификатор трека запроса.

TODO(малые модели): classify(message) → simple_read | kb | action | complex
  rule-режим: таблица правил (приоритет action > kb > complex-маркеры;
  simple_read по умолчанию); ROUTING_MODE=off|rule|prompt.
async classify_with_model(message) → трек: промпт-режим на малой модели
  через inference.guided со схемой {track}; ошибка модели → classify().
  Точность — expect.track сценариев батареи (routing_accuracy в прогоне,
  раннер зовёт classify_with_model при ROUTING_MODE=prompt).

Трек → провайдер приклеивает inference.providers.route_turn, его вы
дописываете в том же шаге.
"""
from __future__ import annotations

TRACKS = ("simple_read", "kb", "action", "complex")


def classify(message: str) -> str:
    raise NotImplementedError("TODO(малые модели)")
