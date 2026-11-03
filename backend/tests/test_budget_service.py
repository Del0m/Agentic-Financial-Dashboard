from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.budget_service import initialize
from app.budget_service.initialize import initialize_budget, load_txns, resolve_category
from app.budget_service.settings import set_user_style, user_style
from app.db.models import Account, AgentRun, Alert, BudgetLine, BudgetPeriod, CategoryRule, Goal
from tests.factories import add_account, add_txn, add_user


def period(user_id="u1", start=date(2026, 10, 1)):
    return BudgetPeriod(user_id=user_id, start_date=start, end_date=date(2026, 10, 31))


def test_one_period_per_user_month(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        s.add_all([period(), period()])
        with pytest.raises(IntegrityError):
            s.commit()


def test_one_line_per_category(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        p = period()
        s.add(p)
        s.flush()
        s.add_all([BudgetLine(period_id=p.id, category="FOOD_AND_DRINK", name="Food and drink", group="needs",
                              kind="flexible", limit_cents=1, source="init") for _ in range(2)])
        with pytest.raises(IntegrityError):
            s.commit()


def test_period_defaults(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        p = period()
        s.add(p)
        s.flush()
        s.add(BudgetLine(period_id=p.id, category="TRAVEL", name="Travel", group="wants", kind="flexible",
                         limit_cents=100, source="init"))
        s.commit()
        assert p.status == "open" and p.fit_json is None
        assert p.lines[0].spent_cents == 0 and p.lines[0].last_pace is None



def test_alert_proposed_limit(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        s.add_all([Alert(user_id="u1", kind="overspend", severity="high", message="m"),
                   Alert(user_id="u1", kind="overspend", severity="medium", message="m", proposed_limit_cents=16000)])
        s.commit()
        assert sorted(a.proposed_limit_cents or 0 for a in s.query(Alert)) == [0, 16000]

# --- initialize_budget ---

TODAY = date(2026, 10, 15)    # window: Jul, Aug, Sep 2026


def user(s, user_id="u1"):
    item = add_user(s, user_id, f"{user_id}@x")
    return add_account(s, item, "Checking")


def history(s, acct, travel_merchant="Delta"):
    for m, food in ((7, 40000), (8, 45000), (9, 50000)):
        add_txn(s, acct, food, "FOOD_AND_DRINK", day=date(2026, m, 10))
        add_txn(s, acct, 150000, "RENT_AND_UTILITIES", merchant="Landlord", day=date(2026, m, 1))
    add_txn(s, acct, 20000, "TRAVEL", day=date(2026, 7, 12))
    add_txn(s, acct, 120000, "TRAVEL", merchant=travel_merchant, day=date(2026, 8, 10))
    add_txn(s, acct, 70000, "TRAVEL", merchant=travel_merchant, day=date(2026, 8, 11))
    add_txn(s, acct, 25000, "TRAVEL", day=date(2026, 9, 12))


def counts(db):
    with db() as s:
        return s.query(BudgetPeriod).count(), s.query(BudgetLine).count()


def test_initialize_creates_period_and_lines(db):
    with db() as s:
        history(s, user(s))
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        assert (p.start_date, p.end_date, p.status) == (date(2026, 10, 1), date(2026, 10, 31), "open")
        pid = p.id
    # read back in a fresh session, so fit_json has really been through json
    with db() as s:
        p = s.get(BudgetPeriod, pid)
        assert p.fit_json == {"window": ["2026-07-01", "2026-08-01", "2026-09-01"], "skipped": []}
        lines = {ln.category: ln for ln in p.lines}
        assert {c: (ln.kind, ln.limit_cents) for c, ln in lines.items()} == {
            "FOOD_AND_DRINK": ("flexible", 47250), "RENT_AND_UTILITIES": ("fixed", 150000),
            "TRAVEL": ("flexible", 26250), "FREE": ("flexible", 12750)}    # no income: free is the fallback's room
        assert all(ln.source == "init" and ln.spent_cents == 0 and ln.last_pace is None for ln in p.lines)
        assert lines["TRAVEL"].fit_json == {
            "monthly_totals": [20000, 190000, 25000],
            "outliers": [{"month": "2026-08-01", "total_cents": 190000, "median_cents": 25000,
                          "top_txns": [{"date": "2026-08-10", "amount_cents": 120000, "merchant": "Delta"},
                                       {"date": "2026-08-11", "amount_cents": 70000, "merchant": "Delta"}]}]}
        assert lines["RENT_AND_UTILITIES"].fit_json == {"monthly_totals": [150000, 150000, 150000], "outliers": []}


def test_skipped_recorded(db):
    with db() as s:
        acct = user(s)
        history(s, acct)
        add_txn(s, acct, 30000, "GENERAL_MERCHANDISE", day=date(2026, 8, 20))
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        assert p.fit_json["skipped"] == ["GENERAL_MERCHANDISE"]
        assert "GENERAL_MERCHANDISE" not in {ln.category for ln in p.lines}


def test_load_txns_mapping(db):
    start, end = date(2026, 7, 1), date(2026, 9, 30)
    with db() as s:
        acct = user(s)
        other = user(s, "u2")
        add_txn(s, acct, 100, "FOOD_AND_DRINK", day=date(2026, 8, 1), removed=True)
        add_txn(s, acct, 200, "FOOD_AND_DRINK", merchant="Air", day=date(2026, 8, 2), our_category="TRAVEL",
                detailed="FOOD_AND_DRINK_COFFEE")
        add_txn(s, acct, 300, None, day=date(2026, 8, 3))
        add_txn(s, other, 400, "FOOD_AND_DRINK", day=date(2026, 8, 4))
        add_txn(s, acct, 500, "FOOD_AND_DRINK", day=start)
        add_txn(s, acct, 600, "FOOD_AND_DRINK", day=end)
        add_txn(s, acct, 700, "FOOD_AND_DRINK", day=date(2026, 10, 1))
        s.commit()
        got = sorted(load_txns(s, "u1", start, end), key=lambda t: t.amount_cents)
    assert [t.amount_cents for t in got] == [200, 300, 500, 600]
    assert (got[0].category, got[0].detailed, got[0].merchant) == ("TRAVEL", "FOOD_AND_DRINK_COFFEE", "Air")
    assert got[1].category == "UNCATEGORIZED"
    assert (got[2].date, got[3].date) == (start, end)


def test_current_month_ignored(db):
    with db() as s:
        acct = user(s)
        for m in (5, 6, 7):
            add_txn(s, acct, 10000, "FOOD_AND_DRINK", day=date(2026, m, 10))
        add_txn(s, acct, 500000, "FOOD_AND_DRINK", day=date(2026, 8, 1))
        s.commit()
        p = initialize_budget(s, "u1", date(2026, 8, 1))
        assert [(ln.category, ln.limit_cents) for ln in p.lines] == [("FOOD_AND_DRINK", 10500), ("FREE", 0)]


def test_second_call_returns_same_period(db):
    with db() as s:
        history(s, user(s))
        s.commit()
        first = initialize_budget(s, "u1", TODAY).id
        assert initialize_budget(s, "u1", TODAY).id == first
    assert counts(db) == (1, 4)       # three category lines and the free line


def test_existing_older_period_returned(db):
    with db() as s:
        history(s, user(s))
        s.add(BudgetPeriod(user_id="u1", start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), status="closed"))
        s.commit()
        assert initialize_budget(s, "u1", TODAY).start_date == date(2026, 9, 1)
    assert counts(db) == (1, 0)


def test_no_history_returns_none(db):
    with db() as s:
        add_txn(s, user(s), 5000, "FOOD_AND_DRINK", day=date(2026, 10, 3))
        s.commit()
        assert initialize_budget(s, "u1", TODAY) is None
    assert counts(db) == (0, 0)


def test_only_excluded_history_returns_none(db):
    with db() as s:
        acct = user(s)
        for m in (7, 8, 9):
            add_txn(s, acct, -400000, "INCOME", day=date(2026, m, 15))
            add_txn(s, acct, 50000, "TRANSFER_OUT", day=date(2026, m, 16))
        s.commit()
        assert initialize_budget(s, "u1", TODAY) is None
    assert counts(db) == (0, 0)


def test_all_skipped_returns_none(db):
    with db() as s:
        add_txn(s, user(s), 30000, "GENERAL_MERCHANDISE", day=date(2026, 8, 20))
        add_txn(s, s.query(Account).one(), 1000, "INCOME", day=date(2026, 7, 2))
        s.commit()
        assert initialize_budget(s, "u1", TODAY) is None
    assert counts(db) == (0, 0)


def test_outlier_without_merchant(db):
    with db() as s:
        history(s, user(s), travel_merchant=None)
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        travel = next(ln for ln in p.lines if ln.category == "TRAVEL")
        assert travel.fit_json["outliers"][0]["top_txns"][0]["merchant"] is None


def test_race_returns_existing_period(db, monkeypatch):
    with db() as s:
        history(s, user(s))
        s.add(period())
        s.commit()
        existing = s.query(BudgetPeriod).one().id
        real, calls = initialize._latest_period, []

        # the first look misses the period, as if another run committed it just after we checked
        def racy(session, user_id):
            calls.append(user_id)
            return None if len(calls) == 1 else real(session, user_id)

        monkeypatch.setattr(initialize, "_latest_period", racy)
        assert initialize_budget(s, "u1", TODAY).id == existing
    assert counts(db) == (1, 0)


def test_other_integrity_error_raises(db, monkeypatch):
    with db() as s:
        history(s, user(s))
        s.add(period())
        s.commit()
        # the commit fails but no period can be found afterwards, so it wasn't the race: the error must
        # surface instead of reading as "not enough history yet"
        monkeypatch.setattr(initialize, "_latest_period", lambda session, user_id: None)
        with pytest.raises(IntegrityError):
            initialize_budget(s, "u1", TODAY)


# user override > merchant rule > llm category > a transfer to savings > plaid's primary
def test_load_txns_precedence(db):
    day = date(2026, 8, 5)
    with db() as s:
        acct = user(s)
        s.add(CategoryRule(user_id="u1", merchant_name="costco", category="FOOD_AND_DRINK"))
        add_txn(s, acct, 100, "GENERAL_MERCHANDISE", merchant="Costco", day=day, user_category="TRAVEL")
        add_txn(s, acct, 200, "GENERAL_MERCHANDISE", merchant="Costco", day=day, our_category="ENTERTAINMENT")
        add_txn(s, acct, 300, "GENERAL_MERCHANDISE", merchant="Shop", day=day, our_category="ENTERTAINMENT")
        add_txn(s, acct, 400, "GENERAL_MERCHANDISE", merchant="Shop", day=day)
        add_txn(s, acct, 500, "TRANSFER_OUT", merchant="Savings", day=day, detailed="TRANSFER_OUT_SAVINGS")
        add_txn(s, acct, 600, "TRANSFER_OUT", merchant="Rent pal", day=day)
        s.commit()
        got = sorted(load_txns(s, "u1", day, day), key=lambda t: t.amount_cents)
        rows = {r.amount_cents: r for r in s.query(initialize.Transaction).filter_by(user_id="u1")}
        rules = {"costco": "FOOD_AND_DRINK"}
        sources = [resolve_category(rows[c], rules)[1] for c in (100, 200, 300, 500)]
    assert [(t.category, t.overridden) for t in got] == [
        ("TRAVEL", True), ("FOOD_AND_DRINK", True), ("ENTERTAINMENT", False), ("GENERAL_MERCHANDISE", False),
        ("SAVINGS_TRANSFER", False), ("TRANSFER_OUT", False)]
    assert sources == ["user", "rule", "plaid", "plaid"]


def test_rule_matches_case_insensitively(db):
    day = date(2026, 8, 5)
    with db() as s:
        acct = user(s)
        s.add(CategoryRule(user_id="u1", merchant_name="costco", category="FOOD_AND_DRINK"))
        add_txn(s, acct, 100, "GENERAL_MERCHANDISE", merchant="COSTCO ", day=day)
        s.commit()
        [got] = load_txns(s, "u1", day, day)
    assert (got.category, got.overridden) == ("FOOD_AND_DRINK", True)


# --- the full budget model at init (TPC-22) ---

def planted(s, pid="overspend-one-time"):
    from eval.budget_users import PLANTED, seed_profile
    return seed_profile(s, next(p for p in PLANTED if p.id == pid))


def test_init_writes_groups_names_income_total_free(db):
    with db() as s:
        uid = planted(s)
        period = initialize_budget(s, uid, date(2026, 10, 1))
        lines = {ln.category: ln for ln in period.lines}
        others = sum(ln.limit_cents for c, ln in lines.items() if c != "FREE")
        assert (period.income_cents, period.total_cents) == (420000, 420000)
        saving = lines["SAVINGS_TRANSFER"]
        assert (saving.name, saving.group, saving.kind, saving.limit_cents) == ("Savings", "saving", "fixed", 50000)
        assert (lines["RENT_AND_UTILITIES"].name, lines["RENT_AND_UTILITIES"].group) == ("Rent and utilities", "needs")
        free = lines["FREE"]
        assert (free.name, free.group, free.kind, free.source) == ("Free", "free", "flexible", "init")
        assert free.limit_cents == 420000 - others and free.fit_json == {"rule": "remainder"}


def test_init_503020(db):
    with db() as s:
        uid = planted(s)
        set_user_style(s, uid, "50_30_20")
        s.commit()
        period = initialize_budget(s, uid, date(2026, 10, 1))
        lines = {ln.category: ln for ln in period.lines}
    assert lines["SAVINGS_TRANSFER"].limit_cents == 84000        # 20% of $4,200, not the fitted $500
    assert user_style_of(db, uid) == ("50_30_20", {"needs": 50, "wants": 30, "saving": 20})


def user_style_of(db, uid):
    with db() as s:
        return user_style(s, uid)


def test_set_user_style_validates(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        assert user_style(s, "u1") == ("caps", {"needs": 50, "wants": 30, "saving": 20})
        with pytest.raises(ValueError, match="Targets must add up to 100."):
            set_user_style(s, "u1", "50_30_20", {"needs": 50, "wants": 30, "saving": 30})
        with pytest.raises(ValueError):
            set_user_style(s, "u1", "envelopes")
        set_user_style(s, "u1", "50_30_20", {"needs": 60, "wants": 25, "saving": 15})
        s.commit()
        assert user_style(s, "u1") == ("50_30_20", {"needs": 60, "wants": 25, "saving": 15})


# "always for target" means target purchases: a paycheck, a transfer or a card payment from the same merchant
# keeps its own category, or income would vanish and spending go negative (tpc-22 review)
def test_rule_moves_only_spending(db):
    day = date(2026, 8, 5)
    with db() as s:
        acct = user(s)
        s.add(CategoryRule(user_id="u1", merchant_name="target", category="GENERAL_MERCHANDISE"))
        add_txn(s, acct, -420000, "INCOME", merchant="Target", day=day)
        add_txn(s, acct, -5000, "TRANSFER_IN", merchant="Target", day=day)
        add_txn(s, acct, 8000, "LOAN_PAYMENTS", merchant="Target", day=day, detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
        add_txn(s, acct, 3000, "ENTERTAINMENT", merchant="Target", day=day)
        s.commit()
        got = {t.amount_cents: (t.category, t.overridden) for t in load_txns(s, "u1", day, day)}
    assert got == {-420000: ("INCOME", False), -5000: ("TRANSFER_IN", False),
                   8000: ("LOAN_PAYMENTS", False), 3000: ("GENERAL_MERCHANDISE", True)}


# --- the history window's edge isn't the start of the history ---

def fitted_food(db, *months):
    with db() as s:
        acct = user(s)
        for day, cents in months:
            add_txn(s, acct, cents, "FOOD_AND_DRINK", day=day)
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        food = next(ln for ln in p.lines if ln.category == "FOOD_AND_DRINK")
        return p.fit_json["window"], food.fit_json["monthly_totals"], food.limit_cents


def test_longer_history_fits_all_three_months(db):
    window, totals, limit = fitted_food(db, *((date(2026, m, 5), cents) for m, cents in (
        (4, 40000), (5, 40000), (6, 40000), (7, 30000), (8, 45000), (9, 50000))))
    assert window == ["2026-07-01", "2026-08-01", "2026-09-01"]
    assert totals == [30000, 45000, 50000]
    assert limit == 47250     # 450 x 1.05; without july it would be 475 x 1.05


def test_history_starting_mid_window_month_skips_it(db):
    window, totals, _ = fitted_food(db, (date(2026, 7, 5), 30000), (date(2026, 8, 5), 45000), (date(2026, 9, 5), 50000))
    assert window == ["2026-08-01", "2026-09-01"]
    assert totals == [45000, 50000]


# june is partial, but it's outside the window; only the months inside it matter
def test_history_starting_mid_month_before_window_keeps_all_three(db):
    window, totals, _ = fitted_food(db, (date(2026, 6, 20), 10000), (date(2026, 7, 5), 30000),
                                    (date(2026, 8, 5), 45000), (date(2026, 9, 5), 50000))
    assert window == ["2026-07-01", "2026-08-01", "2026-09-01"]
    assert totals == [30000, 45000, 50000]


# july's rent paid on jun 30 is july's, so loading june puts it back in the window
def test_rent_paid_just_before_window_counts_for_its_month(db):
    with db() as s:
        acct = user(s)
        add_txn(s, acct, 150000, "RENT_AND_UTILITIES", merchant="Landlord", day=date(2026, 6, 30))
        for m in (8, 9):
            add_txn(s, acct, 150000, "RENT_AND_UTILITIES", merchant="Landlord", day=date(2026, m, 1))
        add_txn(s, acct, 40000, "FOOD_AND_DRINK", day=date(2026, 7, 2))
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        rent = next(ln for ln in p.lines if ln.category == "RENT_AND_UTILITIES")
        assert rent.fit_json["monthly_totals"] == [150000, 150000, 150000]


# review on #11: the column is Double in the migration; a bare float maps to Float on sqlalchemy 2.0
def test_last_pace_is_double():
    from sqlalchemy import Double

    from app.db.models import BudgetLine
    assert type(BudgetLine.__table__.c.last_pace.type) is Double



def test_quiet_month_before_window_does_not_erase_older_history(db):
    with db() as s:
        acct = user(s)
        for m, cents in ((5, 40000), (7, 30000), (8, 45000), (9, 50000)):
            add_txn(s, acct, cents, "FOOD_AND_DRINK", day=date(2026, m, 5))
        s.commit()
        period = initialize_budget(s, "u1", date(2026, 10, 15))
        food = next(ln for ln in period.lines if ln.category == "FOOD_AND_DRINK")
        assert period.fit_json["window"] == ["2026-07-01", "2026-08-01", "2026-09-01"]
        assert food.fit_json["monthly_totals"] == [30000, 45000, 50000]
        assert food.limit_cents == 47250


# --- goals in the first budget (tpc-25) ---

NOV1 = date(2026, 11, 1)
OCT1 = date(2026, 10, 1)
LINES_CENTS = 150000 + 47250 + 26250        # history()'s rent, food and travel limits
INCOME = 420000


def add_goal(s, status="accepted", start=OCT1, run_status=None, cents=58300):
    run_id = None
    if run_status:
        run = AgentRun(user_id="u1", graph="goals", status=run_status)
        s.add(run)
        s.flush()
        run_id = run.id
    goal = Goal(user_id="u1", run_id=run_id, kind="debt_payoff", priority=1, monthly_cents=cents,
                target_cents=cents * 6, target_date=date(2027, 6, 1), start_date=start, rationale="Because.",
                status=status)
    s.add(goal)
    s.flush()
    return goal


def paid_user(s, income=INCOME):
    acct = user(s)
    history(s, acct)
    for m in (7, 8, 9):
        add_txn(s, acct, -income, "INCOME", merchant="Employer", day=date(2026, m, 15))
    return acct


def test_first_budget_includes_goal_already_started(db):
    with db() as s:
        paid_user(s)
        add_goal(s)
        s.commit()
        pid = initialize_budget(s, "u1", TODAY).id
    with db() as s:
        p = s.get(BudgetPeriod, pid)
        lines = {ln.category: ln for ln in p.lines}
        line = lines["GOAL_DEBT_PAYOFF"]
        assert (line.name, line.group, line.kind, line.limit_cents, line.source, line.fit_json) == (
            "Debt payoff", "goal", "fixed", 58300, "user", {"rule": "goal"})
        assert lines["FREE"].limit_cents == INCOME - LINES_CENTS - 58300
        assert lines["FREE"].limit_cents == 138200
        assert lines["RENT_AND_UTILITIES"].limit_cents == 150000
        assert p.total_cents == INCOME
        assert s.query(Alert).count() == 0       # it fits, so there's nothing to tell the user


# the total rises rather than cutting a line, and the user is told, as at close
def test_first_budget_goal_over_income_raises_alert(db):
    with db() as s:
        paid_user(s)
        add_goal(s, cents=250000)
        s.commit()
        pid = initialize_budget(s, "u1", TODAY).id
    with db() as s:
        p = s.get(BudgetPeriod, pid)
        lines = {ln.category: ln for ln in p.lines}
        assert (lines["GOAL_DEBT_PAYOFF"].limit_cents, lines["FREE"].limit_cents) == (250000, 0)
        assert lines["RENT_AND_UTILITIES"].limit_cents == 150000
        assert p.total_cents == LINES_CENTS + 250000 == 473500
        [alert] = s.query(Alert).all()
        assert (alert.user_id, alert.period_id, alert.kind, alert.category, alert.severity, alert.cause, alert.action,
                alert.status) == ("u1", pid, "over_income", None, "medium", None, "alert", "pending_user")
        assert alert.message == ("Your budget lines add up to $4,735.00, $535.00 more than your typical income of "
                                 "$4,200.00.")


# the style reports lines over income here too, but a first budget with no goals has never written that as an alert
def test_first_budget_without_goals_writes_no_alert(db):
    with db() as s:
        paid_user(s, income=100000)
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        assert p.total_cents == LINES_CENTS and p.income_cents == 100000
    with db() as s:
        assert s.query(Alert).count() == 0


# another run opened the month first: the alert goes with the budget that lost
def test_first_budget_lost_race_writes_no_alert(db, monkeypatch):
    with db() as s:
        paid_user(s)
        add_goal(s, cents=250000)
        s.add(period())
        s.commit()
        real, calls = initialize._latest_period, []

        def racy(session, user_id):
            calls.append(user_id)
            return None if len(calls) == 1 else real(session, user_id)

        monkeypatch.setattr(initialize, "_latest_period", racy)
        initialize_budget(s, "u1", TODAY)
    with db() as s:
        assert s.query(Alert).count() == 0
    assert counts(db) == (1, 0)


def test_first_budget_leaves_out_goal_starting_later(db):
    with db() as s:
        paid_user(s)
        goal = add_goal(s, start=NOV1)
        s.commit()
        p = initialize_budget(s, "u1", TODAY)
        lines = {ln.category: ln for ln in p.lines}
        assert "GOAL_DEBT_PAYOFF" not in lines
        assert lines["FREE"].limit_cents == INCOME - LINES_CENTS
        assert goal.status == "accepted"


def test_first_budget_expires_a_stale_proposal(db):
    with db() as s:
        paid_user(s)
        stale, later = add_goal(s, "proposed", run_status="awaiting_user"), add_goal(s, "proposed", start=NOV1)
        s.commit()
        stale_id, later_id, run_id = stale.id, later.id, stale.run_id
        p = initialize_budget(s, "u1", TODAY)
        assert "GOAL_DEBT_PAYOFF" not in {ln.category for ln in p.lines}
    # read back in a fresh session: the expiry went in the same commit as the budget
    with db() as s:
        assert (s.get(Goal, stale_id).status, s.get(Goal, later_id).status) == ("superseded", "proposed")
        run = s.get(AgentRun, run_id)
        assert (run.status, run.summary_json["expired_goal_ids"]) == ("expired", [stale_id])
        assert run.finished_at is not None


# nothing to fit: no budget opened, so nothing retires the proposal yet
def test_no_first_budget_leaves_the_proposal(db):
    with db() as s:
        user(s)
        goal_id = add_goal(s, "proposed").id
        s.commit()
        assert initialize_budget(s, "u1", TODAY) is None
    with db() as s:
        assert s.get(Goal, goal_id).status == "proposed"


# another run opened the month first: its commit stands and ours, expiry included, rolls back
def test_first_budget_lost_race_keeps_the_proposal(db, monkeypatch):
    with db() as s:
        paid_user(s)
        goal_id = add_goal(s, "proposed").id
        s.add(period())
        s.commit()
        real, calls = initialize._latest_period, []

        def racy(session, user_id):
            calls.append(user_id)
            return None if len(calls) == 1 else real(session, user_id)

        monkeypatch.setattr(initialize, "_latest_period", racy)
        initialize_budget(s, "u1", TODAY)
    with db() as s:
        assert s.get(Goal, goal_id).status == "proposed"
    assert counts(db) == (1, 0)
