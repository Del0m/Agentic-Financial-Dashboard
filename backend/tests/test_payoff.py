from datetime import date

from app.budget_engine.payoff import Debt, compare, simulate

START = date(2026, 12, 1)


def D(key, apr, balance, minimum, kind="credit", name=None):
    return Debt(key=key, name=name or f"debt {key}", kind=kind, apr=apr, balance_cents=round(balance * 100),
                min_payment_cents=None if minimum is None else round(minimum * 100))


# A: $150 at 12%, min $100. B: $300 at 24%, min $50. extra $100, so the budget is $250 a month
def test_hand_computed_avalanche():
    plan = simulate([D(1, 12, 150, 100), D(2, 24, 300, 50)], 10000, "avalanche", start=START)
    assert (plan.months, plan.debt_free, plan.total_interest_cents) == (2, date(2027, 1, 1), 1114)
    assert [(p.key, p.month, p.interest_cents) for p in plan.order] == [(1, 2, 202), (2, 2, 912)]


def test_hand_computed_snowball():
    plan = simulate([D(1, 12, 150, 100), D(2, 24, 300, 50)], 10000, "snowball", start=START)
    assert (plan.months, plan.total_interest_cents) == (2, 1165)
    assert [(p.key, p.month, p.interest_cents) for p in plan.order] == [(1, 1, 150), (2, 2, 1015)]


# a big high-APR card and a small low-APR loan: avalanche clears the card first, snowball the loan
def test_strategies_target_different_debts():
    debts = [D(1, 24.99, 3200, 95), D(2, 5.5, 900, 50)]
    assert simulate(debts, 20000, "avalanche", start=START).order[0].key == 1
    assert simulate(debts, 20000, "snowball", start=START).order[0].key == 2


def test_rollover_speeds_up_the_next_debt():
    debts = [D(1, 20, 500, 100), D(2, 10, 2000, 60)]
    alone = simulate([debts[1]], 0, "avalanche", start=START)
    both = simulate(debts, 0, "avalanche", start=START)
    assert next(p.month for p in both.order if p.key == 2) < alone.months


# month 1: the card ($40.80 after interest, $15.80 after its minimum) is cleared by the pool, and the pool's
# other $184.20 reaches the loan that same month. with spill-over the loan clears in month 4; without, month 5
def test_spill_over_in_the_clearing_month():
    plan = simulate([D(1, 24, 40, 25), D(2, 6, 1000, 50)], 20000, "avalanche", start=START)
    assert [(p.key, p.month) for p in plan.order] == [(1, 1), (2, 4)]


# $10,000 at 30%: $250 of interest a month against a $100 minimum
def test_never_names_the_debt():
    plan = simulate([D(1, 30, 10000, 100)], 0, "avalanche", start=START)
    assert (plan.months, plan.debt_free, plan.never) == (None, None, (1,))
    assert simulate([D(1, 30, 10000, 100)], 30000, "avalanche", start=START).months is not None


def test_excluded_with_reasons():
    debts = [D(1, None, 500, 25), D(2, 7, 900, None), D(3, 4, 200000, 1200, kind="mortgage"), D(4, 18, 800, 40)]
    plan = simulate(debts, 0, "avalanche", start=START)
    assert plan.excluded == ((1, "no_apr"), (2, "no_minimum"), (3, "mortgage"))
    assert [p.key for p in plan.order] == [4]
    with_mortgage = simulate(debts, 0, "avalanche", start=START, include_mortgage=True)
    assert (3, "mortgage") not in with_mortgage.excluded
    assert 3 in [p.key for p in with_mortgage.order]


# a promo card at 0% is a real rate, not a missing one
def test_zero_apr_is_a_rate():
    plan = simulate([D(1, 0.0, 300, 100)], 0, "avalanche", start=START)
    assert plan.excluded == () and (plan.months, plan.total_interest_cents) == (3, 0)


# an overpaid card (a credit balance) has nothing to pay off
def test_credit_balance_left_out_silently():
    plan = simulate([D(1, 20, -50, 25), D(2, 20, 100, 25)], 0, "avalanche", start=START)
    assert plan.excluded == () and [p.key for p in plan.order] == [2]


def test_extra_clears_everything_in_month_one():
    plan = simulate([D(1, 20, 300, 25), D(2, 10, 200, 25)], 100000, "avalanche", start=START)
    assert plan.months == 1 and all(p.month == 1 for p in plan.order)


def test_no_debts_and_all_excluded():
    assert simulate([], 0, "avalanche", start=START).months == 0
    plan = simulate([D(1, None, 500, 25)], 0, "snowball", start=START)
    assert (plan.months, plan.order, plan.excluded) == (0, (), ((1, "no_apr"),))


def test_minimum_capped_at_balance():
    plan = simulate([D(1, 12, 30, 100)], 0, "avalanche", start=START)
    assert (plan.months, plan.total_interest_cents) == (1, 30)


def test_compare_baselines_and_extra_never_hurts():
    debts = [D(1, 24.99, 3200, 95), D(2, 5.5, 18400, 210)]
    plans = compare(debts, 20000, start=START)
    assert plans["baseline_avalanche"] == simulate(debts, 0, "avalanche", start=START)
    assert plans["baseline_snowball"] == simulate(debts, 0, "snowball", start=START)
    for s in ("avalanche", "snowball"):
        assert plans[s] == simulate(debts, 20000, s, start=START)
        assert plans[s].months <= plans[f"baseline_{s}"].months
        assert plans[s].total_interest_cents <= plans[f"baseline_{s}"].total_interest_cents


# review: a debt with nothing owed is skipped before any reason is checked, so a paid-off card or mortgage with
# missing details isn't "left out of the plan"
def test_nothing_owed_never_excluded():
    plan = simulate([D(1, None, 0, 25), D(2, None, -50, None), D(3, 4, 0, 1200, kind="mortgage"), D(4, 18, 800, 40)],
                    0, "avalanche", start=START)
    assert plan.excluded == () and [p.key for p in plan.order] == [4]


def test_goal_contribution_stops_after_its_target_month():
    from app.budget_engine.payoff import ScheduledExtra
    from app.budget_engine.txns import add_months
    goal = ScheduledExtra(20000, START, add_months(START, 1))
    # Two $300 payments, then four $100 payments. A permanent extra would finish in four months.
    plan = simulate([D(1, 0, 1000, 100)], 0, 'avalanche', start=START, scheduled_extras=[goal])
    assert plan.months == 6


def test_future_goal_waits_for_start_and_manual_extra_is_additional():
    from app.budget_engine.payoff import ScheduledExtra
    from app.budget_engine.txns import add_months
    later = add_months(START, 1)
    goal = ScheduledExtra(20000, later, later)
    assert simulate([D(1, 0, 1000, 100)], 0, 'avalanche', start=START,
                    scheduled_extras=[goal]).months == 8
    plans = compare([D(1, 0, 1000, 100)], 10000, start=START, scheduled_extras=[goal])
    assert plans['avalanche'].months == 4
    assert plans['baseline_avalanche'].months == 8
