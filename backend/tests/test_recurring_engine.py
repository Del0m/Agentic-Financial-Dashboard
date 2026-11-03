from datetime import date, timedelta

import pytest

from app.budget_engine.recurring import RTxn, Stream, detect, merchant_key, monthly_cents, project


def txn(day, cents, merchant="Streamly", category="ENTERTAINMENT", detailed=None):
    return RTxn(day, cents, merchant, category, detailed)


def monthly(months, cents, day=5, year=2026, **kw):
    return [txn(date(year, m, day), c, **kw) for m, c in zip(months, cents)]


def one(txns, today):
    streams = detect(txns, today)
    assert len(streams) == 1
    return streams[0]


def every(first, days, count, cents, **kw):
    return [txn(first + timedelta(days=days * i), cents, **kw) for i in range(count)]


def after(txns):
    return max(t.date for t in txns) + timedelta(days=1)


def stream(**kw):
    base = dict(direction="out", merchant_key="x", merchant_name="X", category="ENTERTAINMENT", detailed=None,
                frequency="monthly", typical_cents=1000, last_cents=1000, previous_cents=None, amount_varies=False,
                first_date=date(2026, 1, 1), last_date=date(2026, 3, 1), next_date=date(2026, 4, 1), gap_days=30,
                anchors=(1,), n=3, status="active")
    return Stream(**{**base, **kw})


def test_two_visits_make_no_stream():
    assert detect(monthly((9, 10), (4000, 4000), day=3, merchant="Gym"), date(2026, 10, 20)) == []


def test_three_monthly_charges_make_a_stream():
    s = one(monthly((8, 9, 10), (1599, 1599, 1599)), date(2026, 10, 20))
    assert (s.frequency, s.n, s.typical_cents, s.last_cents, s.amount_varies, s.next_date, s.status) == (
        "monthly", 3, 1599, 1599, False, date(2026, 11, 5), "active")


def test_gaps_of_5_and_80_days_make_no_stream():
    days = (date(2026, 6, 1), date(2026, 6, 6), date(2026, 8, 25))
    assert detect([txn(d, 1599) for d in days], date(2026, 9, 1)) == []


def test_next_date_uses_the_median_day_of_the_month():
    days = (date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30))
    s = one([txn(d, 1599) for d in days], date(2026, 10, 1))
    assert (s.anchors, s.next_date) == ((31,), date(2026, 10, 31))


def test_a_weekend_shifted_bill_keeps_its_day():
    days = (date(2026, 8, 3), date(2026, 9, 1), date(2026, 10, 1))
    s = one([txn(d, 1599) for d in days], date(2026, 10, 2))
    assert (s.anchors, s.next_date) == ((1,), date(2026, 11, 1))


def test_biweekly_pay():
    s = one(every(date(2026, 5, 1), 14, 6, -185000, merchant="Employer", category="INCOME"), date(2026, 7, 20))
    assert (s.frequency, s.gap_days, s.typical_cents, s.direction) == ("biweekly", 14, 185000, "in")


def test_one_weekday_means_biweekly_and_two_anchors_mean_semi_monthly():
    fridays = every(date(2026, 5, 1), 14, 5, 6000, merchant="Cleaner", category="GENERAL_SERVICES")
    assert one(fridays, after(fridays)).frequency == "biweekly"
    days = (date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1), date(2026, 7, 15), date(2026, 8, 1))
    s = one([txn(d, 6000, merchant="Cleaner", category="GENERAL_SERVICES") for d in days], date(2026, 8, 2))
    assert (s.frequency, s.anchors, s.next_date) == ("semi_monthly", (1, 15), date(2026, 8, 15))


def test_price_rise_of_6_7_percent_keeps_the_stream_fixed():
    s = one(monthly((6, 7, 8, 9), (3000, 3000, 3000, 3200)), date(2026, 9, 6))
    assert (s.previous_cents, s.last_cents, s.amount_varies) == (3000, 3200, False)


def test_price_rise_of_15_percent_keeps_the_stream_fixed():
    s = one(monthly((6, 7, 8, 9), (3000, 3000, 3000, 3450)), date(2026, 9, 6))
    assert (s.typical_cents, s.last_cents, s.previous_cents, s.amount_varies) == (3000, 3450, 3000, False)


def test_second_charge_at_the_new_price_is_not_a_rise():
    assert one(monthly((6, 7, 8, 9), (3000, 3000, 3450, 3450)), date(2026, 9, 6)).previous_cents is None


def test_a_price_step_keeps_the_stream():
    s = one(monthly((4, 5, 6, 7, 8, 9), (3000, 3000, 3000, 3000, 3450, 3450)), date(2026, 9, 6))
    assert (s.frequency, s.n, s.typical_cents, s.last_cents, s.previous_cents, s.amount_varies, s.next_date) == (
        "monthly", 6, 3450, 3450, None, False, date(2026, 10, 5))


def test_a_small_rise_alerts_only_on_its_first_charge():
    assert one(monthly((5, 6, 7, 8), (4000, 4000, 4000, 4300)), date(2026, 8, 6)).previous_cents == 4000
    assert one(monthly((5, 6, 7, 8, 9), (4000, 4000, 4000, 4300, 4300)), date(2026, 9, 6)).previous_cents is None


def test_a_lower_price_that_repeats_keeps_the_stream():
    s = one(monthly((4, 5, 6, 7, 8, 9), (3000, 3000, 3000, 3000, 2500, 2500)), date(2026, 9, 6))
    assert (s.typical_cents, s.last_cents, s.previous_cents) == (2500, 2500, None)


def test_a_rise_over_50_percent_counts_on_its_second_charge():
    s = one(monthly((4, 5, 6, 7, 8), (3000, 3000, 3000, 3000, 5000)), date(2026, 8, 6))
    assert (s.last_cents, s.previous_cents) == (3000, None)
    s = one(monthly((4, 5, 6, 7, 8, 9), (3000, 3000, 3000, 3000, 5000, 5000)), date(2026, 9, 6))
    assert (s.typical_cents, s.last_cents, s.previous_cents) == (5000, 5000, 3000)


def test_a_price_that_falls_back_and_rises_again_is_a_rise():
    s = one(monthly((4, 5, 6, 7, 8, 9), (3000, 3450, 3000, 3000, 3000, 3450)), date(2026, 9, 6))
    assert (s.last_cents, s.previous_cents) == (3450, 3000)


def test_a_one_off_extra_is_dropped():
    txns = monthly((6, 7, 8, 9), (12000,) * 4, day=8, merchant="MegaMart") + [
        txn(date(2026, 9, 12), 52000, merchant="MegaMart")]
    s = one(txns, date(2026, 9, 20))
    assert (s.n, s.typical_cents, s.last_cents) == (4, 12000, 12000)


def test_a_small_extra_between_bills_is_dropped():
    txns = monthly((7, 8, 9), (1599,) * 3) + [txn(date(2026, 9, 20), 500)]
    s = one(txns, date(2026, 9, 25))
    assert (s.last_cents, s.n) == (1599, 3)


def test_one_offs_a_month_apart_are_not_a_stream():
    days = ((date(2026, 5, 12), 20000), (date(2026, 6, 10), 120000), (date(2026, 7, 12), 25000))
    assert detect([txn(d, c, merchant="Airline", category="TRAVEL") for d, c in days], date(2026, 7, 20)) == []


def test_a_utility_with_varying_amounts_is_a_stream():
    s = one(monthly((6, 7, 8, 9), (8000, 11500, 9400, 10200), merchant="City Power", category="RENT_AND_UTILITIES"),
            date(2026, 9, 6))
    assert (s.amount_varies, s.typical_cents) == (True, 10200)


def test_weekly_food_with_fixed_amounts_is_not_a_stream():
    assert detect(every(date(2026, 5, 2), 7, 13, 11000, merchant="Grocer", category="FOOD_AND_DRINK"),
                  date(2026, 8, 1)) == []


def test_a_weekly_service_with_fixed_amounts_is_a_stream():
    s = one(every(date(2026, 5, 2), 7, 6, 6000, merchant="Cleaner", category="GENERAL_SERVICES"), date(2026, 6, 7))
    assert (s.frequency, s.n) == ("weekly", 6)


def test_a_weekly_outflow_with_varying_amounts_is_not():
    days = every(date(2026, 5, 2), 7, 6, 0, merchant="Cleaner", category="GENERAL_SERVICES")
    amounts = (4000, 9000, 5000, 11000, 6000, 12000)
    assert detect([txn(t.date, c, merchant="Cleaner", category="GENERAL_SERVICES") for t, c in zip(days, amounts)],
                  date(2026, 6, 7)) == []


def _gapped(first, gaps, cents=6000, merchant="Cleaner", category="GENERAL_SERVICES"):
    out, d = [txn(first, cents, merchant=merchant, category=category)], first
    for g in gaps:
        d += timedelta(days=g)
        out.append(txn(d, cents, merchant=merchant, category=category))
    return out


def test_a_weekly_gap_that_slips_by_3_days_is_not_a_stream():
    assert detect(_gapped(date(2026, 5, 2), (7, 7, 10, 7)), date(2026, 6, 15)) == []


def test_a_skipped_month_keeps_the_stream():
    s = one(monthly((4, 5, 7, 8), (1599,) * 4), date(2026, 8, 10))
    assert (s.frequency, s.n, s.next_date, s.status) == ("monthly", 4, date(2026, 9, 5), "active")


def test_a_skip_needs_four_charges():
    assert detect(monthly((8, 9, 11), (1599,) * 3), date(2026, 11, 10)) == []


def test_two_skipped_months_make_no_stream():
    assert detect(monthly((4, 5, 7, 9, 10), (1599,) * 5), date(2026, 10, 10)) == []


def test_a_longer_break_restarts_the_series():
    days = [date(2026, 4, 5), date(2026, 5, 5), date(2026, 7, 20), date(2026, 8, 20), date(2026, 9, 20)]
    s = one([txn(d, 1599) for d in days], date(2026, 9, 25))
    assert (s.n, s.first_date, s.anchors) == (3, date(2026, 7, 20), (20,))
    assert detect([txn(d, 1599) for d in days[:-1]], date(2026, 8, 25)) == []


def test_a_skipped_week_restarts_a_weekly_service():
    s = one(_gapped(date(2026, 5, 2), (7, 7, 14, 7, 7, 7)), date(2026, 6, 25))
    assert (s.frequency, s.n) == ("weekly", 4)


def test_a_cafe_visited_at_random_is_not_a_stream():
    assert detect(_gapped(date(2026, 5, 2), (9, 9, 8, 10), merchant="Cafe", category="FOOD_AND_DRINK"),
                  date(2026, 6, 15)) == []


def test_charges_after_today_and_before_the_window_are_ignored():
    txns = monthly((1, 2, 3, 8, 9, 11), (1599,) * 6)
    assert detect(txns, date(2026, 10, 20)) == []
    assert one(monthly((8, 9, 10, 11), (1599,) * 4), date(2026, 10, 20)).n == 3


def test_a_null_merchant_paycheck_is_a_stream():
    txns = monthly((7, 8, 9), (-420000,) * 3, merchant=None, category="INCOME", detailed="INCOME_WAGES")
    s = one(txns, date(2026, 9, 6))
    assert (s.merchant_key, s.direction) == ("~INCOME_WAGES", "in")


def test_null_merchant_restaurant_charges_are_not():
    assert detect(every(date(2026, 5, 2), 7, 12, 2500, merchant=None, category="FOOD_AND_DRINK"),
                  date(2026, 8, 1)) == []


def test_raw_descriptors_share_a_key():
    assert merchant_key("NETFLIX.COM 8443") == merchant_key("Netflix.com 1123") == "netflixcom"
    assert merchant_key("7-Eleven") == "eleven"
    assert merchant_key("12345") is None and merchant_key("") is None and merchant_key(None) is None


def test_paycheck_goes_stale_after_65_days():
    txns = monthly((3, 4, 5), (-420000,) * 3, day=15, merchant="Employer", category="INCOME")
    assert one(txns, date(2026, 6, 20)).status == "active"
    assert one(txns, date(2026, 7, 19)).status == "active"
    assert one(txns, date(2026, 7, 20)).status == "stale"


def test_a_roommate_transfer_is_an_inflow_stream_but_not_income():
    s = one(monthly((7, 8, 9), (-60000,) * 3, merchant="Sam", category="TRANSFER_IN"), date(2026, 9, 6))
    assert (s.direction, s.category) == ("in", "TRANSFER_IN")


def test_a_refund_is_its_own_direction():
    txns = monthly((7, 8, 9), (1599,) * 3) + [txn(date(2026, 8, 20), -1599)]
    s = one(txns, date(2026, 9, 6))
    assert (s.direction, s.n, s.last_cents) == ("out", 3, 1599)


def test_project_clamps_a_31st_bill():
    s = stream(anchors=(31,), next_date=date(2027, 1, 31))
    assert project(s, date(2027, 4, 30)) == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]


def test_project_semi_monthly_uses_both_anchors():
    s = stream(frequency="semi_monthly", anchors=(1, 15), next_date=date(2026, 6, 15), gap_days=15)
    assert project(s, date(2026, 7, 15)) == [date(2026, 6, 15), date(2026, 7, 1), date(2026, 7, 15)]


def test_project_steps_a_fixed_cadence():
    s = stream(frequency="biweekly", anchors=(), next_date=date(2026, 6, 5), gap_days=14)
    assert project(s, date(2026, 6, 20)) == [date(2026, 6, 5), date(2026, 6, 19)]


@pytest.mark.parametrize("frequency,expected", [("monthly", 1000), ("biweekly", 2167), ("semi_monthly", 2000),
                                                ("weekly", 4333)])
def test_monthly_cents_normalises(frequency, expected):
    assert monthly_cents(1000, frequency) == expected


def test_price_rise_of_exactly_5_percent_keeps_its_previous_price():
    s = one(monthly((6, 7, 8, 9), (2000, 2000, 2000, 2100)), date(2026, 9, 6))
    assert (s.previous_cents, s.last_cents) == (2000, 2100)


def test_price_rise_under_5_percent_has_no_previous_price():
    assert one(monthly((6, 7, 8, 9), (2000, 2000, 2000, 2099)), date(2026, 9, 6)).previous_cents is None
