from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Optional

from app.budget_engine.txns import add_months, round_cents
from app.finance import avalanche_key

# paying off every debt month by month (architecture 6a.3, tpc-32). pure: the caller loads the debts and picks the
# first planned month. every month pays one budget, the extra plus every debt's minimum: minimums first, the rest
# to the strategy's target, spilling to the next, so a paid-off debt's minimum rolls over

MAX_MONTHS = 600
Strategy = Literal["avalanche", "snowball"]


@dataclass(frozen=True)
class Debt:
    key: int                       # the account id
    name: str
    kind: Literal["credit", "student", "mortgage"]
    apr: Optional[float]           # percent, e.g. 24.99
    balance_cents: int
    min_payment_cents: Optional[int]


@dataclass(frozen=True)
class Payoff:
    key: int
    name: str
    month: int                     # 1 = the first planned month
    date: date                     # first of that month
    interest_cents: int            # interest this debt accrued until paid off


@dataclass(frozen=True)
class Plan:
    strategy: Strategy
    extra_monthly_cents: int
    months: Optional[int]          # None when never
    debt_free: Optional[date]
    total_interest_cents: int      # up to the stop when never
    order: tuple[Payoff, ...]      # in payoff order
    never: tuple[int, ...]         # keys still open after MAX_MONTHS
    excluded: tuple[tuple[int, str], ...]   # (key, "mortgage" | "no_apr" | "no_minimum"), in input order


def _excluded(debt: Debt, include_mortgage: bool) -> Optional[str]:
    if debt.kind == "mortgage" and not include_mortgage:
        return "mortgage"
    if debt.apr is None:           # 0% is a real rate (a promo card), so only a missing one counts
        return "no_apr"
    if debt.min_payment_cents is None:
        return "no_minimum"
    return None


def _target_key(strategy: Strategy, debt: Debt, balance: int) -> tuple:
    if strategy == "avalanche":
        return avalanche_key(debt.apr, balance) + (debt.key,)
    return balance, -debt.apr, debt.key


def simulate(debts: Iterable[Debt], extra_monthly_cents: int, strategy: Strategy, *, start: date,
             include_mortgage: bool = False) -> Plan:
    included, excluded = [], []
    for d in debts:
        if d.balance_cents <= 0:   # nothing owed (or a credit balance): nothing to plan, whatever's missing
            continue
        reason = _excluded(d, include_mortgage)
        if reason:
            excluded.append((d.key, reason))
        else:
            included.append(d)

    balance = {d.key: d.balance_cents for d in included}
    interest = {d.key: 0 for d in included}
    budget = extra_monthly_cents + sum(d.min_payment_cents for d in included)
    order: list[Payoff] = []
    month = 0
    while any(balance.values()) and month < MAX_MONTHS:
        month += 1
        open_debts = [d for d in included if balance[d.key] > 0]
        for d in open_debts:
            charged = round_cents(Decimal(balance[d.key]) * Decimal(str(d.apr)) / 1200)
            balance[d.key] += charged
            interest[d.key] += charged
        cleared = []
        pool = budget
        for d in open_debts:
            pay = min(d.min_payment_cents, balance[d.key])
            balance[d.key] -= pay
            pool -= pay
            if balance[d.key] == 0:
                cleared.append(d)
        for d in sorted((d for d in open_debts if balance[d.key] > 0),
                        key=lambda d: _target_key(strategy, d, balance[d.key])):
            if pool <= 0:
                break
            pay = min(pool, balance[d.key])
            balance[d.key] -= pay
            pool -= pay
            if balance[d.key] == 0:
                cleared.append(d)
        when = add_months(start, month - 1)
        order += [Payoff(d.key, d.name, month, when, interest[d.key]) for d in cleared]

    never = tuple(d.key for d in included if balance[d.key] > 0)
    months = None if never else month
    return Plan(strategy=strategy, extra_monthly_cents=extra_monthly_cents, months=months,
                debt_free=add_months(start, months - 1) if months else None,
                total_interest_cents=sum(interest.values()), order=tuple(order), never=never,
                excluded=tuple(excluded))


# both strategies with the extra, and each again without it, so every answer can say what the extra changes
def compare(debts: Iterable[Debt], extra_monthly_cents: int, *, start: date,
            include_mortgage: bool = False) -> dict[str, Plan]:
    debts = list(debts)
    plans = {}
    for strategy in ("avalanche", "snowball"):
        plans[strategy] = simulate(debts, extra_monthly_cents, strategy, start=start,
                                   include_mortgage=include_mortgage)
        plans[f"baseline_{strategy}"] = simulate(debts, 0, strategy, start=start, include_mortgage=include_mortgage)
    return plans
