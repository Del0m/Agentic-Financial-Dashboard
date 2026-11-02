from datetime import date

import pytest

from app.budget_engine import goals

START = date(2026, 12, 1)
OFFERED = ('debt_payoff', 'emergency_fund', 'savings')


def allocation(kind='savings', dollars=10, strategy=None):
    return goals.normalize_goal(kind, dollars, strategy, 'Save for later.')


@pytest.mark.parametrize('dollars,cents', [(1.004, 100), (1.005, 101), (10.125, 1013)])
def test_monthly_amount_rounds_half_up_once(dollars, cents):
    assert allocation(dollars=dollars).monthly_cents == cents


def test_missing_debt_strategy_defaults_to_avalanche():
    assert allocation('debt_payoff').strategy == 'avalanche'
    assert allocation('savings', strategy='snowball').strategy is None


def test_combined_exact_cap_passes_and_one_cent_over_fails():
    proposed = [allocation('savings', 10.005), allocation('emergency_fund', 10.005)]
    assert goals.validate_allocations(proposed, cap_cents=2002, offered=OFFERED) == []
    [issue] = goals.validate_allocations(proposed, cap_cents=2001, offered=OFFERED)
    assert issue.code == 'over_cap'
    assert '$20.02' in issue.message and '$20.01' in issue.message and '$0.01' in issue.message


def test_duplicate_and_unoffered_kinds_report_all_failures():
    proposed = [allocation('savings'), allocation('savings'), allocation('emergency_fund')]
    issues = goals.validate_allocations(proposed, cap_cents=2000, offered=('savings',))
    assert {i.code for i in issues} == {'repeat', 'not_offered', 'over_cap'}
    assert [(i.index, i.kind) for i in issues if i.code == 'repeat'] == [(2, 'savings')]


@pytest.mark.parametrize('cents', [0, 99, 100_000_001])
def test_monthly_bounds_reject_unsafe_allocations(cents):
    proposed = [goals.GoalAllocation('savings', cents, None, 'x')]
    assert 'bad_amount' in {i.code for i in goals.validate_allocations(
        proposed, cap_cents=200_000_000, offered=OFFERED)}


@pytest.mark.parametrize('count', [0, 4])
def test_proposal_count_is_bounded(count):
    assert 'bad_count' in {i.code for i in goals.validate_allocations(
        [allocation()] * count, cap_cents=10000, offered=OFFERED)}


@pytest.mark.parametrize('kind', OFFERED)
def test_computed_dated_plans_pass(kind):
    from tests.test_goals import DEBTS
    plan = goals.build_goal(kind, 1, 20000, 'avalanche', 'x', debts=DEBTS, gap_cents=100000, start=START)
    assert goals.validate_plans([plan], start=START) == []


@pytest.mark.parametrize('months,valid', [(600, True), (601, False)])
def test_emergency_horizon_boundary(months, valid):
    plan = goals.build_goal('emergency_fund', 1, 100, None, 'x', debts=[], gap_cents=months * 100, start=START)
    issues = goals.validate_plans([plan], start=START)
    assert (not issues) == valid
    if not valid:
        assert issues[0].code == 'no_date' and '600' in issues[0].message


def test_non_amortizing_debt_is_rejected():
    from app.budget_engine.payoff import Debt
    plan = goals.build_goal('debt_payoff', 1, 100, 'avalanche', 'x',
                            debts=[Debt(1, 'Card', 'credit', 30, 1000000, 10000)], gap_cents=0, start=START)
    assert [i.code for i in goals.validate_plans([plan], start=START)] == ['no_date']


def test_invalid_target_and_past_or_outside_date_are_rejected():
    from dataclasses import replace
    plan = goals.build_goal('savings', 1, 100, None, 'x', debts=[], gap_cents=0, start=START)
    assert {i.code for i in goals.validate_plans([replace(plan, target_cents=0, target_date=date(2026, 11, 1))],
                                               start=START)} == {'bad_target', 'bad_date'}
    assert [i.code for i in goals.validate_plans([replace(plan, target_date=date(2076, 12, 1))],
                                                start=START)] == ['bad_date']
