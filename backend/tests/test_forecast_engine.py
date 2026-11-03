from dataclasses import replace
from datetime import date

import pytest

from app.budget_engine.forecast import CardIn, ForecastInput, forecast
from app.budget_engine.goals import CommittedGoal, goal_cap
from app.budget_engine.recurring import Stream

TODAY = date(2026, 10, 10)


def stream(name="Landlord", cents=150000, day=13, direction="out", category="RENT_AND_UTILITIES", **kw):
    s = Stream(direction, name.lower(), name, category, None, "monthly", cents, cents, None, False,
               date(2026, 5, day), date(2026, 9, min(day, 30)), date(2026, 10, day), 30, (day,), 5, "active")
    return replace(s, **kw)


def inputs(**kw):
    inp = ForecastInput(TODAY, 155000, [stream(), stream("Employer", 420000, 15, "in", "INCOME")], [], [], [0]*90, 120, 4)
    return replace(inp, **kw)


def test_rent_before_payday_is_a_dip_that_names_rent():
    r = forecast(inputs())
    assert (r.dip.date, r.dip.days_ahead, r.dip.shortfall_cents, r.dip.cause.name) == (date(2026, 10, 13), 3, 5000, "Landlord")
    assert next(d.expected_cents for d in r.days if d.date == date(2026, 10, 15)) == 425000


def test_a_low_path_equal_to_the_buffer_is_not_a_dip():
    assert forecast(inputs(start_balance_cents=160000)).dip is None


def test_the_deepest_low_in_14_days_sets_the_shortfall():
    inp = inputs()
    r = forecast(replace(inp, streams=[*inp.streams, stream("Other", 3000, 14)]))
    assert r.dip.date == date(2026, 10, 13) and r.dip.shortfall_cents == 8000


def test_an_event_dated_today_is_not_applied_twice():
    r = forecast(inputs(streams=[stream(day=10)]))
    assert not any(e.date == TODAY for e in r.events)
    assert r.days[0].expected_cents == 155000


def test_everyday_spending_falls_by_the_mean_not_the_median():
    assert forecast(inputs(streams=[], everyday_daily_cents=[0,0,0,400]*22+[0,0])).days[0].expected_cents == 154902


def test_constant_spending_has_no_band():
    r = forecast(inputs(streams=[], everyday_daily_cents=[100]*90))
    assert all(d.low_cents == d.expected_cents == d.high_cents for d in r.days)
    assert r.days[13].expected_cents == 153600


def test_the_band_orders_and_is_symmetric_until_clamped():
    assert all(d.low_cents <= d.expected_cents <= d.high_cents for d in forecast(inputs(everyday_daily_cents=[0]*45+[300]*45)).days)


def test_no_checking_account():
    r = forecast(inputs(start_balance_cents=None))
    assert (r.status, r.days, r.dip, r.surplus_fallback, r.monthly_surplus_cents) == ("no_checking", (), None, "no_checking", None)


def test_under_28_days_of_history_is_no_history():
    assert forecast(inputs(history_days=27)).status == "no_history"


def test_no_income_stream_assumes_no_income():
    r = forecast(inputs(streams=[stream()]))
    assert r.income == "none" and r.surplus_fallback == "no_inflow_stream" and r.horizon_days == 30
    assert "no_income_stream" in r.assumptions


def test_a_stale_paycheck_falls_back():
    r = forecast(inputs(streams=[stream("Employer", 420000, 15, "in", "INCOME", status="stale")]))
    assert r.surplus_fallback == "stale_paycheck" and r.events == ()


@pytest.mark.parametrize("kw", [{"history_days": 60}, {"inflow_transactions": 2}])
def test_short_history_falls_back(kw):
    assert forecast(inputs(**kw)).surplus_fallback == "short_history"


def test_an_overdue_bill_lands_tomorrow():
    r = forecast(inputs(streams=[stream(day=8)]))
    assert r.events[0].date == date(2026, 10, 11)


def test_a_bill_overdue_by_more_than_5_days_rolls_forward():
    r = forecast(inputs(streams=[stream(day=1)]))
    assert r.events[0].date == date(2026, 11, 1) and "bill_overdue:Landlord" in r.assumptions


def test_card_minimum_lands_on_its_due_date_without_a_stream():
    r = forecast(inputs(cards=[CardIn(7, "Card", 9500, date(2026, 10, 20))]))
    assert [(e.date, e.cents, e.key) for e in r.events if e.source == "card_min"] == [(date(2026, 10, 20), -9500, "card:7")]


def test_a_card_with_a_payment_stream_adds_no_minimum():
    inp = inputs(); r = forecast(replace(inp, streams=[*inp.streams, stream("Card", 9500, 20, detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")], cards=[CardIn(7,"Card",9500,date(2026,10,20))]))
    assert not any(e.source == "card_min" for e in r.events)


def test_a_card_with_no_due_date_counts_in_the_surplus_only():
    a = forecast(inputs()); b = forecast(inputs(cards=[CardIn(7,"Card",9500,None)]))
    assert "card_due_date_unknown:Card" in b.assumptions and not any(e.source == "card_min" for e in b.events)
    assert 9000 <= a.monthly_surplus_cents-b.monthly_surplus_cents <= 10000


def test_a_past_due_date_rolls_forward_by_months():
    r = forecast(inputs(cards=[CardIn(7,"Card",9500,date(2026,9,20))]))
    assert next(e.date for e in r.events if e.source == "card_min") == date(2026,10,20)


def test_goal_pays_the_day_after_payday():
    r = forecast(inputs(goals=[CommittedGoal(1,"debt_payoff",58300,date(2026,10,1),date(2027,3,1))]))
    assert [(e.date,e.cents) for e in r.events if e.source == "goal"] == [(date(2026,10,16),-58300)]


def test_goal_with_no_paycheck_pays_on_the_first():
    r = forecast(inputs(streams=[],goals=[CommittedGoal(1,"savings",1000,date(2026,11,1),date(2027,3,1))]))
    assert next(e.date for e in r.events if e.source == "goal") == date(2026,11,1)


def test_a_goal_not_yet_started_has_no_event_but_is_in_the_surplus():
    r = forecast(inputs(goals=[CommittedGoal(1,"savings",1000,date(2027,1,1),date(2027,3,1))]))
    assert not any(e.source == "goal" for e in r.events) and r.committed_goal_cents == 1000


def test_goal_payments_already_in_history_are_not_counted_twice():
    inp=inputs(); goal=CommittedGoal(1,"savings",50000,date(2026,10,1),date(2027,3,1))
    new=stream("Savings",50000,5,category="SAVINGS_TRANSFER",first_date=date(2026,10,1))
    a=forecast(replace(inp,goals=[goal])); b=forecast(replace(inp,goals=[goal],streams=[*inp.streams,new]))
    assert a.monthly_surplus_cents == b.monthly_surplus_cents


def test_surplus_is_a_flow_and_ignores_the_balance():
    inp=inputs(); streams=[*inp.streams,stream("Savings",50000,3,category="SAVINGS_TRANSFER"),stream("Streamly",1599,5)]
    a=forecast(replace(inp,streams=streams,everyday_daily_cents=[100]*90,start_balance_cents=10000))
    b=forecast(replace(inp,streams=streams,everyday_daily_cents=[100]*90,start_balance_cents=2000000))
    assert a.monthly_surplus_cents == b.monthly_surplus_cents == 215000


def test_accepted_goals_lower_the_surplus_by_exactly_their_amount():
    inp=inputs(); streams=[*inp.streams,stream("Savings",50000,3,category="SAVINGS_TRANSFER"),stream("Streamly",1599,5)]
    goals=[CommittedGoal(i,"savings",n,date(2026,11,1),date(2027,3,1)) for i,n in enumerate((58300,19440))]
    r=forecast(replace(inp,streams=streams,everyday_daily_cents=[100]*90,goals=goals))
    assert r.monthly_surplus_cents == 138000 and r.committed_goal_cents == 77740


def test_cash_above_buffer_is_reported_separately():
    assert forecast(inputs()).cash_above_buffer_cents == 145000


def test_monthly_bill_on_the_31st_is_clamped():
    s=stream(day=31,next_date=date(2027,1,31),last_date=date(2026,12,31))
    r=forecast(inputs(today=date(2027,1,29),streams=[s]))
    assert [e.date for e in r.events] == [date(2027,1,31),date(2027,2,28)]


@pytest.mark.parametrize("surplus,taken,expected", [(50000,50000,30000),(44200,58300,23700),(24760,77740,4260),(97175,0,77740),(-5000,0,0)])
def test_goal_cap_on_the_forecast_path(surplus,taken,expected):
    assert goal_cap(surplus,taken,0.8,net_of_goals=True) == expected


def test_goal_cap_on_the_measured_path_is_unchanged():
    assert goal_cap(97175,58300,0.8,net_of_goals=False) == 19440
