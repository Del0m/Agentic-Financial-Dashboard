import math
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Optional, Sequence

from app.budget_engine.fit import FIRST_MONTH_DAY, HISTORY_MONTHS
from app.budget_engine.payoff import MAX_MONTHS, Debt, simulate
from app.budget_engine.txns import (Txn, add_months, category_totals, income_by_month, month_end, month_start,
                                    round_cents, savings, spending)

# the numbers behind a goal (tpc-23). pure: what the user has left each month, what an emergency fund still needs,
# and a goal's target and date. the llm picks the goals and their monthly amounts; everything else is here

GoalKind = Literal["debt_payoff", "emergency_fund", "savings"]
GOAL_KINDS = ("debt_payoff", "emergency_fund", "savings")
EMERGENCY_MONTHS = 3
SAVINGS_MONTHS = 12


@dataclass(frozen=True)
class GoalPlan:
    kind: GoalKind
    priority: int
    monthly_cents: int
    target_cents: int
    target_date: Optional[date]    # None when the goal outlasts the planner's stop (MAX_MONTHS)
    strategy: Optional[str]
    rationale: str


# the gateway's input: what the graph worked out for this run
@dataclass(frozen=True)
class GoalFacts:
    surplus_cents: int
    cap_cents: int
    offered: tuple[str, ...]
    gap_cents: int
    start: date
    protected_categories: tuple[str, ...] = ()
    note: Optional[str] = None


# complete months only, and none before the history starts: the same window fit_budget uses
def history_months(txns: Sequence[Txn], today: date) -> list[date]:
    if not txns:
        return []
    current = month_start(today)
    earliest = min(t.date for t in txns)
    begin = month_start(earliest) if earliest.day <= FIRST_MONTH_DAY else add_months(month_start(earliest), 1)
    first = max(add_months(current, -HISTORY_MONTHS), begin)
    months = []
    m = first
    while m < current:
        months.append(m)
        m = add_months(m, 1)
    return months


def _median_cents(values: Sequence[int]) -> int:
    return round_cents(Decimal(str(statistics.median(values))))


# what's left each month after spending and saving, at the median. a month in the red stays negative: it's real
# evidence against committing money. None with no complete month to measure
def monthly_surplus(txns: Iterable[Txn], today: date) -> Optional[int]:
    txns = list(txns)
    months = history_months(txns, today)
    if not months:
        return None
    spent, saved = spending(txns), savings(txns)
    left = [income - sum(category_totals(spent, m, month_end(m)).values())
            - sum(category_totals(saved, m, month_end(m)).values())
            for m, income in zip(months, income_by_month(txns, months))]
    return _median_cents(left)


def emergency_gap(txns: Iterable[Txn], today: date, needs: frozenset[str], savings_balance_cents: int) -> int:
    txns = list(txns)
    months = history_months(txns, today)
    if not months:
        return 0
    spent = spending(txns)
    totals = [sum(cents for cat, cents in category_totals(spent, m, month_end(m)).items() if cat in needs)
              for m in months]
    return max(EMERGENCY_MONTHS * _median_cents(totals) - savings_balance_cents, 0)


# the debts the planner would actually plan, by asking it: its rules for what's left out stay in one place
def planned_debts(debts: Sequence[Debt], start: date) -> list[Debt]:
    excluded = {key for key, _ in simulate(debts, 0, "avalanche", start=start).excluded}
    return [d for d in debts if d.key not in excluded and d.balance_cents > 0]


def offered_kinds(debts: Sequence[Debt], gap_cents: int, skip_kinds: Sequence[str], *, start: date) -> list[str]:
    available = {"savings"}
    if planned_debts(debts, start):
        available.add("debt_payoff")
    if gap_cents > 0:
        available.add("emergency_fund")
    return [k for k in GOAL_KINDS if k in available and k not in skip_kinds]


def build_goal(kind: GoalKind, priority: int, monthly_cents: int, strategy: Optional[str], rationale: str, *,
               debts: Sequence[Debt], gap_cents: int, start: date) -> GoalPlan:
    if kind == "debt_payoff":
        # the monthly amount is an extra on top of the minimums, which the budget's lines already pay
        target = sum(d.balance_cents for d in planned_debts(debts, start))
        when = simulate(debts, monthly_cents, strategy, start=start).debt_free
    elif kind == "emergency_fund":
        target = gap_cents
        months = math.ceil(gap_cents / monthly_cents)
        when = add_months(start, months - 1) if months <= MAX_MONTHS else None
    else:
        target = monthly_cents * SAVINGS_MONTHS
        when = add_months(start, SAVINGS_MONTHS - 1)
    return GoalPlan(kind, priority, monthly_cents, target, when, strategy if kind == "debt_payoff" else None, rationale)
