import math
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Optional, Sequence

from app.budget_engine.fit import FIRST_MONTH_DAY, HISTORY_MONTHS
from app.budget_engine.payoff import MAX_MONTHS, Debt, _excluded, simulate
from app.budget_engine.txns import (Txn, add_months, category_totals, income_by_month, month_end, month_start,
                                    round_cents, savings, spending)

# the numbers behind a goal (tpc-23). pure: what the user has left each month, what an emergency fund still needs,
# and a goal's target and date. the llm picks the goals and their monthly amounts; everything else is here

GoalKind = Literal["debt_payoff", "emergency_fund", "savings"]
GOAL_KINDS = ("debt_payoff", "emergency_fund", "savings")
EMERGENCY_MONTHS = 3
SAVINGS_MONTHS = 12
MIN_MONTHLY_CENTS = 100
MAX_MONTHLY_CENTS = 100_000_000


def goal_cap(surplus_cents: int, committed_cents: int, max_share: float, *, net_of_goals: bool) -> int:
    gross = surplus_cents + committed_cents if net_of_goals else surplus_cents
    return max(round_cents(Decimal(gross) * Decimal(str(max_share))) - committed_cents, 0)


@dataclass(frozen=True)
class GoalAllocation:
    kind: GoalKind
    monthly_cents: int
    strategy: Optional[str]
    rationale: str


@dataclass(frozen=True)
class GoalIssue:
    code: str
    message: str
    index: Optional[int] = None    # 1-based position in the rejected proposal
    kind: Optional[str] = None


def normalize_goal(kind: GoalKind, monthly_dollars: float, strategy: Optional[str], rationale: str) -> GoalAllocation:
    cents = round_cents(Decimal(str(monthly_dollars)) * 100)
    return GoalAllocation(kind, cents, (strategy or 'avalanche') if kind == 'debt_payoff' else None, rationale)


def validate_allocations(goals: Sequence[GoalAllocation], *, cap_cents: int,
                         offered: Sequence[str]) -> list[GoalIssue]:
    issues, seen = [], set()
    if not 1 <= len(goals) <= 3:
        issues.append(GoalIssue('bad_count', 'Propose one to three goals.'))
    for index, goal in enumerate(goals, 1):
        if goal.kind not in offered:
            issues.append(GoalIssue('not_offered', 'Choose only offered goal kinds.', index, goal.kind))
        if goal.kind in seen:
            issues.append(GoalIssue('repeat', 'Propose each kind at most once.', index, goal.kind))
        seen.add(goal.kind)
        if not MIN_MONTHLY_CENTS <= goal.monthly_cents <= MAX_MONTHLY_CENTS:
            issues.append(GoalIssue('bad_amount', 'Monthly amounts must be $1 to $1,000,000.', index, goal.kind))
    total = sum(g.monthly_cents for g in goals)
    if total > cap_cents:
        issues.append(GoalIssue('over_cap', f'Total ${total / 100:,.2f} exceeds cap ${cap_cents / 100:,.2f} '
                                f'by ${(total - cap_cents) / 100:,.2f}. Reduce the combined monthly amounts.'))
    return issues


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
    committed_cents: int = 0      # monthly money accepted goals already take; the cap is net of it
    surplus_source: str = "measured"


# an accepted goal as the budget sees it (tpc-25): a line from its start month through the month of its target date
@dataclass(frozen=True)
class CommittedGoal:
    id: int
    kind: GoalKind
    monthly_cents: int
    start_date: date
    target_date: date


def budgeted_in(goal: CommittedGoal, month: date) -> bool:
    return goal.start_date <= month <= month_start(goal.target_date)


def finished_before(goal: CommittedGoal, month: date) -> bool:
    return month_start(goal.target_date) < month


def committed_cents(goals: Sequence[CommittedGoal]) -> int:
    return sum(g.monthly_cents for g in goals)


def validate_plans(plans: Sequence[GoalPlan], *, start: date) -> list[GoalIssue]:
    issues = []
    last = add_months(start, MAX_MONTHS - 1)
    for plan in plans:
        if plan.target_cents <= 0:
            issues.append(GoalIssue('bad_target', 'The goal must have a positive target.', plan.priority, plan.kind))
        if plan.target_date is None:
            issues.append(GoalIssue('no_date', f'This allocation cannot finish within {MAX_MONTHS} months. '
                                    'Increase its monthly amount within the cap or choose another offered kind.',
                                    plan.priority, plan.kind))
        elif not start <= plan.target_date <= last:
            issues.append(GoalIssue('bad_date', f'The target date must be within {MAX_MONTHS} payments from start.',
                                    plan.priority, plan.kind))
    return issues


# complete months only, and none before the history starts: the same window fit_budget uses
def history_months(txns: Sequence[Txn], today: date, history_start: Optional[date] = None) -> list[date]:
    if not txns:
        return []
    current = month_start(today)
    earliest = history_start or min(t.date for t in txns)
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
def monthly_surplus(txns: Iterable[Txn], today: date, *, history_start: Optional[date] = None) -> Optional[int]:
    txns = list(txns)
    months = history_months(txns, today, history_start)
    if not months:
        return None
    spent, saved = spending(txns), savings(txns)
    left = [income - sum(category_totals(spent, m, month_end(m)).values())
            - sum(category_totals(saved, m, month_end(m)).values())
            for m, income in zip(months, income_by_month(txns, months))]
    return _median_cents(left)


# what the surplus still owes the cards: a goal is on top of the minimums, and a card payment isn't spending (it pays
# for purchases already counted). a loan's payment is spending, so it's already out
def card_minimums(debts: Sequence[Debt]) -> int:
    return sum(d.min_payment_cents for d in debts if d.kind == "credit" and d.balance_cents > 0 and d.min_payment_cents)


def emergency_gap(txns: Iterable[Txn], today: date, needs: frozenset[str], savings_balance_cents: int, *, history_start: Optional[date] = None) -> int:
    txns = list(txns)
    months = history_months(txns, today, history_start)
    if not months:
        return 0
    spent = spending(txns)
    totals = [sum(cents for cat, cents in category_totals(spent, m, month_end(m)).items() if cat in needs)
              for m in months]
    return max(EMERGENCY_MONTHS * _median_cents(totals) - savings_balance_cents, 0)


# the debts the planner would actually plan, by asking it: its rules for what's left out stay in one place
def planned_debts(debts: Sequence[Debt], start: date) -> list[Debt]:
    return [d for d in debts if d.balance_cents > 0 and _excluded(d, False) is None]


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
        # the monthly amount is an extra on top of the minimums, which the surplus has already allowed for
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
