from datetime import date
from decimal import Decimal

import pytest

from app.budget_engine.txns import (GOAL_CATEGORIES, GOAL_DEBT_PAYOFF, GOAL_EMERGENCY_FUND, GOAL_SAVINGS, LOAN_PAYMENTS,
                                    SAVINGS, Txn, add_months, category_totals, income_by_month, month_end,
                                    month_start, net_of_goal, normalize_merchant, round_cents, savings, spending)


def t(day, cents, cat="FOOD_AND_DRINK", detailed=None, month=10, year=2026):
    return Txn(date=date(year, month, day), amount_cents=cents, category=cat, detailed=detailed)


def test_spending_drops_non_spending():
    car = t(3, 30000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CAR_PAYMENT")
    food = t(4, 4210)
    txns = [
        t(1, -250000, "INCOME"),
        t(2, -50000, "TRANSFER_IN"),
        car,
        t(2, 50000, "TRANSFER_OUT"),
        t(3, 80000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
        food,
    ]
    assert spending(txns) == [car, food]


def test_totals_net_refunds():
    txns = [t(1, 5000), t(2, 3000), t(3, -2000)]
    assert category_totals(txns, date(2026, 10, 1), date(2026, 10, 31)) == {"FOOD_AND_DRINK": 6000}


def test_totals_floor_at_zero():
    txns = [t(1, 1000, "GENERAL_MERCHANDISE"), t(2, -4000, "GENERAL_MERCHANDISE")]
    assert category_totals(txns, date(2026, 10, 1), date(2026, 10, 31)) == {"GENERAL_MERCHANDISE": 0}


def test_totals_range_inclusive():
    txns = [t(30, 100, month=9), t(1, 100), t(31, 100), t(1, 100, month=11)]
    assert category_totals(txns, date(2026, 10, 1), date(2026, 10, 31)) == {"FOOD_AND_DRINK": 200}


def test_totals_omit_absent_categories():
    assert category_totals([t(1, 100, month=9)], date(2026, 10, 1), date(2026, 10, 31)) == {}


def test_month_helpers_leap_year():
    assert month_end(date(2028, 2, 10)) == date(2028, 2, 29)
    assert month_end(date(2027, 2, 1)) == date(2027, 2, 28)
    assert month_start(date(2028, 2, 29)) == date(2028, 2, 1)


def test_add_months_crosses_year():
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert add_months(date(2027, 1, 1), -1) == date(2026, 12, 1)
    assert add_months(date(2026, 10, 1), -3) == date(2026, 7, 1)
    with pytest.raises(ValueError):
        add_months(date(2026, 10, 15), 1)


def test_round_cents_half_up():
    assert round_cents(Decimal("1010.5")) == 1011
    assert round_cents(Decimal("1010.49")) == 1010
    assert round_cents(Decimal("47250.00")) == 47250


def test_spending_drops_savings():
    food = t(4, 4210)
    assert spending([t(16, 50000, SAVINGS), food]) == [food]


# a user who moves a card payment into a line means it; plaid's detailed category no longer excludes it
def test_spending_keeps_overridden_card_payment():
    moved = Txn(date=date(2026, 10, 3), amount_cents=8000, category="FOOD_AND_DRINK",
                detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", overridden=True)
    plain = t(3, 8000, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    assert spending([moved, plain]) == [moved]


def test_savings_selects_transfers():
    saved = t(16, 50000, SAVINGS)
    assert savings([t(2, 50000, "TRANSFER_OUT"), saved, t(4, 4210)]) == [saved]


def test_income_by_month():
    txns = [t(15, -420000, "INCOME", month=8), t(15, -420000, "INCOME", month=9), t(3, 4210, month=9),
            t(20, 1000, "INCOME", month=9)]     # a reversed deposit nets the month down
    months = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
    assert income_by_month(txns, months) == [420000, 419000, 0]


def test_normalize_merchant():
    assert normalize_merchant("  COSTCO ") == normalize_merchant("Costco") == "costco"


def test_net_of_goal_only_attributes_what_is_above_the_limit():
    assert net_of_goal(21000, 21000, 20000) == 21000    # the goal went to a card; the loan payment is unchanged
    assert net_of_goal(79300, 21000, 58300) == 21000    # the whole goal went to the loan
    assert net_of_goal(30000, 21000, 58300) == 21000    # part of it did
    assert net_of_goal(10000, 21000, 58300) == 10000    # a smaller payment is still adopted
    assert net_of_goal(58300, 0, 58300) == 0            # no line: all of it is the goal


def test_goal_categories_map_each_kind():
    assert GOAL_CATEGORIES == {"debt_payoff": GOAL_DEBT_PAYOFF, "emergency_fund": GOAL_EMERGENCY_FUND,
                               "savings": GOAL_SAVINGS}
    assert (GOAL_DEBT_PAYOFF, GOAL_EMERGENCY_FUND, GOAL_SAVINGS, LOAN_PAYMENTS) == (
        "GOAL_DEBT_PAYOFF", "GOAL_EMERGENCY_FUND", "GOAL_SAVINGS", "LOAN_PAYMENTS")
