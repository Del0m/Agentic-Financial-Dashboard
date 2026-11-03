import pytest

from app.budget_engine.signals import Signal, allowed_actions, cancel_limit, choose, fallback_message, rank_signals


def signal(kind="price_increase", category="PERSONAL_CARE", **numbers):
    return Signal(kind, f"{kind}|1|2026-10-03", 1, category,
                  {"merchant": "FitClub Gym", "previous_cents": 4000, "last_cents": 4300, **numbers})


@pytest.mark.parametrize("limit,monthly,expected", [(10000, 1599, 8500), (1599, 1599, None),
                                                   (1000, 1599, None), (600, 100, 500), (10000, 9996, 500)])
def test_cancel_limit(limit, monthly, expected):
    assert cancel_limit(limit, monthly) == expected


def test_rank_puts_low_balance_then_income_then_price_then_new():
    signals = [signal("new_subscription"), signal(last_cents=4400), signal("income_change"),
               signal("low_balance"), signal(last_cents=5000)]
    assert rank_signals(signals) == [signals[i] for i in (3, 2, 4, 1, 0)]


@pytest.mark.parametrize("category", ["RENT_AND_UTILITIES", "LOAN_PAYMENTS", "MEDICAL",
                                     "GOVERNMENT_AND_NON_PROFIT", "TRANSFER_IN", "TRANSFER_OUT", "SAVINGS_TRANSFER"])
def test_rent_and_transfers_cannot_be_cancelled(category):
    assert allowed_actions(signal(category=category)) == ("alert",)
    assert choose(signal(category=category), "suggest_cancel") == "alert"


def test_income_change_only_alerts():
    assert allowed_actions(signal("income_change", "INCOME")) == ("alert",)
    assert choose(signal("income_change", "INCOME"), "suggest_cancel") == "alert"


def test_discretionary_stream_can_be_cancelled_and_unknown_actions_downgrade():
    assert choose(signal(), "suggest_cancel") == "suggest_cancel"
    assert choose(signal(), "adjust_next_budget") == "alert"


def test_fallback_price_message_quotes_the_old_and_new_price():
    message = fallback_message(signal())
    assert all(text in message for text in ("FitClub Gym", "$40.00", "$43.00"))
