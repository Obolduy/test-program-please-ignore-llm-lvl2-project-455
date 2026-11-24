"""Модельный слой: всё, что решает, КАК и ГДЕ исполняется вызов модели.

Оркестратор не знает про провайдеров: он строит агентов и запускает их,
а выбор «кто обслужит этот ход» делает inference:

    inference.run(agent, run_input, trace_id=..., agent_id=...)
        → (результат Runner, фактический провайдер)

Пакет собирается по шагам проекта:

  providers  — пул провайдеров из .env (облако + локаль), failover
  guided     — структурированный выход для локальной модели
  router     — классификатор трека на малой модели (sticky по trace_id)
  anonymizer — маскирование PII перед облачным провайдером
  degrade    — контролируемая деградация при отказе провайдеров
"""
from inference.providers import (  # noqa: F401
    PROVIDERS,
    Provider,
    enabled,
    resolve,
    route_turn,
    run,
    setup_pool,
    version_of,
)
