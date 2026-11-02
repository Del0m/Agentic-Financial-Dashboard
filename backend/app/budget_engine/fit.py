import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Literal, Optional

from app.budget_engine.txns import (SAVINGS, Txn, add_months, category_totals, income_by_month, month_end,
                                    month_start, round_cents, savings, shift_early_bills, spending)

# initial budget from history (architecture 6.3). fixed lines take a typical month's amount; flexible lines
# take the median month plus a small cushion, so one vacation doesn't inflate the budget.

FIXED_DEFAULT = frozenset({"RENT_AND_UTILITIES", "LOAN_PAYMENTS"})
FLEX_MARKUP = 1.05
OUTLIER_RATIO = 1.5
HISTORY_MONTHS = 3
# history that starts after this day of its first month only covers part of it
FIRST_MONTH_DAY = 3


@dataclass(frozen=True)
class OutlierMonth:
    month: date                  # first of the month
    total_cents: int
    median_cents: int            # the center the month was compared with; always the median in production
    top_txns: tuple[Txn, ...]    # up to 3, largest amount first


@dataclass(frozen=True)
class FittedLine:
    category: str
    kind: Literal["fixed", "flexible"]
    limit_cents: int
    monthly_totals: tuple[int, ...]   # oldest month first
    outliers: tuple[OutlierMonth, ...]


@dataclass(frozen=True)
class FitResult:
    lines: tuple[FittedLine, ...]     # sorted by category
    skipped: tuple[str, ...]          # flexible categories whose median is 0, sorted
    window: tuple[date, ...] = ()     # the fitted months (first of each), oldest first
    income_cents: Optional[int] = None    # the median month's income; None when that median is $0
    spending_total_cents: int = 0         # the median month's total spending + the cushion: the total with no income


def fit_budget(txns: Iterable[Txn], today: date, fixed: frozenset[str] = FIXED_DEFAULT,
               center: Literal["median", "mean"] = "median") -> FitResult:
    txns = list(txns)    # read more than once below; a generator would come back empty
    if not txns:
        return FitResult((), ())
    # complete months only (a partial month would drag every median down), and none before the history starts.
    # callers load a month further back than the window, so the window's edge doesn't look like the history's start
    current = month_start(today)
    window_start = add_months(current, -HISTORY_MONTHS)
    earliest = min(t.date for t in txns)
    start = month_start(earliest) if earliest.day <= FIRST_MONTH_DAY else add_months(month_start(earliest), 1)
    first = max(window_start, start)
    months = []
    m = first
    while m < current:
        months.append(m)
        m = add_months(m, 1)
    if not months:
        return FitResult((), ())

    # that extra month's only other use: a bill paid early at its end belongs to the window's first month
    usual = _usual_bills([t for t in spending(txns) if t.date >= window_start], fixed)
    spent = shift_early_bills(spending(txns), usual)
    per_month = [category_totals(spent, m, month_end(m)) for m in months]
    categories = sorted({cat for totals in per_month for cat, total in totals.items() if total > 0})

    lines, skipped = [], []
    for cat in categories:
        totals = tuple(t.get(cat, 0) for t in per_month)
        if cat in fixed:
            # rent paid early puts two rents in one month and none in the next. a fixed bill is never really
            # $0, so only months that had it count, and the lower median is always a real month's amount
            # (an even count would otherwise average one rent with two)
            limit = statistics.median_low([total for total in totals if total > 0])
            lines.append(FittedLine(cat, "fixed", limit, totals, ()))
            continue
        if center == "mean":
            mid = Decimal(sum(totals)) / len(totals)
        else:
            mid = Decimal(str(statistics.median(totals)))
        if mid == 0:
            # an occasional purchase would get a $0 limit that flags every time
            skipped.append(cat)
            continue
        outliers = tuple(
            OutlierMonth(m, total, round_cents(mid), _top_txns(spent, cat, m))
            for m, total in zip(months, totals) if total > mid * Decimal(str(OUTLIER_RATIO))
        )
        limit = round_cents(mid * Decimal(str(FLEX_MARKUP)))
        lines.append(FittedLine(cat, "flexible", limit, totals, outliers))

    # the saving line is fitted like a fixed bill: a month without a transfer is a missed month, not a $0 goal
    saved = savings(txns)
    saved_totals = tuple(category_totals(saved, m, month_end(m)).get(SAVINGS, 0) for m in months)
    if any(saved_totals):
        lines.append(FittedLine(SAVINGS, "fixed", statistics.median_low([x for x in saved_totals if x > 0]),
                                saved_totals, ()))
        lines.sort(key=lambda ln: ln.category)

    income = statistics.median(income_by_month(txns, months))
    spent_totals = [sum(totals.values()) for totals in per_month]
    fallback = round_cents(Decimal(str(statistics.median(spent_totals))) * Decimal(str(FLEX_MARKUP)))
    return FitResult(tuple(lines), tuple(skipped), tuple(months), round_cents(Decimal(str(income))) or None, fallback)


# a fixed category's usual bill: the lower median of each month's largest charge. the largest, because rent and
# utilities share a category and the rent is what gets paid early
def _usual_bills(spent: list[Txn], fixed: frozenset[str]) -> dict[str, int]:
    largest: dict[tuple[str, date], int] = {}
    for t in spent:
        if t.category in fixed and t.amount_cents > 0:
            key = (t.category, month_start(t.date))
            largest[key] = max(largest.get(key, 0), t.amount_cents)
    by_cat: dict[str, list[int]] = {}
    for (cat, _), cents in largest.items():
        by_cat.setdefault(cat, []).append(cents)
    return {cat: statistics.median_low(v) for cat, v in by_cat.items()}


def _top_txns(spent: list[Txn], category: str, month: date) -> tuple[Txn, ...]:
    end = month_end(month)
    hits = [t for t in spent if t.category == category and month <= t.date <= end and t.amount_cents > 0]
    return tuple(sorted(hits, key=lambda t: (-t.amount_cents, t.date))[:3])
