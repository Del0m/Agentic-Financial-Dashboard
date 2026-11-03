from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

# stream and cash-flow signals share a decision path, separate from category overspend facts (tpc-33)
SIGNAL_CAP = 3
RANK = {"low_balance": 0, "income_change": 1, "price_increase": 2, "new_subscription": 3}
NOT_CANCELLABLE = frozenset({"RENT_AND_UTILITIES", "LOAN_PAYMENTS", "MEDICAL", "GOVERNMENT_AND_NON_PROFIT",
                             "TRANSFER_IN", "TRANSFER_OUT", "SAVINGS_TRANSFER"})


@dataclass(frozen=True)
class Signal:
    kind: str
    key: str
    stream_id: Optional[int]
    category: Optional[str]
    numbers: dict[str, int | str | bool]


def rank_signals(signals: Sequence[Signal]) -> list[Signal]:
    return sorted(signals, key=lambda s: (RANK.get(s.kind, len(RANK)),
                                         -int(s.numbers.get("shortfall_cents", s.numbers.get("last_cents", 0))), s.key))


def allowed_actions(signal: Signal) -> tuple[str, ...]:
    if signal.kind in ("new_subscription", "price_increase") and signal.category and signal.category not in NOT_CANCELLABLE:
        return ("alert", "suggest_cancel")
    if signal.kind == "low_balance":
        return ("alert", "delay_spend", "move_from_savings")
    return ("alert",)


def choose(signal: Signal, proposed: str) -> str:
    if signal.kind == "low_balance":
        n = signal.numbers
        if n.get("assumed_no_income"):
            return "alert"
        if proposed == "move_from_savings" and not n.get("savings_cover"):
            return "alert"
        if proposed == "delay_spend" and not (n.get("has_budget") and n.get("flexible_ahead")):
            return "alert"
    return proposed if proposed in allowed_actions(signal) else "alert"


@dataclass(frozen=True)
class PreviousDip:
    date: date
    shortfall_cents: int
    days_ahead: int


def repeat_decision(previous: Optional[PreviousDip], dip_date: date, shortfall_cents: int, days_ahead: int) -> bool:
    if previous is None or dip_date < previous.date or (dip_date - previous.date).days > 3:
        return True
    return ((shortfall_cents * 4 >= previous.shortfall_cents * 5 and shortfall_cents - previous.shortfall_cents >= 2000)
            or (days_ahead <= 2 and previous.days_ahead >= 3))


def low_balance_key(bill_key: str, dip_date: date) -> str:
    return f"low_balance|{bill_key}|{dip_date.isoformat()}"


def move_amount(shortfall_cents: int, buffer_cents: int) -> int:
    return ((shortfall_cents + buffer_cents + 499) // 500) * 500


def cancel_limit(limit_cents: int, monthly_cents: int) -> Optional[int]:
    remaining = limit_cents - monthly_cents
    return None if remaining <= 0 else min(((remaining + 499) // 500) * 500, limit_cents)


def fallback_message(signal: Signal) -> str:
    n = signal.numbers
    money = lambda k: f"${int(n.get(k, 0)) / 100:,.2f}"
    merchant = n.get("merchant", "A recurring charge")
    if signal.kind == "price_increase":
        return f"{merchant} increased from {money('previous_cents')} to {money('last_cents')}."
    if signal.kind == "new_subscription":
        return f"A new repeating charge from {merchant}: {money('last_cents')}, {n.get('frequency', 'recurring')}."
    if signal.kind == "income_change":
        if n.get("status") == "stale":
            return "Your usual income has not arrived recently. Check whether your pay schedule has changed."
        return f"Your income changed from {money('previous_cents')} to {money('last_cents')}."
    if signal.kind == "low_balance":
        text = f"{n.get('bill', 'Planned spending')} may leave you {money('shortfall_cents')} short on {n.get('date', 'an upcoming day')}."
        return text + (" No paycheck found, so no income is assumed." if n.get("assumed_no_income") else "")
    return "A change in your recurring activity needs a look."
