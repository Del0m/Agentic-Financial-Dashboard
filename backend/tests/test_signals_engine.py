import pytest

from app.budget_engine.signals import Signal, allowed_actions, cancel_limit, choose, fallback_message, rank_signals
from app.budget_engine.signals import PreviousDip, repeat_decision, move_amount
from datetime import date


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


def test_repeat_rule():
    previous=PreviousDip(date(2026,10,13),5000,5)
    assert not repeat_decision(previous,previous.date,5000,4)
    assert not repeat_decision(previous,previous.date,6250,4)
    assert repeat_decision(previous,previous.date,7000,4)
    assert repeat_decision(previous,previous.date,5000,2)
    assert not repeat_decision(PreviousDip(previous.date,5000,2),previous.date,5000,2)


def test_a_dip_that_slides_later_by_3_days_does_not_repeat_and_by_4_does():
    previous=PreviousDip(date(2026,10,13),5000,5)
    assert not repeat_decision(previous,date(2026,10,16),5000,5)
    assert repeat_decision(previous,date(2026,10,17),5000,5)


def test_move_amount():
    assert move_amount(5000,10000)==15000 and move_amount(9700,10000)==20000


def test_move_from_savings_is_downgraded_without_cover_or_with_no_income():
    assert choose(signal('low_balance',savings_cover=False),'move_from_savings')=='alert'
    assert choose(signal('low_balance',savings_cover=True,assumed_no_income=True),'move_from_savings')=='alert'
    assert choose(signal('low_balance',savings_cover=True),'move_from_savings')=='move_from_savings'


def test_delay_spend_is_downgraded_without_a_budget():
    assert choose(signal('low_balance',has_budget=False,flexible_ahead=True),'delay_spend')=='alert'
    assert choose(signal('low_balance',has_budget=True,flexible_ahead=True),'delay_spend')=='delay_spend'
