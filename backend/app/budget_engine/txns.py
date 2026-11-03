import calendar
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional, Sequence

# the budget engine's input: plain values the caller (TPC-18) builds from `transactions` rows.
# no DB or clock in here, so every budget rule is testable with literals.


@dataclass(frozen=True)
class Txn:
    date: date
    amount_cents: int            # plaid sign: positive = money out, negative = money in / refund
    category: str                # pf_category_primary; the caller maps None to "UNCATEGORIZED"
    detailed: Optional[str] = None
    merchant: Optional[str] = None
    overridden: bool = False     # the user's correction or merchant rule chose the category, not plaid


# reserved line categories (tpc-22): the saving line tracks transfers to savings, the free line is the room left
SAVINGS = "SAVINGS_TRANSFER"
FREE = "FREE"
SAVINGS_DETAILED = "TRANSFER_OUT_SAVINGS"     # plaid's label for a transfer to the user's savings

# an accepted goal's budget line (tpc-25), one reserved category per goal kind, and the plaid category a debt goal's
# payments can land in
GOAL_DEBT_PAYOFF = "GOAL_DEBT_PAYOFF"
GOAL_EMERGENCY_FUND = "GOAL_EMERGENCY_FUND"
GOAL_SAVINGS = "GOAL_SAVINGS"
GOAL_CATEGORIES = {"debt_payoff": GOAL_DEBT_PAYOFF, "emergency_fund": GOAL_EMERGENCY_FUND, "savings": GOAL_SAVINGS}
LOAN_PAYMENTS = "LOAN_PAYMENTS"


# not spending: income isn't, a transfer moves money between your own accounts,
# and a card payment pays for purchases already counted
# and a transfer to savings is saving. a user who moved a card payment into a line means it
EXCLUDED_PRIMARY = frozenset({"INCOME", "TRANSFER_IN", "TRANSFER_OUT", SAVINGS})
EXCLUDED_DETAILED = frozenset({"LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"})


def spending(txns: Iterable[Txn]) -> list[Txn]:
    return [t for t in txns if t.category not in EXCLUDED_PRIMARY
            and (t.overridden or t.detailed not in EXCLUDED_DETAILED)]


def savings(txns: Iterable[Txn]) -> list[Txn]:
    return [t for t in txns if t.category == SAVINGS]


# money in per month, as positive cents (plaid signs income negative); months are firsts of the month
def income_by_month(txns: Iterable[Txn], months: Sequence[date]) -> list[int]:
    income = [t for t in txns if t.category == "INCOME"]
    return [max(-sum(t.amount_cents for t in income if m <= t.date <= month_end(m)), 0) for m in months]


# "Costco" and "COSTCO " are one merchant for a category rule
def normalize_merchant(name: str) -> str:
    return name.strip().casefold()


# next month's rent paid a day or two early would put two bills in one month and none in the next. a fixed
# category's charge in a month's last EARLY_DAYS days, within EARLY_TOLERANCE of the usual bill, is moved to the
# 1st of the next month. bills maps a fixed category to its usual bill in cents (a line's limit, or fit's estimate)
EARLY_DAYS = 3
EARLY_TOLERANCE = Decimal("0.10")


def shift_early_bills(txns: Iterable[Txn], bills: dict[str, int]) -> list[Txn]:
    out = []
    for t in txns:
        bill = bills.get(t.category)
        if (bill and t.amount_cents > 0 and t.date.day > month_end(t.date).day - EARLY_DAYS
                and abs(t.amount_cents - bill) <= bill * EARLY_TOLERANCE):
            t = replace(t, date=add_months(month_start(t.date), 1))
        out.append(t)
    return out


def category_totals(txns: Iterable[Txn], start: date, end: date) -> dict[str, int]:
    totals: dict[str, int] = {}
    for t in txns:
        if start <= t.date <= end:
            totals[t.category] = totals.get(t.category, 0) + t.amount_cents
    # refunds net a category down, but a refund larger than the month's purchases isn't negative spending
    return {cat: max(total, 0) for cat, total in totals.items()}


# a goal paid to a loan shows up as loan spending. only what's above the line's old limit is the goal's, so a goal
# paid to a card (not in LOAN_PAYMENTS) leaves the usual loan payment alone
def net_of_goal(actual: int, limit: int, goal: int) -> int:
    return actual - min(goal, max(actual - limit, 0))


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    if d.day != 1:
        raise ValueError(f"add_months needs the 1st of a month, got {d}")
    year, month = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(year, month + 1, 1)


# money rounds half up; python's round() is banker's rounding
def round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
