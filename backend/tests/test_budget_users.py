from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest

from app.budget_engine.fit import fit_budget
from app.budget_engine.goals import monthly_surplus
from app.budget_engine.pace import BudgetLine, evaluate
from app.budget_engine.txns import Txn, month_start
from app.budget_service.debts import load_debts
from app.budget_service.initialize import load_txns
from app.db.models import Account, Transaction, User
from eval.budget_users import (FORECAST_USERS, GOAL_USERS, PLANTED, PROFILES, RECURRING_USERS, SCENARIO_USERS, Every, Monthly,
                               Scatter, default_start, profile_by_id, seed_profile, transactions)


def profile(pid):
    return next(p for p in PROFILES if p.id == pid)


def test_same_seed_same_transactions():
    p = profile("steady")
    assert transactions(p) == transactions(p)


def test_vacation_june_travel():
    june = [c for d, c, cat, _ in transactions(profile("vacation")) if cat == "TRAVEL" and d.month == 6]
    assert sum(june) == 190000


def test_early_rent_doubles_june():
    rent = [(d, c) for d, c, cat, _ in transactions(profile("early-rent")) if cat == "RENT_AND_UTILITIES"]
    assert sum(c for d, c in rent if d.month == 6) == 300000
    assert not [d for d, _ in rent if d.month == 7]


def test_rising_groceries_grow():
    p = profile("rising")
    by_month = {}
    for d, c, _, merchant in transactions(p):
        if merchant == "Grocer":
            by_month.setdefault(d.month, []).append(c)
    avg = {m: sum(cs) / len(cs) for m, cs in by_month.items()}
    assert avg[5] < avg[6] < avg[7] < avg[8]


def test_every_profile_has_income_and_transfer():
    for p in PROFILES:
        cats = {cat for _, _, cat, _ in transactions(p)}
        assert {"INCOME", "TRANSFER_OUT"} <= cats


def test_transactions_stay_in_range():
    for p in PROFILES:
        days = [d for d, _, _, _ in transactions(p)]
        assert min(days) >= p.start and max(days) <= p.end


def test_profile_ids():
    assert [p.id for p in PROFILES] == ["steady", "vacation", "early-rent", "rising", "occasional"]


def test_seed_profile_writes_rows(db):
    p = profile("steady")
    with db() as s:
        uid = seed_profile(s, p)
    with db() as s:
        assert s.query(Transaction).filter_by(user_id=uid).count() == len(transactions(p))


# --- planted overspend users (TPC-19) ---

def planted(pid):
    return next(p for p in PLANTED if p.id == pid)


def test_planted_one_time_purchase():
    assert (date(2026, 10, 12), 60000, "GENERAL_MERCHANDISE", "ElectroMart") in transactions(planted("overspend-one-time"))


def test_planted_gym_starts_september():
    gym = [d for d, _, _, m in transactions(planted("overspend-recurring")) if m == "FitClub Gym"]
    assert gym == [date(2026, 9, 3), date(2026, 10, 3)]


def test_planted_december_shops():
    dec = {m for d, _, cat, m in transactions(planted("overspend-seasonal"))
           if d.month == 12 and cat == "GENERAL_MERCHANDISE" and m != "Corner Shop"}
    assert len(dec) == 8


def test_planted_flags_only_the_planted_category():
    # fit on the three months before run_date, pace on run_date: exactly one flag, the planted one
    for p in PLANTED:
        txns = [Txn(d, c, cat, merchant=m) for d, c, cat, m in transactions(p)]
        start = month_start(p.run_date)
        fit = fit_budget([t for t in txns if t.date < start], p.run_date)
        lines = [BudgetLine(ln.category, ln.kind, ln.limit_cents) for ln in fit.lines]
        flagged = [r.category for r in evaluate(lines, [t for t in txns if t.date <= p.run_date], p.run_date)
                   if r.flagged]
        assert flagged == [{"overspend-recurring": "PERSONAL_CARE"}.get(p.id, "GENERAL_MERCHANDISE")], p.id


def test_planted_carry_run_date_and_cause():
    assert {p.id: (p.run_date, p.expected_cause) for p in PLANTED} == {
        "overspend-one-time": (date(2026, 10, 20), "one_time"),
        "overspend-recurring": (date(2026, 10, 20), "recurring"),
        "overspend-seasonal": (date(2026, 12, 15), "seasonal"),
        "overspend-known-merchant": (date(2026, 10, 20), "one_time")}


def test_planted_known_merchant_big_purchase():
    mega = [(d, c) for d, c, _, m in transactions(planted("overspend-known-merchant")) if m == "MegaMart"]
    assert (date(2026, 10, 12), 52000) in mega
    assert [d for d, c in mega if c == 12000] == [date(2026, m, 8) for m in (7, 8, 9, 10)]


def test_profiles_unchanged():
    assert [p.id for p in PROFILES] == ["steady", "vacation", "early-rent", "rising", "occasional"]
    assert all(p.run_date is None for p in PROFILES)



# --- seeding up to a start date (TPC-21) ---

def seeded_rows(db, pid, until=None):
    p = next(x for x in PLANTED + PROFILES if x.id == pid)
    with db() as s:
        uid = seed_profile(s, p, until=until)
    with db() as s:
        rows = s.query(Transaction).filter_by(user_id=uid).order_by(Transaction.id).all()
        return p, rows, s.get(User, uid).prefs_json


def test_seed_until_sets_sim(db):
    p, rows, prefs = seeded_rows(db, "overspend-one-time", until=date(2026, 9, 30))
    assert rows and max(r.date for r in rows) <= date(2026, 9, 30)
    assert len(rows) == sum(1 for d, *_ in transactions(p) if d <= date(2026, 9, 30))
    assert prefs["sim"] == {"profile": "overspend-one-time", "date": "2026-09-30"}


def test_seed_without_until_unchanged(db):
    p, rows, prefs = seeded_rows(db, "overspend-one-time")
    assert "sim" not in (prefs or {}) and len(rows) == len(transactions(p))


def test_seed_ids_stable(db):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    _, first, _ = seeded_rows(db, "overspend-one-time")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    other = sessionmaker(bind=engine, expire_on_commit=False)
    _, second, _ = seeded_rows(other, "overspend-one-time")
    assert [r.plaid_txn_id for r in first][:2] == ["sim-overspend-one-time-0", "sim-overspend-one-time-1"]
    assert [r.plaid_txn_id for r in first] == [r.plaid_txn_id for r in second]


def test_until_before_profile_start(db):
    _, rows, prefs = seeded_rows(db, "overspend-one-time", until=date(2026, 6, 1))
    assert rows == [] and prefs["sim"]["date"] == "2026-06-01"


def test_default_start():
    starts = {p.id: default_start(p) for p in PLANTED + PROFILES}
    assert (starts["overspend-one-time"], starts["overspend-seasonal"], starts["steady"]) == (
        date(2026, 9, 30), date(2026, 11, 30), date(2026, 7, 31))


# the saving line (tpc-22) tracks plaid's TRANSFER_OUT_SAVINGS; other transfers stay plain
def test_planted_savings_detailed(db):
    _, rows, _ = seeded_rows(db, "overspend-one-time")
    transfers = {r.pf_category_detailed for r in rows if r.pf_category_primary == "TRANSFER_OUT"}
    assert transfers == {"TRANSFER_OUT_SAVINGS"}


# --- the goal graph's synthetic user (tpc-23) ---

def goal_user():
    return next(p for p in GOAL_USERS if p.id == "goals-card-24")


def test_goal_user_seeds_debts_and_savings(db):
    with db() as s:
        uid = seed_profile(s, goal_user())
        s.commit()
        debts = load_debts(s, uid)
        savings_account = s.query(Account).filter_by(user_id=uid, subtype="savings").one()
    assert [(d.name, d.apr, d.balance_cents, d.min_payment_cents) for d in debts] == [
        ("Platinum Card", 24.99, 320000, 9500), ("Student Loan", 5.5, 1840000, 21000)]
    assert savings_account.current_balance_cents == 100000


def test_goal_user_has_a_surplus(db):
    p = goal_user()
    with db() as s:
        uid = seed_profile(s, p)
        s.commit()
        surplus = monthly_surplus(load_txns(s, uid, p.start, p.today), p.today)
    assert 30000 < surplus < 150000, surplus


def test_goal_user_not_in_the_accuracy_eval():
    assert "goals-card-24" not in {p.id for p in PROFILES + PLANTED}


# the debts' payments are in its history, or the goal graph would measure money that's already spoken for
def test_goal_user_pays_its_debts(db):
    with db() as s:
        uid = seed_profile(s, goal_user())
        payments = {(r.merchant_name, r.amount_cents, r.pf_category_detailed) for r in
                    s.query(Transaction).filter_by(user_id=uid, pf_category_primary="LOAN_PAYMENTS")}
    assert payments == {("Student Loan", 21000, None), ("Platinum Card", 9500, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")}


# --- biweekly pay, price steps, noisy purchases and a moving balance (TPC-33) ---

HASHES = {"steady": "c46034002c6f", "vacation": "1f32f65b528d", "early-rent": "f54a9cbf30f1", "rising": "dfb7a42a181d",
          "occasional": "5cffd523cfcd", "overspend-one-time": "50d7cc005186", "overspend-recurring": "7ebed8d37d7c",
          "overspend-seasonal": "a9e3ec12934c", "overspend-known-merchant": "056ac49b1a45", "goals-card-24": "2bcfb8ca8436"}


def test_new_fields_leave_existing_profiles_identical():
    for p in (*PROFILES, *PLANTED, *GOAL_USERS):
        assert sha256(repr(transactions(p)).encode()).hexdigest()[:12] == HASHES[p.id]


def test_every_generates_biweekly_dates():
    p = replace(PROFILES[0], patterns=(Every("INCOME", "Employer", -185000, 14, date(2026, 5, 1)),),
                start=date(2026, 5, 1), end=date(2026, 6, 12))
    assert [(t[0], t[1]) for t in transactions(p)] == [(date(2026, 5, 1), -185000), (date(2026, 5, 15), -185000),
                                                       (date(2026, 5, 29), -185000), (date(2026, 6, 12), -185000)]


def test_every_honours_last_and_steps():
    p = replace(PROFILES[0], start=date(2026, 5, 1), end=date(2026, 8, 1), patterns=(
        Every("GENERAL_SERVICES", "Cleaner", 6000, 7, date(2026, 5, 1), last=date(2026, 5, 22),
              steps=((date(2026, 5, 15), 6500),)),))
    assert [(t[0], t[1]) for t in transactions(p)] == [(date(2026, 5, 1), 6000), (date(2026, 5, 8), 6000),
                                                       (date(2026, 5, 15), 6500), (date(2026, 5, 22), 6500)]


def test_monthly_steps_change_the_amount_from_a_date():
    p = replace(PROFILES[0], start=date(2026, 8, 1), end=date(2026, 10, 31), patterns=(
        Monthly("PERSONAL_CARE", "FitClub Gym", 4000, day=3, steps=((date(2026, 10, 3), 4300),)),))
    assert [(t[0], t[1]) for t in transactions(p)] == [(date(2026, 8, 3), 4000), (date(2026, 9, 3), 4000),
                                                       (date(2026, 10, 3), 4300)]


def test_monthly_last_stops_the_pattern():
    p = replace(PROFILES[0], start=date(2026, 8, 1), end=date(2026, 12, 31), patterns=(
        Monthly("ENTERTAINMENT", "Streamly", 1549, day=5, last=date(2026, 10, 5)),))
    assert [t[0] for t in transactions(p)] == [date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5)]


def test_unknown_pattern_class_raises_type_error():
    p = replace(PROFILES[0], patterns=(object(),))
    with pytest.raises(TypeError):
        transactions(p)


def test_scatter_is_repeatable_and_stays_inside_its_ranges():
    merchants = ("Cafe", "Bakery")
    p = replace(PROFILES[0], start=date(2026, 1, 1), end=date(2026, 6, 30), patterns=(
        Scatter("FOOD_AND_DRINK", merchants, 0.2, (1, 3), (300, 4500)),))
    rows = transactions(p)
    assert rows == transactions(p)
    assert rows and all(300 <= t[1] <= 4500 and t[3] in merchants for t in rows)
    per_day = {}
    for t in rows:
        per_day[t[0]] = per_day.get(t[0], 0) + 1
    assert max(per_day.values()) <= 3
    assert 0.1 < len(per_day) / 181 < 0.3


def test_noisy_user_buys_on_about_half_of_its_days():
    noisy = next(p for p in RECURRING_USERS if p.id == "recurring-noisy")
    days = {t[0] for t in transactions(noisy) if t[2] in ("FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "TRANSPORTATION")}
    assert 0.4 < len(days) / ((noisy.end - noisy.start).days + 1) < 0.6


def test_opening_balance_is_applied_up_to_until(db):
    p = replace(PROFILES[0], opening_cents=190000)
    until = date(2026, 6, 15)
    with db() as s:
        uid = seed_profile(s, p, until=until)
    with db() as s:
        acct = s.query(Account).filter_by(user_id=uid, name="Checking").one()
        assert acct.subtype == "checking"
        assert acct.current_balance_cents == 190000 - sum(t[1] for t in transactions(p) if t[0] <= until)
        assert acct.available_balance_cents == acct.current_balance_cents


def test_profiles_without_opening_keep_the_old_account(db):
    with db() as s:
        uid = seed_profile(s, PROFILES[0], until=date(2026, 6, 15))
    with db() as s:
        acct = s.query(Account).filter_by(user_id=uid, name="Checking").one()
        assert (acct.subtype, acct.current_balance_cents, acct.available_balance_cents) == (None, 10000, None)


def test_recurring_users_are_not_in_the_accuracy_tuples():
    ids = {p.id for p in RECURRING_USERS}
    assert ids == {"recurring-planted", "recurring-noisy"}
    assert not ids & {p.id for p in PROFILES + PLANTED}


# --- the benchmark's own users (TPC-26) ---

def scenario_user(pid):
    return next(p for p in SCENARIO_USERS if p.id == pid)


def spent_in(p, category, year, month):
    return sum(c for d, c, cat, _ in transactions(p) if cat == category and (d.year, d.month) == (year, month))


def test_profile_by_id_finds_every_tuple():
    for group in (PROFILES, PLANTED, GOAL_USERS, RECURRING_USERS, FORECAST_USERS, SCENARIO_USERS):
        assert profile_by_id(group[0].id) is group[0]
    assert profile_by_id("nobody") is None


def test_profile_ids_are_unique():
    every = [p.id for p in (*PROFILES, *PLANTED, *GOAL_USERS, *RECURRING_USERS, *FORECAST_USERS, *SCENARIO_USERS)]
    assert len(every) == len(set(every))


def test_scenario_users_are_not_in_the_accuracy_eval():
    assert not {p.id for p in SCENARIO_USERS} & {p.id for p in PROFILES + PLANTED}


def test_new_club_starts_in_september():
    club = [d for d, _, _, m in transactions(scenario_user("overspend-new-club")) if m == "Vine Club"]
    assert club == [date(2026, 9, 4), date(2026, 10, 4)]


def test_back_to_school_is_spread_across_seven_shops():
    p = scenario_user("overspend-back-to-school")
    shops = {m: c for d, c, _, m in transactions(p) if d.month == 9 and m != "Corner Shop"
             and c > 0 and m not in ("Landlord", "Savings")}
    assert len(shops) == 7 and max(shops.values()) < sum(shops.values()) / 2


# the control users really are on budget, so a flag on one is a false alarm and not a planted overspend
def test_flat_control_spends_the_same_every_month():
    p = scenario_user("control-minimal")
    assert {spent_in(p, "FOOD_AND_DRINK", 2026, m) for m in (7, 8, 10)} == {24800}


def test_early_order_control_ends_october_at_its_usual_total():
    p = scenario_user("control-under-limit")
    assert spent_in(p, "FOOD_AND_DRINK", 2026, 10) == spent_in(p, "FOOD_AND_DRINK", 2026, 8) == 26800
    assert (date(2026, 10, 20), 2000, "FOOD_AND_DRINK", "Bakery") in transactions(p)


def test_early_rent_control_pays_twice_in_june_and_never_in_july():
    p = scenario_user("control-early-rent")
    assert (spent_in(p, "RENT_AND_UTILITIES", 2026, 6), spent_in(p, "RENT_AND_UTILITIES", 2026, 7)) == (300000, 0)


@pytest.mark.parametrize("pid, low, high", [("goals-no-surplus", -30000, -1), ("goals-thin-surplus", 1000, 1000),
                                            ("goals-no-debts", 100000, 150000)])
def test_goal_scenario_users_have_their_planted_surplus(db, pid, low, high):
    p = scenario_user(pid)
    with db() as s:
        uid = seed_profile(s, p)
        s.commit()
        assert low <= monthly_surplus(load_txns(s, uid, p.start, p.today), p.today) <= high


def test_no_debts_user_has_almost_no_savings_and_no_debts(db):
    with db() as s:
        uid = seed_profile(s, scenario_user("goals-no-debts"))
        s.commit()
        assert load_debts(s, uid) == []
        assert s.query(Account).filter_by(user_id=uid, subtype="savings").one().current_balance_cents == 20000
