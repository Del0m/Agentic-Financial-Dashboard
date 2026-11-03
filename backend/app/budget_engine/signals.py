from dataclasses import dataclass
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
    return proposed if proposed in allowed_actions(signal) else "alert"


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
        return f"{n.get('bill', 'Planned spending')} may leave you {money('shortfall_cents')} short on {n.get('date', 'an upcoming day')}."
    return "A change in your recurring activity needs a look."
