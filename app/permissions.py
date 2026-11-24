"""Права сессии: allowlist из claims, два барьера оркестратора.

Три функции — ядро контура прав:

  allowlist_from_claims  модель прав «субъект × операция × объект»:
                         роль задаёт набор, scope токена его сужает —
                         причём у каждого действия свой scope
  visible_tools          барьер 1: модель видит только разрешённое —
                         фильтрация набора до LLM
  admit                  барьер 2: допуск «субъект × операция × объект»
                         в момент вызова, до ухода на сервер

Имена операций контура:

  read:   get_my_overview, get_my_cards, get_card_limits, get_my_contacts,
          list_recent_transactions, find_transactions, get_transaction,
          list_commission_rules, search_kb
  write:  prepare_block_card        (cards:write)
          prepare_set_transfer_limit (limits:write)
          prepare_contacts_update   (contacts:write)   — карточка; исполняет PDP
  особые: get_client_overview       (только operator; чужой
          идентификатор легитимен — роль проверит сервер)
          create_ticket             (эскалация, без карточки)
  не LLM: execute_action            (код оркестратора после «да»;
          admit его не пропускает никогда)
"""
from __future__ import annotations

from dataclasses import dataclass, field

READ_OPS = (
    "get_my_overview", "get_my_cards", "get_card_limits", "get_my_contacts",
    "list_recent_transactions", "find_transactions", "get_transaction",
    "list_commission_rules", "search_kb",
)

# Каждому действию — свой scope: право «менять карту» не даёт права
# менять контакты. Политика PDP проверяет ту же пару действие×scope.
WRITE_SCOPE = {
    "prepare_block_card": "cards:write",
    "prepare_set_transfer_limit": "limits:write",
    "prepare_contacts_update": "contacts:write",
}
WRITE_OPS = tuple(WRITE_SCOPE)

OPERATOR_OPS = ("get_client_overview",)
ESCALATION_OPS = ("create_ticket",)

# Ключи, в которых идентичность не передаётся никогда: субъект живёт в
# токене вызова. Такой ключ в аргументах — попытка взять объект из
# аргументов, барьер 2 её отклоняет. Маска карты и txn_id — не
# идентичность, а выбор объекта среди СВОИХ: их сервер фильтрует по
# субъекту сам.
IDENTITY_ARGS = ("client_id", "user_id", "sub", "account", "account_id", "subscriber", "msisdn")


@dataclass
class Allowlist:
    sub: str
    role: str
    scopes: list[str] = field(default_factory=list)
    read_ops: list[str] = field(default_factory=list)
    write_ops: list[str] = field(default_factory=list)


def allowlist_from_claims(claims: dict) -> Allowlist:
    """Собрать allowlist сессии из проверенных claims токена.

    Роль даёт базовый набор; scope сужает его: каждое действие требует
    свой scope. Оператору доступны operator-инструменты и недоступны
    write-операции — предложение без исполнения. Эскалация
    (create_ticket) есть у всех: позвать человека можно всегда, аудит
    запишет.
    """
    role = claims.get("role", "")
    scopes = claims.get("scope", "").split()

    allow = Allowlist(sub=claims["sub"], role=role, scopes=scopes)
    allow.read_ops = list(READ_OPS)
    if role == "operator":
        allow.read_ops += list(OPERATOR_OPS)
    if role == "client":
        allow.write_ops = [
            op for op, scope in WRITE_SCOPE.items() if scope in scopes
        ]
    allow.read_ops += list(ESCALATION_OPS)
    return allow


def visible_tools(allowlist: Allowlist, all_tool_names: list[str]) -> list[str]:
    """Барьер 1: какие инструменты показываются модели этой сессии.

    Исполнение (execute_action) не видно никому: его вызывает код
    оркестратора после «да».
    """
    allowed = set(allowlist.read_ops) | set(allowlist.write_ops)
    return [name for name in all_tool_names if name in allowed]


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


def admit(allowlist: Allowlist, operation: str, args: dict | None = None) -> Decision:
    """Барьер 2: допуск операции в момент вызова, до ухода на сервер.

    Три проверки:
      операция  — имя в read_ops/write_ops allowlist сессии?
      объект    — в аргументах нет идентичности (её несёт токен)
      лимиты    — на write-пути решает политика (PDP), здесь не дублируется
    Несоответствие — вызов не уходит вообще; причина вернётся модели
    как tool-результат и запишется в аудит.
    """
    args = args or {}
    allowed_ops = set(allowlist.read_ops) | set(allowlist.write_ops)
    if operation not in allowed_ops:
        return Decision(False, f"operation_not_allowed:{operation}")

    if operation not in OPERATOR_OPS and operation not in ESCALATION_OPS:
        bad = sorted(k for k in args if k in IDENTITY_ARGS)
        if bad:
            return Decision(False, f"object_from_arguments:{','.join(bad)}")

    return Decision(True)
