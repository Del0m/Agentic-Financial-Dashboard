from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Iterable, Literal, Optional, Sequence

from app.budget_engine.txns import (GOAL_DEBT_PAYOFF, LOAN_PAYMENTS, Txn, add_months, category_totals, month_end,
                                    month_start, net_of_goal, savings, shift_early_bills, spending)

# today's overspend flags (architecture 6.4). budget periods are calendar months.

PACE_FLAG = 1.10
MIN_DAYS = 5      # one grocery trip on day 1 isn't overspending
USUAL_MONTHS = 3  # how far back "what this user has usually spent by now" looks
WEEKLY_PURCHASES = 4   # purchases in a month from which spending counts as at least weekly


@dataclass(frozen=True)
class BudgetLine:
    category: str
    kind: Literal["fixed", "flexible"]
    limit_cents: int
    group: str = "wants"          # needs | wants | saving | free (tpc-22) | goal (tpc-25)


@dataclass(frozen=True)
class PaceResult:
    category: str
    kind: Literal["fixed", "flexible", "unbudgeted"]
    limit_cents: int              # 0 for unbudgeted
    spent_cents: int
    days_elapsed: int
    days_in_period: int
    pace: Optional[float]         # None for fixed, unbudgeted, or a limit of 0
    flagged: bool
    reason: Optional[Literal["pace", "over_limit"]]
    group: str = "wants"


# what this category normally reaches by this day of the month. pace assumes even spending, so a monthly charge on the
# 5th or a weekly shop reads as overspending all month. medians, so one new or unusual month, like a club that started
# last month, doesn't become normal. spending at least weekly also gets one normal purchase on top, for timing: a
# weekly shop can land twice in the first 8 days of one month and once in another. a monthly charge lands on its day,
# so it gets none (tpc-45). with little history it's 0 and pace stands alone
def usual_by_day(history: Iterable[Txn], category: str, today: date) -> int:
    rows = [t for t in spending(history) if t.category == category]
    by_day, largest, counts = [], [], []
    for back in range(1, USUAL_MONTHS + 1):
        start = add_months(month_start(today), -back)
        cutoff = start.replace(day=min(today.day, month_end(start).day))
        month = [t.amount_cents for t in rows if start <= t.date <= month_end(start)]
        by_day.append(category_totals(rows, start, cutoff).get(category, 0))
        largest.append(max(month, default=0))
        counts.append(len(month))
    return round(median(by_day) + (median(largest) if median(counts) >= WEEKLY_PURCHASES else 0))


def history_start(period_start: date) -> date:
    return add_months(month_start(period_start), -USUAL_MONTHS)


def evaluate(lines: Sequence[BudgetLine], txns: Iterable[Txn], today: date,
             history: Iterable[Txn] = ()) -> list[PaceResult]:
    txns = list(txns)
    history = list(history)
    # a fixed bill paid early for next month is next month's (judged against the line's limit as the usual bill)
    bills = {line.category: line.limit_cents for line in lines if line.kind == "fixed"}
    spent = category_totals(shift_early_bills(spending(txns), bills), month_start(today), today)
    # a debt goal paid to a loan is the goal's, not loan spending: it would flag the loan line or land on free
    goal = next((line for line in lines if line.category == GOAL_DEBT_PAYOFF), None)
    if goal and spent.get(LOAN_PAYMENTS):
        loan = next((line.limit_cents for line in lines if line.category == LOAN_PAYMENTS), 0)
        spent[LOAN_PAYMENTS] = net_of_goal(spent[LOAN_PAYMENTS], loan, goal.limit_cents)
    saved = sum(category_totals(savings(txns), month_start(today), today).values())
    days_elapsed, days_in_period = today.day, month_end(today).day
    budgeted = {line.category for line in lines}
    unlined = sum(amount for c, amount in spent.items() if c not in budgeted)

    results = []
    for line in lines:
        amount = spent.get(line.category, 0)
        if line.group == "free":
            amount += unlined          # the room left after the other lines covers everything without one
        elif line.group == "saving":
            # what was saved so far; saving more than planned is never overspending
            results.append(PaceResult(line.category, line.kind, line.limit_cents, saved, days_elapsed,
                                      days_in_period, None, False, None, line.group))
            continue
        pace = None
        # fixed bills are lumpy (rent lands on the 1st), so they're only judged against the limit
        if line.kind == "flexible" and line.limit_cents > 0:
            # one division, so a pace of exactly 1.10 isn't 1.0999999
            pace = amount * days_in_period / (line.limit_cents * days_elapsed)
        reason = None
        if amount > line.limit_cents:
            reason = "over_limit"
        elif pace is not None and days_elapsed >= MIN_DAYS and pace >= PACE_FLAG:
            # free holds whatever had no line, so no single category's history describes it
            usual = 0 if line.group == "free" else usual_by_day(history, line.category, today)
            if amount > usual:
                reason = "pace"
        results.append(PaceResult(line.category, line.kind, line.limit_cents, amount, days_elapsed,
                                  days_in_period, pace, reason is not None, reason, line.group))

    # spending with no line is reported, never judged on its own; with a free line it's judged there (tpc-22)
    for cat in sorted(c for c, amount in spent.items() if amount > 0 and c not in budgeted):
        results.append(PaceResult(cat, "unbudgeted", 0, spent[cat], days_elapsed, days_in_period,
                                  None, False, None))
    return results
