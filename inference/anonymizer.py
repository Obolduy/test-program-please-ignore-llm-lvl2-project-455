"""PII-анонимизатор: маскирование на транспорте перед облаком.

TODO(малые модели): mask_text(text) → замаскированный текст; mask_messages(...)
  телефоны, почты, полные номера карт, номера счетов, имена клиентов
  (словарь из моков: services.store.clients_all_names()). Маску карты
  из последних четырёх цифр (•4417) не трогайте: по ней инструменты
  действий находят карту. Обёртка облачного
  клиента MaskedAsyncOpenAI(client) — маскирование в
  chat.completions.create, интерфейс как у AsyncOpenAI. Подключается
  в setup_pool() для провайдера cloud при ANONYMIZE_CLOUD != 0;
  ANONYMIZE_LOG=1 печатает замаскированный контекст в лог.
  Точность и полнота — python -m evals.pii_check по evals/pii_cases.json
  (кейсы собираете вы).
"""
from __future__ import annotations


def mask_text(text: str) -> str:
    raise NotImplementedError("TODO(малые модели)")


def mask_messages(messages: list[dict]) -> list[dict]:
    raise NotImplementedError("TODO(малые модели)")
