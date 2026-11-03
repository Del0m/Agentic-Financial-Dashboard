import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median, median_low
from typing import Literal, Optional, Sequence

from app.budget_engine.txns import add_months, month_start, round_cents

# repeating charges and paychecks found in a user's own transactions (tpc-33). streams are scheduled commitments;
# weekly habits (groceries, fuel) stay everyday spending, so the short-gap rules are strict on purpose. no DB or
# clock in here: the caller passes non-removed, non-pending rows and `today`

Frequency = Literal["weekly", "biweekly", "semi_monthly", "monthly"]          # "annual" is never produced
EVERYDAY_CATEGORIES = frozenset({"FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "TRANSPORTATION", "PERSONAL_CARE",
                                 "ENTERTAINMENT", "TRAVEL"})
# no price_increase: a metered bill moves with the season. rent, phone and internet stay in
METERED_UTILITIES = frozenset({"RENT_AND_UTILITIES_GAS_AND_ELECTRICITY", "RENT_AND_UTILITIES_WATER",
                               "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT", "RENT_AND_UTILITIES_OTHER_UTILITIES"})
INCOME = "INCOME"
UTILITIES = "RENT_AND_UTILITIES"
MONTHLY_STALE_DAYS = 65
# charges per month for each frequency, as (numerator, denominator)
PER_MONTH = {"monthly": (1, 1), "semi_monthly": (2, 1), "biweekly": (26, 12), "weekly": (52, 12)}


@dataclass(frozen=True)
class RTxn:
    date: date
    amount_cents: int            # plaid sign: positive = money out
    merchant: Optional[str]
    category: str
    detailed: Optional[str]


@dataclass(frozen=True)
class Stream:
    direction: Literal["in", "out"]
    merchant_key: str
    merchant_name: str
    category: str
    detailed: Optional[str]
    frequency: Frequency
    typical_cents: int
    last_cents: int
    previous_cents: Optional[int]     # the price before a rise, on the first charge at the new price only
    amount_varies: bool
    first_date: date
    last_date: date
    next_date: date
    gap_days: int
    anchors: tuple[int, ...]          # days of the month: monthly one, semi-monthly two, otherwise none
    n: int
    status: Literal["active", "stale"]


# lowercase, digits and punctuation removed, spaces collapsed, so "NETFLIX.COM 8443" and "Netflix.com 1123" match
def merchant_key(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return " ".join(re.sub(r"[^\w\s]|[\d_]", "", name.casefold()).split()) or None


def _cents(t: RTxn) -> int:
    return abs(t.amount_cents)


def _mid(values: Sequence[int]) -> int:
    return round_cents(Decimal(str(median(values))))


def _gaps(charges: Sequence[RTxn]) -> list[int]:
    return [(b.date - a.date).days for a, b in zip(charges, charges[1:])]


def _within_10_percent(amount: int, of: int) -> bool:
    return abs(amount - of) * 10 <= of


# the amount most charges sit at, when 3+ charges and more than half the group do. everything else in the group is
# an extra or a price change, and shouldn't move the amounts or the gaps
def _dominant_cluster(charges: Sequence[RTxn]) -> Optional[list[RTxn]]:
    best: list[RTxn] = []
    for c in charges:
        members = [t for t in charges if _within_10_percent(_cents(t), _cents(c))]
        if len(members) > len(best):
            best = members
    return best if len(best) >= 3 and len(best) * 2 > len(charges) else None


def _tolerance(gap: float) -> int:
    return 6 if gap >= 25 else 2


# charges after the cluster's last that still belong to the stream: a price step (the last 2+ on the cadence, within
# 10% of each other, any size, up or down), or a lone latest charge 5% to 50% above the cluster. a lone charge further
# off looks the same as a one-off, so it waits for a second charge
def _tail(cluster: list[RTxn], charges: Sequence[RTxn]) -> tuple[list[RTxn], bool]:
    gap = median(_gaps(cluster))
    on_cadence, previous = [], cluster[-1].date
    for t in charges:
        if t.date > previous and abs((t.date - previous).days - gap) <= _tolerance(gap):
            on_cadence.append(t)
            previous = t.date
    if not on_cadence:
        return [], False
    run = [on_cadence[-1]]
    for t in reversed(on_cadence[:-1]):
        if not _within_10_percent(_cents(t), _cents(run[-1])):
            break
        run.append(t)
    if len(run) >= 2:
        return run[::-1], True
    base = _mid([_cents(t) for t in cluster])
    return (run, False) if base * 105 <= _cents(run[0]) * 100 and _cents(run[0]) * 100 <= base * 150 else ([], False)


def _classify(gap: float, charges: Sequence[RTxn]) -> Optional[Frequency]:
    if 4 <= gap <= 10:
        return "weekly"
    if 11 <= gap <= 18:
        one_weekday = len({t.date.weekday() for t in charges}) == 1
        return "biweekly" if gap <= 17 and one_weekday else "semi_monthly"
    if 25 <= gap <= 35:
        return "monthly"
    return None


# how many charges to drop from the front: everything up to the latest gap the rules reject. a monthly series of 4+
# may hold one gap of about two cycles (a skipped month); short-gap pay has no per-gap rule
def _restart_at(charges: Sequence[RTxn], gap: float, frequency: Frequency, direction: str) -> int:
    if frequency != "monthly" and direction == "in":
        return 0
    gaps, skips = _gaps(charges), 0
    tolerance = _tolerance(gap)
    for i in range(len(gaps) - 1, -1, -1):
        if abs(gaps[i] - gap) <= tolerance:
            continue
        if frequency == "monthly" and skips == 0 and len(charges) >= 4 and abs(gaps[i] - 2 * gap) <= tolerance:
            skips = 1
            continue
        return i + 1
    return 0


def _cadence(charges: Sequence[RTxn], direction: str) -> Optional[tuple[Frequency, float, list[RTxn]]]:
    charges = list(charges)
    while len(charges) >= 3:
        gap = median(_gaps(charges))
        frequency = _classify(gap, charges)
        if frequency is None:
            return None
        cut = _restart_at(charges, gap, frequency, direction)
        if cut == 0:
            return frequency, gap, charges
        charges = charges[cut:]
    return None


def _on_day(month: date, day: int) -> date:
    return month.replace(day=min(day, calendar.monthrange(month.year, month.month)[1]))


def _anchors(frequency: Frequency, charges: Sequence[RTxn]) -> tuple[int, ...]:
    days = [t.date.day for t in charges]
    if frequency == "monthly":
        return (median_low(days[-3:]),)
    if frequency == "semi_monthly":
        recent = days[-4:]              # the two days take turns
        return tuple(sorted((median_low(recent[-1::-2]), median_low(recent[-2::-2]))))
    return ()


# anchored dates from a given month on
def _anchored(anchors: Sequence[int], since: date):
    month = month_start(since)
    while True:
        yield from sorted(_on_day(month, day) for day in anchors)
        month = add_months(month, 1)


# a late payment doesn't move the next bill: a 31st paid on the 1st is next due on the 31st of that same month
def _next_date(frequency: Frequency, anchors: Sequence[int], gap: int, last: date) -> date:
    if not anchors:
        return last + timedelta(days=gap)
    soonest = last + timedelta(days=15 if frequency == "monthly" else 7)
    return next(d for d in _anchored(anchors, last) if d >= soonest)


def _previous_cents(charges: Sequence[RTxn], cluster: Optional[list[RTxn]], step: Sequence[RTxn]) -> Optional[int]:
    last = _cents(charges[-1])
    if cluster and len(step) == 2:
        base = _mid([_cents(t) for t in cluster])
        # its first charge was dropped as a one-off, so the rise is only seen now
        if last * 100 > base * 150:
            return base
    prior = _cents(charges[-2])
    if abs(last - prior) * 20 < prior:
        return None
    before = [_cents(t) for t in charges[-4:-1]]
    return _mid(before) if len(before) >= 2 and max(before) * 10 <= min(before) * 11 else None


def _stale(frequency: Frequency, gap: int, last: date, today: date) -> bool:
    days = MONTHLY_STALE_DAYS if frequency == "monthly" else 2 * gap + 3
    return today > last + timedelta(days=days)


def _stream(direction: str, key: str, charges: list[RTxn], today: date) -> Optional[Stream]:
    latest = charges[-1]
    cluster = _dominant_cluster(charges)
    step: list[RTxn] = []
    series = charges
    if cluster is not None:
        tail, is_step = _tail(cluster, charges)
        step = tail if is_step else []
        series = cluster + tail
    elif latest.category != UTILITIES and max(map(_cents, charges)) > 3 * min(map(_cents, charges)):
        return None
    found = _cadence(series, direction)
    if found is None:
        return None
    frequency, gap, series = found
    varies = cluster is None
    if frequency != "monthly":
        if direction == "in" and latest.category != INCOME:
            return None
        if direction == "out" and (len(series) < 4 or varies or latest.category in EVERYDAY_CATEGORIES):
            return None
    gap_days = int(_mid([gap]))
    anchors = _anchors(frequency, series)
    if step:
        typical = _mid([_cents(t) for t in step])
    elif cluster is not None:
        typical = _mid([_cents(t) for t in series if t in cluster])
    else:
        typical = _mid([_cents(t) for t in series[-3:]])
    return Stream(direction=direction, merchant_key=key, merchant_name=latest.merchant or "Income",
                  category=latest.category, detailed=latest.detailed, frequency=frequency, typical_cents=typical,
                  last_cents=_cents(series[-1]), previous_cents=_previous_cents(series, cluster, step),
                  amount_varies=varies, first_date=series[0].date, last_date=series[-1].date,
                  next_date=_next_date(frequency, anchors, gap_days, series[-1].date), gap_days=gap_days,
                  anchors=anchors, n=len(series),
                  status="stale" if _stale(frequency, gap_days, series[-1].date, today) else "active")


def detect(txns: Sequence[RTxn], today: date, *, window_days: int = 180) -> list[Stream]:
    since = today - timedelta(days=window_days)
    groups: dict[tuple[str, str], list[RTxn]] = {}
    for t in sorted(txns, key=lambda t: t.date):
        if not since <= t.date <= today or t.amount_cents == 0:
            continue
        direction = "in" if t.amount_cents < 0 else "out"
        key = merchant_key(t.merchant)
        if key is None:
            # a category fallback made false streams out of null-merchant purchases; a paycheck is the exception
            if direction != "in" or t.category != INCOME:
                continue
            key = f"~{t.detailed or INCOME}"
        groups.setdefault((direction, key), []).append(t)
    streams = [_stream(direction, key, charges, today) for (direction, key), charges in sorted(groups.items())
               if len(charges) >= 3]
    return [s for s in streams if s is not None]


# occurrence dates from next_date on, up to and including `through`. anchored streams step from their days of the
# month, so Feb 28 doesn't drag a 31st bill to the 28th
def project(stream: Stream, through: date) -> list[date]:
    out = []
    if not stream.anchors:
        d = stream.next_date
        while d <= through:
            out.append(d)
            d += timedelta(days=stream.gap_days)
        return out
    for d in _anchored(stream.anchors, stream.next_date):
        if d > through:
            return out
        if d >= stream.next_date:
            out.append(d)


def monthly_cents(typical_cents: int, frequency: Frequency) -> int:
    per, months = PER_MONTH[frequency]
    return round_cents(Decimal(typical_cents * per) / months)
