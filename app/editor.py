"""Редактор: собирает финальный ответ клиенту из докладов агентов.

Единственный агент, чей текст уходит пользователю: доклады агентов —
его факты, источники базы знаний обязательны, выдумывать нельзя.
"""
from __future__ import annotations

from app import llm
from app.workers import CATALOG

EDITOR_PROMPT = """\
Ты — редактор ассистента банка. Ты получаешь сообщение клиента и
доклады доменных агентов. Напиши ответ клиенту: кратко, по-русски,
только на фактах докладов. Источник из базы знаний указывай текстом
(источник: <source>). Ничего не выдумывай. Если докладов нет — ответь
на сообщение сам, без инструментов.
"""


def build_editor(session):
    return llm.build_agent(f"редактор ({session.login})", EDITOR_PROMPT)


def editor_task(message: str, runs: list[dict]) -> str:
    lines = [f"Сообщение клиента: {message}"]
    if runs:
        for i, run in enumerate(runs, 1):
            title = CATALOG.get(run["agent"], {}).get("title", run["agent"])
            lines.append(f"{i}. {title}: {run['report']}")
    else:
        lines.append("Доклады агентов: нет — агенты не запускались.")
    lines.append("Напиши ответ клиенту на его сообщение по фактам докладов.")
    return "\n".join(lines)
