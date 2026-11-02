from datetime import date

from app.budget_engine.goals import build_goal, emergency_gap, monthly_surplus, offered_kinds
from app.budget_engine.payoff import Debt, simulate
from app.budget_engine.txns import SAVINGS, Txn

TODAY = date(2026, 11, 2)
START = date(2026, 12, 1)
RENT = frozenset({"RENT_AND_UTILITIES"})
CARD = Debt(1, "Card", "credit", 24.99, 320000, 9500)
LOAN = Debt(2, "Loan", "student", 5.5, 1840000, 21000)
DEBTS = [CARD, LOAN]


def T(y, m, d, cents, cat):
    return Txn(date=date(y, m, d), amount_cents=cents, category=cat)


# aug-oct with $4,200 of income, that month's spending and $500 to savings: $700, $400 and $900 left
def months(spend=(300000, 330000, 280000), saved=50000):
    out = []
    for m, cents in zip((8, 9, 10), spend):
        out += [T(2026, m, 1, -420000, "INCOME"), T(2026, m, 5, cents, "FOOD_AND_DRINK")]
        if saved:
            out.append(T(2026, m, 16, saved, SAVINGS))
    return out


def test_surplus_is_the_median_month():
    assert monthly_surplus(months(), TODAY) == 70000


# a $100 refund in august makes august's $700 left into $800, the new median of $800, $400 and $900
def test_refund_nets_its_category():
    assert monthly_surplus(months() + [T(2026, 8, 20, -10000, "FOOD_AND_DRINK")], TODAY) == 80000


def test_savings_transfers_subtract():
    assert monthly_surplus(months(saved=0), TODAY) == 120000


# history starting aug 15 only covers part of august, so only sep ($400) and oct ($900) count
def test_partial_first_month_skipped():
    txns = [t for t in months() if t.date >= date(2026, 8, 15)]
    assert monthly_surplus(txns, TODAY) == 65000


def test_negative_month_stays_negative():
    assert monthly_surplus(months(spend=(500000, 500000, 500000)), TODAY) == -130000


def test_no_complete_month_is_none():
    assert monthly_surplus([T(2026, 11, 1, 5000, "FOOD_AND_DRINK")], TODAY) is None
    assert monthly_surplus([], TODAY) is None


# needs months of $1,200, $1,500 and $1,300: the median $1,300 × 3 is $3,900, less $1,000 saved. food isn't a need here
def needs_months():
    return [T(2026, 8, 1, 120000, "RENT_AND_UTILITIES"), T(2026, 9, 1, 150000, "RENT_AND_UTILITIES"),
            T(2026, 10, 1, 130000, "RENT_AND_UTILITIES"), T(2026, 10, 5, 90000, "FOOD_AND_DRINK")]


def test_gap_is_three_needs_months_minus_savings():
    assert emergency_gap(needs_months(), TODAY, RENT, 100000) == 290000


def test_gap_floored_at_zero():
    assert emergency_gap(needs_months(), TODAY, RENT, 500000) == 0


def test_debt_goal_matches_the_planner():
    g = build_goal("debt_payoff", 1, 20000, "avalanche", "why", debts=DEBTS, gap_cents=0, start=START)
    want = simulate(DEBTS, 20000, "avalanche", start=START)
    assert (g.kind, g.priority, g.monthly_cents, g.rationale) == ("debt_payoff", 1, 20000, "why")
    assert (g.target_date, g.target_cents, g.strategy) == (want.debt_free, 2160000, "avalanche")


def test_debt_target_leaves_out_what_the_planner_does():
    mortgage = Debt(3, "Home", "mortgage", 4.0, 20000000, 120000)
    no_apr = Debt(4, "Store", "credit", None, 50000, 2500)
    overpaid = Debt(5, "Old", "credit", 20.0, -5000, 2500)
    g = build_goal("debt_payoff", 1, 20000, "avalanche", "why", debts=DEBTS + [mortgage, no_apr, overpaid], gap_cents=0,
                   start=START)
    assert g.target_cents == 2160000


# $10,000 at 30% is $250 of interest a month; $100 minimum and a $1 extra never catch it
def test_never_debt_goal_has_no_date():
    g = build_goal("debt_payoff", 1, 100, "avalanche", "why", debts=[Debt(1, "C", "credit", 30.0, 1000000, 10000)],
                   gap_cents=0, start=START)
    assert g.target_date is None and g.target_cents == 1000000


# $1,000 at $300 a month takes 4 payments: dec, jan, feb, mar
def test_emergency_date_rounds_up():
    g = build_goal("emergency_fund", 2, 30000, None, "why", debts=DEBTS, gap_cents=100000, start=START)
    assert (g.target_cents, g.target_date, g.strategy) == (100000, date(2027, 3, 1), None)


def test_emergency_exact_months():
    g = build_goal("emergency_fund", 2, 25000, None, "why", debts=DEBTS, gap_cents=100000, start=START)
    assert g.target_date == date(2027, 3, 1)


def test_savings_is_a_year():
    g = build_goal("savings", 3, 10000, None, "why", debts=DEBTS, gap_cents=0, start=START)
    assert (g.target_cents, g.target_date) == (120000, date(2027, 11, 1))


def test_offered_kinds():
    assert offered_kinds(DEBTS, 100000, (), start=START) == ["debt_payoff", "emergency_fund", "savings"]
    assert offered_kinds([], 100000, (), start=START) == ["emergency_fund", "savings"]
    assert offered_kinds([Debt(4, "Store", "credit", None, 50000, 2500)], 0, (), start=START) == ["savings"]
    assert offered_kinds(DEBTS, 0, (), start=START) == ["debt_payoff", "savings"]
    assert offered_kinds(DEBTS, 100000, ("savings", "debt_payoff"), start=START) == ["emergency_fund"]
