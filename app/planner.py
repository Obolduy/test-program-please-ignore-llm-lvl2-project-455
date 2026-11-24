"""Планировщик: план запусков доменных агентов до всякого исполнения.

План — структурированный выход LLM, но исполнению он не принадлежит:
гейт оркестратора оставляет только шаги известных агентов. Действия
(блокировка карты, лимит, контакты, обращение) идут через карточку
подтверждения и политику прав, а не напрямую.
"""
from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

READ_AGENTS = ("data", "ops", "kb")
ACTION_AGENTS = ("actions", "tickets")


class Step(BaseModel):
    """Один шаг плана — запуск одного доменного агента."""

    agent: str = Field(
        validation_alias=AliasChoices("agent", "agentName"),
        description="id агента из каталога: data, ops или kb",
    )
    task: str = Field(
        validation_alias=AliasChoices("task", "job"),
        description="задача агенту одним предложением по-русски, с точными значениями",
    )
    why: str = Field(description="зачем этот шаг — одна короткая фраза")
    action: bool = Field(description="true, только если шаг изменит данные")


class Plan(BaseModel):
    """План обработки сообщения клиента."""

    # Полей со значением по умолчанию в схеме нет: Yandex AI Studio отвечает
    # 400 «all fields must be required» на схему с необязательными полями.
    steps: list[Step] = Field(description="шаги в порядке выполнения")
    question: str | None = Field(
        description="уточняющий вопрос клиенту, если данных для плана мало, иначе null"
    )


PLANNER_PROMPT = """\
Ты — планировщик ассистента банка. Ты не общаешься с клиентом: по его
сообщению составляй план — каких доменных агентов запустить и с какими
задачами. Отвечай строго схемой Plan, без текста вокруг.

Каталог агентов:
- data (агент данных клиента) — счета, балансы, карты, лимиты, контакты.
  Инструменты: get_my_overview, get_my_cards, get_card_limits,
  get_my_contacts.
- ops (агент операций) — история операций, поиск операций, правила
  комиссий. Инструменты: list_recent_transactions, find_transactions,
  get_transaction, list_commission_rules.
- kb (агент базы знаний) — условия продуктов, правила комиссий и
  лимитов, FAQ, безопасность. Инструмент: search_kb; в докладе указывает
  источник (source).
- actions (агент действий) — блокировка карты, изменение лимита
  переводов, обновление контактов. Просьба что-то из этого сделать —
  это он, с action=true: подготовка вернёт карточку подтверждения.
- tickets (агент эскалации) — обращение живому оператору поддержки для
  запросов вне тем банка (закрытие счёта, жалобы, спорные операции на
  крупную сумму). Действие, action=true.

Правила планирования:
- вопрос о СОСТОЯНИИ денег клиента (баланс, какие карты, какой лимит,
  какие контакты) — один шаг data, даже если вопросов несколько: агент
  сам вызовет нужные инструменты;
- вопрос об ОПЕРАЦИЯХ — всегда шаг ops, не data: «найди операцию»,
  «история операций», «операции за месяц», «самая крупная трата»,
  «сколько потратил», «где списание N рублей» — это ops;
  «почему списание/комиссия» — оба шага: ops (найти операцию и правило
  комиссии) и kb (правила);
- вопрос о правилах и условиях — шаг kb;
- просьба заблокировать карту, изменить лимит, обновить телефон или
  почту — шаг actions с action=true, и в task прямо напиши команду с
  деталями («заблокировать карту •4417», «поставить месячный лимит
  200000 на •4417»); вопрос «как заблокировать» или «что будет после
  блокировки» — шаг kb, без action;
- если у клиента несколько карт, а в просьбе карта не указана —
  не угадывай: question с просьбой назвать карту (маска — последние
  4 цифры);
- запрос вне тем банка — шаг tickets с action=true;
- клиент прямо просит оформить обращение или позвать оператора — шаг
  tickets немедленно, без уточняющих вопросов: недостающие детали
  клиент изложит оператору;
- данных для плана не хватает — question и пустые steps;
- агенты не нужны (приветствие) — пустые steps.
"""


def build_planner():
    """Планировщик — агент со структурированным выходом Plan."""
    from app import llm

    return llm.build_agent("планировщик", PLANNER_PROMPT, output_type=Plan)


def gate(plan: Plan) -> tuple[Plan, list[str]]:
    """Гейт плана: read-агенты проходят без действий, действия — только
    агент действий и эскалация.

    У read-агентов действий нет по конструкции: пометку action гейт
    снимает (модель любит её ставить щедро). Неизвестные агенты —
    отбрасываются; причина попадает в трейс хода, не в исполнение.
    """
    clean, dropped = [], []
    for step in plan.steps:
        if step.agent in ACTION_AGENTS:
            clean.append(step)
        elif step.agent in READ_AGENTS:
            if step.action:
                step = step.model_copy(update={"action": False})
            clean.append(step)
        else:
            dropped.append(f"{step.agent}: агент вне каталога")
    return Plan(steps=clean, question=plan.question), dropped


async def next_plan(planner, history: list, message: str, trace_id: str = ""):
    """Один вызов планировщика → (Plan, история следующего хода).

    Провайдера выбирает inference — как и для всех вызовов хода:
    sticky по trace_id держит ход на одной модели.
    """
    from app.llm import run_logged

    run_input = [*history, {"role": "user", "content": message}] if history else message
    result = await run_logged(planner, run_input, trace_id=trace_id)
    return result.final_output, result.to_input_list()
