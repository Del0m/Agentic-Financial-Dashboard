from datetime import date

from app.budget_engine.payoff import Debt, simulate


def test_non_amortizing_high_apr_does_not_overflow_decimal_precision():
    debt = Debt(1, "High APR", "credit", 120.0, 1000000, 100)
    result = simulate([debt], 0, "avalanche", start=date(2026, 12, 1))
    assert result.months is None and result.never == (1,)
    assert result.total_interest_cents > 0
