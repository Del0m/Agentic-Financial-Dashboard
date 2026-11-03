from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from math import sqrt
from statistics import stdev
from typing import Literal, Optional, Sequence

from app.budget_engine.goals import CommittedGoal, budgeted_in
from app.budget_engine.recurring import Stream, merchant_key, monthly_cents, project
from app.budget_engine.txns import add_months, month_start, round_cents


@dataclass(frozen=True)
class CardIn:
    account_id: int
    name: str
    min_cents: Optional[int]
    due_date: Optional[date]


@dataclass(frozen=True)
class ForecastInput:
    today: date
    start_balance_cents: Optional[int]
    streams: Sequence[Stream]
    cards: Sequence[CardIn]
    goals: Sequence[CommittedGoal]
    everyday_daily_cents: Sequence[int]
    history_days: int
    inflow_transactions: int
    buffer_cents: int = 10000


@dataclass(frozen=True)
class Event:
    date: date
    name: str
    cents: int
    source: Literal["stream", "card_min", "goal"]
    key: str


@dataclass(frozen=True)
class Day:
    date: date
    expected_cents: int
    low_cents: int
    high_cents: int


@dataclass(frozen=True)
class Dip:
    date: date
    days_ahead: int
    shortfall_cents: int
    cause: Optional[Event]


@dataclass(frozen=True)
class ForecastResult:
    status: Literal["ok", "no_checking", "no_history"]
    income: Literal["paycheck", "none"]
    horizon_days: int
    days: tuple[Day, ...]
    events: tuple[Event, ...]
    dip: Optional[Dip]
    monthly_surplus_cents: Optional[int]
    committed_goal_cents: int
    cash_above_buffer_cents: Optional[int]
    surplus_fallback: Optional[Literal["no_inflow_stream", "short_history", "stale_paycheck", "no_checking"]]
    assumptions: tuple[str, ...]


def _scheduled(stream: Stream, today: date, through: date, assumptions: list[str]) -> list[date]:
    if stream.next_date < today:
        if (today - stream.next_date).days <= 5:
            dates = project(stream, through)
            return sorted({today + timedelta(days=1), *(d for d in dates if d > today)})
        assumptions.append(f"bill_overdue:{stream.merchant_name}")
    return [d for d in project(stream, through) if d > today]


def _pays_goal(s: Stream, goal: CommittedGoal) -> bool:
    loan = (s.detailed or "").startswith("LOAN_PAYMENTS")
    kind = loan if goal.kind == "debt_payoff" else s.category == "SAVINGS_TRANSFER"
    return s.direction == "out" and kind and s.first_date >= goal.start_date


# the goal's own payment is already an event, so a stream paying it mustn't count twice. streams name no goal, so a match
# is the right kind, new since the goal started, and about the goal's amount or more; each goal takes the closest one
# and only its own amount. a miss counts the payment twice, which understates the surplus: an unrelated bill left out
# would overstate it. returns monthly cents by id(stream)
def _goal_shares(streams: Sequence[Stream], goals: Sequence[CommittedGoal]) -> dict[int, int]:
    shares: dict[int, int] = {}
    for goal in goals:
        size = lambda s: monthly_cents(s.typical_cents, s.frequency)
        matches = [s for s in streams if id(s) not in shares and _pays_goal(s, goal)
                   and size(s) * 10 >= goal.monthly_cents * 9]
        best = min(matches, key=lambda s: abs(size(s) - goal.monthly_cents), default=None)
        if best is not None:
            shares[id(best)] = min(size(best), goal.monthly_cents)
    return shares


def forecast(inp: ForecastInput) -> ForecastResult:
    today = inp.today
    active = [s for s in inp.streams if s.status == "active"]
    pay = [s for s in active if s.direction == "in" and s.category == "INCOME"]
    all_pay = [s for s in inp.streams if s.direction == "in" and s.category == "INCOME"]
    fallback = ("no_checking" if inp.start_balance_cents is None else
                "stale_paycheck" if all_pay and not pay else "no_inflow_stream" if not pay else
                "short_history" if inp.history_days < 65 or inp.inflow_transactions < 3 else None)
    assumptions = [] if pay else ["no_income_stream"]
    goals = [g for g in inp.goals if month_start(g.target_date) >= month_start(today)]
    committed = sum(g.monthly_cents for g in goals)
    cash = None if inp.start_balance_cents is None else max(inp.start_balance_cents - inp.buffer_cents, 0)
    if inp.start_balance_cents is None or inp.history_days < 28:
        return ForecastResult("no_checking" if inp.start_balance_cents is None else "no_history",
                              "paycheck" if pay else "none", 0, (), (), None, None, committed, cash,
                              fallback or "short_history", tuple(assumptions))
    future = today + timedelta(days=90)
    dates = {id(s): _scheduled(s, today, future, assumptions) for s in active}
    pay_dates = sorted({d for s in pay for d in dates[id(s)]})
    horizon = min(max(30, (pay_dates[0] - today).days + 1 if pay_dates else 30), 45)
    through = today + timedelta(days=horizon)

    shares = _goal_shares(active, goals)
    included = []
    for s in active:
        monthly, share = monthly_cents(s.typical_cents, s.frequency), shares.get(id(s), 0)
        if share >= monthly > 0:
            continue
        if share:
            rest = replace(s, typical_cents=round_cents(Decimal(s.typical_cents * (monthly - share)) / monthly))
            dates[id(rest)] = dates[id(s)]
            s = rest
        included.append(s)
    events = [Event(d, "Income" if s.direction == "in" else s.merchant_name,
                    s.typical_cents * (1 if s.direction == "in" else -1), "stream", s.merchant_key)
              for s in included for d in dates[id(s)] if d <= through]
    payment_streams = [s for s in included if s.direction == "out" and s.detailed == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"]
    uncovered = []
    for card in inp.cards:
        key = merchant_key(card.name)
        covered = any(key and key in s.merchant_key for s in payment_streams) or (len(inp.cards) == len(payment_streams) == 1)
        if covered:
            continue
        uncovered.append(card)
        if card.due_date is None:
            assumptions.append(f"card_due_date_unknown:{card.name}")
        elif card.min_cents is not None and card.min_cents > 0:
            due, offset = card.due_date, 0
            while due <= through:
                if due > today:
                    events.append(Event(due, card.name, -card.min_cents, "card_min", f"card:{card.account_id}"))
                offset += 1
                anchor = add_months(month_start(card.due_date), offset)
                due = anchor.replace(day=min(card.due_date.day, monthrange(anchor.year, anchor.month)[1]))
    month = month_start(today)
    while month <= through:
        # the first paycheck may already have posted this month, so include the stream's calendar before today
        payday = min((d for s in pay for d in project(replace(s, next_date=s.first_date), add_months(month, 1))
                      if month_start(d) == month), default=None)
        when = payday + timedelta(days=1) if payday else month
        for goal in goals:
            if today < when <= through and budgeted_in(goal, month):
                events.append(Event(when, goal.kind.replace("_", " ").capitalize(), -goal.monthly_cents,
                                    "goal", f"goal:{goal.id}"))
        month = add_months(month, 1)
    events.sort(key=lambda e: (e.date, e.cents, e.key))
    daily = list(inp.everyday_daily_cents)
    mean = Decimal(sum(daily)) / len(daily) if daily else Decimal(0)
    weekly = [sum(daily[i:i + 7]) for i in range(max(len(daily) - 84, 0), len(daily) - 6, 7)]
    sd = stdev(weekly) if len(weekly) > 1 else 0
    days, balance = [], inp.start_balance_cents
    for k in range(1, horizon + 1):
        d = today + timedelta(days=k)
        balance += sum(e.cents for e in events if e.date == d)
        cost, spread = mean * k, Decimal(str(1.2816 * sd * sqrt(k / 7)))
        days.append(Day(d, balance - round_cents(cost), balance - round_cents(cost + spread),
                        balance - round_cents(max(cost - spread, Decimal(0)))))
    low_days = [d for d in days[:14] if d.low_cents < inp.buffer_cents]
    dip = None
    if low_days:
        first = low_days[0]
        # the band can cross a day after a bill, or just after it posted. keep that bill's identity without
        # applying a posted charge twice; a paycheck in between breaks the attribution
        last_pay = max([s.last_date for s in pay if s.last_date <= first.date] +
                       [e.date for e in events if e.cents > 0 and e.date <= first.date], default=date.min)
        candidates = [e for e in events if e.cents < 0 and last_pay < e.date <= first.date
                      and (first.date - e.date).days <= 5]
        candidates += [Event(s.last_date, s.merchant_name, -s.typical_cents, "stream", s.merchant_key)
                       for s in included if s.direction == "out" and last_pay < s.last_date <= today
                       and (first.date - s.last_date).days <= 5]
        cause = min(candidates, key=lambda e: e.cents, default=None)
        dip = Dip(first.date, (first.date - today).days, inp.buffer_cents - min(d.low_cents for d in days[:14]), cause)
    surplus = None
    if fallback is None:
        flow = sum(monthly_cents(s.typical_cents, s.frequency) * (1 if s.direction == "in" else -1)
                   for s in included) - committed - sum(c.min_cents or 0 for c in uncovered) - Decimal('30.4375') * mean
        surplus = round_cents(flow / 1000) * 1000
    return ForecastResult("ok", "paycheck" if pay else "none", horizon, tuple(days), tuple(events), dip,
                          surplus, committed, cash, fallback, tuple(dict.fromkeys(assumptions)))
