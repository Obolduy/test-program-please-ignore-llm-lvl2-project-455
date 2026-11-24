"""Контракты действий: схемы, нормализация, таксономия ошибок.

Действие — это интерфейс, а не функция: вход нормализуется (карту можно
назвать как угодно — «4417», «•4417», «карту на 4417»; сумму — «200 000»
или «200 тыс»), предусловия проверяются до похода в бизнес-систему,
ошибка возвращается машинно-читаемым кодом с подсказками — модель
исправляет свой вызов, а не роняет сессию.

Три класса исходов:
  ошибка валидации    — код + подсказки (available/matches): модель
                        исправляет аргументы и повторяет вызов;
  бизнес-правило      — честный отказ (card_already_blocked, same_limit,
                        same_contacts): повторять бессмысленно;
  инфраструктура      — не возвращается модели вообще: транспортный сбой
                        прячет оркестратор за одним повтором.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

# Коды, которые не имеет смысла ретраить: отказ по правилам системы
BUSINESS_RULE_CODES = frozenset(
    {"card_already_blocked", "same_limit", "same_contacts"}
)


class ActionError(Exception):
    """Отказ действия: код + человеческое сообщение + подсказки модели."""

    def __init__(self, code: str, message: str = "", **hints) -> None:
        self.code = code
        self.message = message or code
        self.hints = hints
        super().__init__(self.message)

    def to_tool_result(self) -> dict:
        result = {"error": self.code, "detail": self.message}
        result.update(self.hints)
        return result


class PrepareBlockCard(BaseModel):
    """Вход подготовки блокировки карты (исполнение — отдельный шаг)."""

    mask: str = Field(description="последние 4 цифры карты, как их назвал пользователь")


class PrepareSetLimit(BaseModel):
    """Вход подготовки изменения лимита переводов."""

    mask: str = Field(description="последние 4 цифры карты")
    period: str = Field(description="период лимита: дневной или месячный")
    amount: int = Field(description="новая сумма лимита в рублях, целым числом без пробелов")


class PrepareContacts(BaseModel):
    """Вход подготовки обновления контактных данных."""

    phone: str | None = Field(default=None, description="новый телефон или null")
    email: str | None = Field(default=None, description="новая почта или null")


def normalize_mask(raw: str, cards: list[dict]) -> dict:
    """Найти карту клиента по маске как попало → запись карты.

    Ошибка unknown_card несёт available: модель предложит пользователю
    выбрать из своих карт, вместо того чтобы выдумывать маску.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) != 4:
        raise ActionError(
            "unknown_card",
            f"маска «{raw}» не похожа на последние 4 цифры карты",
            matches="•4417 — только цифры, 4 знака",
            available=[{"mask": c["mask"], "product": c["product"], "status": c["status"]} for c in cards],
        )
    for card in cards:
        if str(card["mask"]) == digits:
            return card
    raise ActionError(
        "unknown_card",
        f"карты с маской «{digits}» нет среди карт клиента",
        available=[{"mask": c["mask"], "product": c["product"], "status": c["status"]} for c in cards],
    )


PERIOD_WORDS = {
    "daily": ("день", "дневной", "днев", "сутки", "сут", "daily", "в день", "за день"),
    "monthly": ("месяц", "месячный", "месячн", "мес", "monthly", "в месяц", "за месяц"),
}


def normalize_period(raw: str) -> str:
    """«дневной», «в день», «daily» → daily; «месячный», «в месяц» → monthly."""
    lowered = (raw or "").strip().lower()
    for period, words in PERIOD_WORDS.items():
        if any(word in lowered for word in words):
            return period
    raise ActionError(
        "unknown_period",
        f"период «{raw}» не распознан",
        available=["daily (дневной)", "monthly (месячный)"],
    )


def normalize_amount(raw: int | str | None) -> int:
    """Сумма как попало → целые рубли: «200 000» → 200000, «200 тыс» → 200000.

    Модель по схеме передаёт число; строка принимается на всякий случай:
    пользователи говорят «двести тысяч», и модель иногда перетаскивает
    формулировку в аргументы целиком.
    """
    if raw is None:
        raise ActionError("bad_amount", "не передана сумма", matches="200000")
    if isinstance(raw, bool):
        raise ActionError("bad_amount", "сумма — число рублей", matches="200000")
    if isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip().lower().replace(",", ".")
        multiplier = 1
        if "млн" in text:
            multiplier, text = 1_000_000, text.replace("млн", "")
        elif "тыс" in text or "т.р" in text or "т.р." in text:
            multiplier, text = 1_000, text.replace("тыс", "").replace("т.р", "")
        digits = re.sub(r"[^\d.]", "", text)
        try:
            value = int(float(digits) * multiplier)
        except (TypeError, ValueError):
            raise ActionError("bad_amount", f"сумму «{raw}» не удалось разобрать", matches="200000")
    if value <= 0:
        raise ActionError("bad_amount", "сумма должна быть положительной", matches="200000")
    return value


def normalize_phone(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 11:
        raise ActionError(
            "bad_phone", "телефон — 11 цифр", matches="+7 900 123-45-67"
        )
    return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"


def normalize_email(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    value = raw.strip()
    if "@" not in value or "." not in value.split("@")[-1]:
        raise ActionError("bad_email", "почта без @ или домена", matches="name@example.ru")
    return value
