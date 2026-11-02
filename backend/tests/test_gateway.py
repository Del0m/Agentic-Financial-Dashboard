import json
from datetime import date, timedelta

import pytest

from tests.factories import add_account, add_txn, add_user
from app.privacy import gateway as gw
from app.budget_engine.diagnose import overspend_facts
from app.budget_engine.pace import PaceResult
from app.budget_engine.txns import Txn
from app.privacy.gateway import ACCOUNT_FIELDS, DEBT_FIELDS, DIAGNOSE_FIELDS, TXN_FIELDS, PrivacyGateway

TODAY = date(2026, 10, 21)
PII = ["Jane", "Doe", "jane.doe@example.com", "4321", "acc-secret-1", "item-secret-1", "u-jane"]


# every test runs on a fixed "today" so the default 30-day spending window is predictable
@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(gw, "_today", lambda: TODAY)


# the spec's Jane Doe: PII in the email, the account name, the mask, plaid ids and a Zelle merchant
def seed_pii_user(db) -> str:
    with db() as s:
        item = add_user(s, "u-jane", "jane.doe@example.com")
        item.plaid_item_id = "item-secret-1"
        checking = add_account(s, item, "Jane Doe's Checking ••4321", balance=12345, mask="4321",
                               plaid_id="acc-secret-1")
        add_account(s, item, "Plaid Credit Card", type_="credit", balance=41000,
                    liability={"kind": "credit", "apr": 22.0, "min_payment_cents": 2000})
        add_txn(s, checking, 5000, "TRANSFER_OUT", merchant="Zelle to Jane Doe")
        add_txn(s, checking, 650, "FOOD_AND_DRINK", merchant="Starbucks")
        s.commit()
    return "u-jane"


def data(prompt):
    return json.loads(prompt)["data"]


def audit_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# the ticket's done-when: no name, email or account number in any gateway-built prompt
def test_no_pii_in_any_prompt(db, tmp_path):
    uid = seed_pii_user(db)
    with db() as s:
        g = PrivacyGateway(s, uid, audit_path=tmp_path / "audit.jsonl")
        prompts = [g.build_context("accounts"), g.build_context("debts"), g.build_context("spending"),
                   g.build_context("spending", category="TRANSFER_OUT")]

    for prompt in prompts:
        for secret in PII:
            assert secret not in prompt, f"{secret!r} leaked: {prompt}"


# rows are built field by field, so a new column can't leak by accident
def test_rows_have_only_allowlisted_keys(db, tmp_path):
    uid = seed_pii_user(db)
    with db() as s:
        g = PrivacyGateway(s, uid, audit_path=tmp_path / "audit.jsonl")
        accounts = data(g.build_context("accounts"))
        debts = data(g.build_context("debts"))
        txns = data(g.build_context("spending", category="FOOD_AND_DRINK"))["transactions"]

    assert all(set(r) == set(ACCOUNT_FIELDS) for r in accounts)
    assert all(set(r) == set(DEBT_FIELDS) for r in debts)
    assert all(set(r) == set(TXN_FIELDS) for r in txns)


def test_money_in_dollars(db, tmp_path):
    uid = seed_pii_user(db)
    with db() as s:
        accounts = data(PrivacyGateway(s, uid, audit_path=tmp_path / "a.jsonl").build_context("accounts"))

    assert 123.45 in [a["balance"] for a in accounts]


# one pseudonym per gateway (= per chat session), stable across its prompts
def test_pseudonym_format_and_lifetime(db, tmp_path, monkeypatch):
    uid = seed_pii_user(db)
    hexes = iter(["7f3a", "b2c1"])
    monkeypatch.setattr(gw.secrets, "token_hex", lambda n: next(hexes))
    with db() as s:
        first = PrivacyGateway(s, uid, audit_path=tmp_path / "a.jsonl")
        second = PrivacyGateway(s, uid, audit_path=tmp_path / "a.jsonl")
        users = {json.loads(first.build_context(p))["user"] for p in ("accounts", "debts")}

    assert first.pseudonym == "user_7f3a"
    assert users == {"user_7f3a"}
    assert second.pseudonym == "user_b2c1"


# a chat session spans many requests (each with its own db session); passing the pseudonym
# back in keeps the model seeing one consistent user
def test_pseudonym_can_be_reused(db, tmp_path):
    uid = seed_pii_user(db)
    with db() as s:
        g = PrivacyGateway(s, uid, audit_path=tmp_path / "a.jsonl", pseudonym="user_abcd")
        prompt = g.build_context("accounts")

    assert g.pseudonym == "user_abcd"
    assert json.loads(prompt)["user"] == "user_abcd"


def test_other_users_data_hidden(db, tmp_path):
    uid = seed_pii_user(db)
    with db() as s:
        other = add_user(s, "u-other", "other@example.com")
        add_account(s, other, "Theirs", type_="credit", liability={"kind": "credit", "apr": 30.0})
        s.commit()
        g = PrivacyGateway(s, uid, audit_path=tmp_path / "a.jsonl")
        prompts = g.build_context("accounts") + g.build_context("debts")

    assert "Theirs" not in prompts


# same avalanche order as GET /dashboard: highest APR first, unknown APR last
def test_debts_sorted_like_dashboard(db, tmp_path):
    with db() as s:
        item = add_user(s, "u-1", "a@example.com")
        for name, apr in [("A", 15.0), ("B", 24.99), ("C", None)]:
            add_account(s, item, name, type_="credit", liability={"kind": "credit", "apr": apr})
        s.commit()
        debts = data(PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("debts"))

    assert [d["apr"] for d in debts] == [24.99, 15.0, None]


# spending = positive amounts (money out) in range, not refunds, not removed rows
def test_spending_totals_positive_only_in_range(db, tmp_path):
    with db() as s:
        acct = add_account(s, add_user(s, "u-1", "a@example.com"), "Checking")
        add_txn(s, acct, 650, "FOOD_AND_DRINK")
        add_txn(s, acct, -200, "FOOD_AND_DRINK")                       # refund
        add_txn(s, acct, 100, "FOOD_AND_DRINK", day=date(2026, 9, 1))  # before start
        add_txn(s, acct, 999, "FOOD_AND_DRINK", removed=True)
        s.commit()
        g = PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl")
        totals = data(g.build_context("spending", start=date(2026, 10, 1), end=TODAY))

    assert totals == {"FOOD_AND_DRINK": 6.5}


# minimization: a category question gets that category's rows only, newest first, capped
def test_spending_category_lists_capped_newest_first(db, tmp_path):
    with db() as s:
        acct = add_account(s, add_user(s, "u-1", "a@example.com"), "Checking")
        for i in range(60):
            add_txn(s, acct, 100, "FOOD_AND_DRINK", day=TODAY - timedelta(days=i % 25))
        add_txn(s, acct, 5000, "TRANSFER_OUT")
        s.commit()
        out = data(PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl")
                   .build_context("spending", category="FOOD_AND_DRINK"))

    assert out["category"] == "FOOD_AND_DRINK"
    assert len(out["transactions"]) == 50
    assert (out["count"], out["truncated"]) == (60, True)
    assert out["transactions"][0]["date"] == TODAY.isoformat()
    assert "TRANSFER_OUT" not in json.dumps(out)


# models lowercase enum values; and "no rows in that category" must read as $0, not as nothing
def test_spending_category_case_insensitive_and_zero(db, tmp_path):
    with db() as s:
        acct = add_account(s, add_user(s, "u-1", "a@example.com"), "Checking")
        add_txn(s, acct, 650, "FOOD_AND_DRINK")
        s.commit()
        g = PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl")
        dining = data(g.build_context("spending", category="food_and_drink"))
        travel = data(g.build_context("spending", category="TRAVEL"))

    assert (dining["category"], dining["total"]) == ("FOOD_AND_DRINK", 6.5)
    assert travel == {"category": "TRAVEL", "total": 0.0, "direction": "outflow", "count": 0, "truncated": False,
                      "transactions": []}


# 30 calendar days with today included: Sep 22 - Oct 21
def test_spending_defaults_to_last_30_days(db, tmp_path):
    with db() as s:
        acct = add_account(s, add_user(s, "u-1", "a@example.com"), "Checking")
        add_txn(s, acct, 100, "IN_WINDOW", day=date(2026, 9, 22))
        add_txn(s, acct, 100, "TOO_OLD", day=date(2026, 9, 21))
        s.commit()
        totals = data(PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("spending"))

    assert totals == {"IN_WINDOW": 1.0}


def test_empty_user_builds_empty_context(db, tmp_path):
    with db() as s:
        add_user(s, "u-1", "a@example.com")
        s.commit()
        g = PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl")
        results = [data(g.build_context(p)) for p in ("accounts", "debts", "spending")]

    assert results == [[], [], {}]
    assert len(audit_lines(tmp_path / "a.jsonl")) == 3


# the audit log is the evidence of what left the system: exact prompt, pseudonym, never the user id
def test_audit_line_per_call(db, tmp_path):
    uid = seed_pii_user(db)
    path = tmp_path / "a.jsonl"
    with db() as s:
        g = PrivacyGateway(s, uid, audit_path=path)
        p1 = g.build_context("accounts")
        p2 = g.build_context("spending", category="FOOD_AND_DRINK")

    lines = audit_lines(path)
    assert [l["prompt"] for l in lines] == [p1, p2]
    assert {l["pseudonym"] for l in lines} == {g.pseudonym}
    assert [l["purpose"] for l in lines] == ["accounts", "spending"]
    assert lines[1]["params"] == {"category": "FOOD_AND_DRINK"}
    assert uid not in path.read_text()


def test_audit_creates_missing_directory(db, tmp_path):
    path = tmp_path / "new" / "dir" / "a.jsonl"
    with db() as s:
        add_user(s, "u-1", "a@example.com")
        s.commit()
        PrivacyGateway(s, "u-1", audit_path=path).build_context("accounts")

    assert path.exists()


def test_unknown_purpose_raises(db, tmp_path):
    with db() as s:
        with pytest.raises(ValueError):
            PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("transactions")



# --- purpose "diagnose" (TPC-19) ---

GM = "GENERAL_MERCHANDISE"
OCT = date(2026, 10, 1)


def gm(day, cents, merchant="Shop", cat=GM):
    return Txn(date=date(2026, 10, day), amount_cents=cents, category=cat, merchant=merchant)


def diagnose_prompt(db, tmp_path, txns, audit="a.jsonl"):
    spent = sum(t.amount_cents for t in txns if t.category == GM)
    flag = PaceResult(category=GM, kind="flexible", limit_cents=15750, spent_cents=spent, days_elapsed=20,
                      days_in_period=31, pace=None, flagged=True, reason="over_limit")
    facts = overspend_facts(flag, {"monthly_totals": [15000, 14800, 15200], "outliers": []}, txns, [], OCT)
    uid = seed_pii_user(db)
    with db() as s:
        g = PrivacyGateway(s, uid, audit_path=tmp_path / audit)
        return g, g.build_context("diagnose", facts=facts, transactions=txns)


def test_diagnose_allowlist(db, tmp_path):
    _, prompt = diagnose_prompt(db, tmp_path, [gm(12, 60000, "ElectroMart"), gm(5, 3500)])
    d = data(prompt)
    assert set(d) == set(DIAGNOSE_FIELDS)
    assert (d["limit"], d["spent"], d["month"], d["day"], d["days_in_month"]) == (157.5, 635.0, "October 2026", 20, 31)
    assert d["largest"] == {"date": "2026-10-12", "amount": 600.0, "merchant": "ElectroMart", "share_of_spent": 0.94,
                            "times_typical": None}
    assert d["history"][0] == {"month": "July 2026", "total": 150.0}
    assert d["merchants"][0]["pattern"] == "new"


def test_diagnose_scrubs_merchants(db, tmp_path):
    _, prompt = diagnose_prompt(db, tmp_path, [gm(12, 60000, "Paid jane.doe@example.com")])
    assert "jane.doe@example.com" not in prompt


def test_diagnose_category_only_capped_newest_first(db, tmp_path):
    txns = [gm(d, 1000 + d) for d in range(1, 26)] + [gm(3, 5000, "Diner", "FOOD_AND_DRINK") for _ in range(3)]
    rows = data(diagnose_prompt(db, tmp_path, txns)[1])["transactions"]
    assert len(rows) == 20
    assert "Diner" not in {r["merchant"] for r in rows}
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)


def test_diagnose_audited(db, tmp_path):
    g, _ = diagnose_prompt(db, tmp_path, [gm(12, 60000, "ElectroMart")])
    line = audit_lines(tmp_path / "a.jsonl")[-1]
    assert (line["purpose"], line["params"], line["pseudonym"]) == ("diagnose", {"category": GM}, g.pseudonym)
    assert "u-jane" not in json.dumps(line)


def test_diagnose_unnamed_merchant(db, tmp_path):
    d = data(diagnose_prompt(db, tmp_path, [gm(12, 60000, None)])[1])
    assert d["merchants"][0]["merchant"] is None
    assert d["transactions"][0]["merchant"] is None



def test_diagnose_memory_keys(db, tmp_path):
    txns = [gm(12, 60000, "ElectroMart")]
    g, prompt = diagnose_prompt(db, tmp_path, txns)
    d = data(prompt)
    assert (d["this_month_alerts"], d["past_advice"]) == ([], [])
    from app.budget_engine.diagnose import overspend_facts as of
    flag = PaceResult(category=GM, kind="flexible", limit_cents=15750, spent_cents=60000, days_elapsed=20,
                      days_in_period=31, pace=None, flagged=True, reason="over_limit")
    d = data(g.build_context("diagnose", facts=of(flag, None, txns, [], OCT), transactions=txns,
                             this_month_alerts=[{"severity": "high", "action": "alert", "status": "pending_user",
                                                 "message": "secret text"}],
                             past_advice=[{"month": "September 2026", "action": "adjust_next_budget",
                                           "outcome": "dismissed", "message": "secret text"}]))
    assert d["this_month_alerts"] == [{"severity": "high", "action": "alert", "status": "pending_user"}]
    assert d["past_advice"] == [{"month": "September 2026", "action": "adjust_next_budget", "outcome": "dismissed"}]
    assert "secret text" not in json.dumps(d)


# review on #6: transfers and card payments move money that's already counted, so they aren't spending
def test_spending_leaves_out_transfers_and_card_payments(db, tmp_path):
    with db() as s:
        acct = add_account(s, add_user(s, "u-1", "a@example.com"), "Checking")
        add_txn(s, acct, 500, "FOOD_AND_DRINK")
        add_txn(s, acct, 500, "LOAN_PAYMENTS", detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
        add_txn(s, acct, 900, "LOAN_PAYMENTS", detailed="LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT")
        add_txn(s, acct, 10000, "TRANSFER_OUT")
        add_txn(s, acct, -250000, "INCOME")
        s.commit()
        totals = data(PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("spending"))

    assert totals == {"LOAN_PAYMENTS": 9.0, "FOOD_AND_DRINK": 5.0}


# income is money in (negative in plaid's convention); asking for it gives a positive total, not $0
def test_spending_income_question_reports_inflow(db, tmp_path):
    with db() as s:
        acct = add_account(s, add_user(s, "u-1", "a@example.com"), "Checking")
        add_txn(s, acct, -250000, "INCOME", merchant="ACME PAYROLL")
        add_txn(s, acct, 500, "FOOD_AND_DRINK")
        s.commit()
        out = data(PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl")
                   .build_context("spending", category="INCOME"))

    assert (out["total"], out["direction"], out["count"]) == (2500.0, "inflow", 1)
    assert out["transactions"] == [{"date": "2026-10-10", "amount": 2500.0, "merchant": None}]


def test_spending_reversed_range_raises(db, tmp_path):
    with db() as s:
        with pytest.raises(ValueError):
            PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context(
                "spending", start=date(2026, 11, 1), end=TODAY)


# a pseudonym passed back in must look like one, or prompt text could ride in as the "user"
def test_bad_pseudonym_rejected(db, tmp_path):
    with db() as s:
        with pytest.raises(ValueError):
            PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl", pseudonym="u-jane")


# the debt balance is the account's live balance, the same number the dashboard totals use
def test_debt_balance_from_account(db, tmp_path):
    with db() as s:
        item = add_user(s, "u-1", "a@example.com")
        card = add_account(s, item, "Card", type_="credit", balance=41000,
                           liability={"kind": "credit", "apr": 22.0})
        card.current_balance_cents = 45000          # a later balance refresh; the liability copy is older
        s.commit()
        [debt] = data(PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("debts"))

    assert debt["balance"] == 450.0


# the audit file holds every prompt: owner-only, and rotated to one .1 backup instead of growing forever
def test_audit_file_private_and_rotated(db, tmp_path, monkeypatch):
    path = tmp_path / "a.jsonl"
    with db() as s:
        add_user(s, "u-1", "a@example.com")
        s.commit()
        g = PrivacyGateway(s, "u-1", audit_path=path)
        g.build_context("accounts")
        monkeypatch.setattr(gw.config, "PROMPT_AUDIT_MAX_BYTES", path.stat().st_size + 10)
        g.build_context("debts")

    assert path.stat().st_mode & 0o777 == 0o600
    assert [l["purpose"] for l in audit_lines(path)] == ["debts"]
    assert [l["purpose"] for l in audit_lines(path.with_name("a.jsonl.1"))] == ["accounts"]


# review on #12: the diagnose rows go through the same spending filter as the facts, so a card payment that
# isn't in "spent" isn't quoted as evidence either
def test_diagnose_rows_leave_out_card_payments(db, tmp_path):
    loans = "LOAN_PAYMENTS"
    txns = [Txn(date=date(2026, 10, 5), amount_cents=200000, category=loans, merchant="Card Payment",
                detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            Txn(date=date(2026, 10, 9), amount_cents=40000, category=loans, merchant="Student Loan",
                detailed="LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT")]
    flag = PaceResult(category=loans, kind="fixed", limit_cents=30000, spent_cents=40000, days_elapsed=20,
                      days_in_period=31, pace=None, flagged=True, reason="over_limit")
    facts = overspend_facts(flag, None, txns, [], date(2026, 10, 1))
    uid = seed_pii_user(db)
    with db() as s:
        rows = data(PrivacyGateway(s, uid, audit_path=tmp_path / "a.jsonl")
                    .build_context("diagnose", facts=facts, transactions=txns))["transactions"]

    assert [r["merchant"] for r in rows] == ["Student Loan"]


# --- the payoff planner (tpc-32): computed results only, names scrubbed ---

from app.budget_engine.payoff import compare
from app.budget_service.debts import load_debts
from app.privacy.scrub import scrub_account_name

PLAN_START = date(2026, 11, 1)     # the month after TODAY


def seed_debts(db) -> str:
    with db() as s:
        item = add_user(s, "u-1", "a@example.com")
        add_account(s, item, "Jane Doe's Visa ••4421", type_="credit", balance=320000,
                    liability={"kind": "credit", "apr": 24.99, "min_payment_cents": 9500})
        add_account(s, item, "Student Loan", type_="loan", balance=1840000,
                    liability={"kind": "student", "apr": 5.5, "min_payment_cents": 21000})
        add_account(s, item, "Store Card", type_="credit", balance=50000,
                    liability={"kind": "credit", "apr": None, "min_payment_cents": 2500})
        s.commit()
    return "u-1"


def plan_context(db, tmp_path, **kw):
    with db() as s:
        prompt = PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("debt_plan", **kw)
        expected = compare(load_debts(s, "u-1"), kw.get("extra_monthly_cents", 0), start=PLAN_START)
    return prompt, data(prompt), expected


def test_debt_plan_matches_compare_and_scrubs(db, tmp_path):
    seed_debts(db)
    prompt, d, expected = plan_context(db, tmp_path, extra_monthly_cents=20000)
    assert "Jane" not in prompt and "4421" not in prompt
    assert (d["extra_monthly"], d["start"]) == (200.0, "2026-11")
    by = {p["strategy"]: p for p in d["plans"]}
    for strategy in ("avalanche", "snowball"):
        want = expected[strategy]
        assert (by[strategy]["months"], by[strategy]["total_interest"], by[strategy]["debt_free"]) == \
            (want.months, want.total_interest_cents / 100, want.debt_free.strftime("%Y-%m"))
        assert [(o["name"], o["paid_off"], o["interest"]) for o in by[strategy]["order"]] == \
            [(scrub_account_name(p.name), p.date.strftime("%Y-%m"), p.interest_cents / 100) for p in want.order]
    assert d["excluded"] == [{"name": "Store Card", "reason": "no APR on file"}]
    assert all(p["never"] == [] for p in d["plans"])


def test_debt_plan_one_strategy(db, tmp_path):
    seed_debts(db)
    _, d, _ = plan_context(db, tmp_path, extra_monthly_cents=20000, strategy="snowball")
    assert [p["strategy"] for p in d["plans"]] == ["snowball"]


def test_debt_plan_difference_and_assumptions(db, tmp_path):
    seed_debts(db)
    _, d, expected = plan_context(db, tmp_path, extra_monthly_cents=20000, strategy="avalanche")
    [plan] = d["plans"]
    base = expected["baseline_avalanche"]
    assert plan["without_extra"] == {"months": base.months, "total_interest": base.total_interest_cents / 100}
    assert plan["difference"] == {
        "months_sooner": base.months - expected["avalanche"].months,
        "interest_saved": (base.total_interest_cents - expected["avalanche"].total_interest_cents) / 100}
    assert d["assumptions"] == ("Interest is charged monthly at APR/12; minimum payments are held at today's "
                                "amounts; not financial advice.")


# a plan that never ends has no "sooner" to report, and names the debt (scrubbed)
def test_debt_plan_never(db, tmp_path):
    with db() as s:
        add_account(s, add_user(s, "u-1", "a@example.com"), "Jane Doe's Card", type_="credit", balance=1000000,
                    liability={"kind": "credit", "apr": 30.0, "min_payment_cents": 10000})
        s.commit()
    _, d, _ = plan_context(db, tmp_path, extra_monthly_cents=0, strategy="avalanche")
    [plan] = d["plans"]
    assert (plan["months"], plan["debt_free"], plan["difference"]) == (None, None, None)
    # review: the interest summed over 600 months of an unpaid debt is billions, not an answer to quote
    assert plan["total_interest"] is None and plan["without_extra"] == {"months": None, "total_interest": None}
    assert plan["never"] == [scrub_account_name("Jane Doe's Card")] and "Jane" not in json.dumps(d)


def test_debt_plan_audited_with_params(db, tmp_path):
    seed_debts(db)
    plan_context(db, tmp_path, extra_monthly_cents=20000)
    [line] = audit_lines(tmp_path / "a.jsonl")
    assert (line["purpose"], line["params"]) == ("debt_plan", {"extra_monthly_cents": 20000})


def test_debt_plan_unknown_strategy_raises(db, tmp_path):
    seed_debts(db)
    with pytest.raises(ValueError, match="strategy"):
        plan_context(db, tmp_path, extra_monthly_cents=0, strategy="cheapest")


# review: when every debt is left out, the model gets no plan and a note, never "debt-free in 0 months"
def test_debt_plan_nothing_planned(db, tmp_path):
    with db() as s:
        add_account(s, add_user(s, "u-1", "a@example.com"), "Store Card", type_="credit", balance=50000,
                    liability={"kind": "credit", "apr": None, "min_payment_cents": 2500})
        s.commit()
    _, d, _ = plan_context(db, tmp_path, extra_monthly_cents=20000)
    assert d["plans"] == [] and d["excluded"] == [{"name": "Store Card", "reason": "no APR on file"}]
    assert d["note"] == "None of the debts could be planned; see excluded."


def test_debt_plan_no_debts(db, tmp_path):
    with db() as s:
        add_user(s, "u-1", "a@example.com")
        s.commit()
    _, d, _ = plan_context(db, tmp_path, extra_monthly_cents=0)
    assert (d["plans"], d["excluded"], d["note"]) == ([], [], "No debts with a balance on file.")



# --- the goal graph's facts (tpc-23) ---

from app.budget_engine.goals import GoalFacts


def goal_facts(offered=("debt_payoff", "emergency_fund", "savings"), **over):
    fields = dict(surplus_cents=90000, cap_cents=72000, offered=tuple(offered), gap_cents=820000, start=PLAN_START,
                  protected_categories=("PERSONAL_CARE",), note="Jane Doe's card first")
    return GoalFacts(**{**fields, **over})


def goals_context(db, tmp_path, facts):
    with db() as s:
        prompt = PrivacyGateway(s, "u-1", audit_path=tmp_path / "a.jsonl").build_context("goals", goal_facts=facts)
        debts = load_debts(s, "u-1")
    return prompt, data(prompt), debts


def test_goals_scrubs_debts_and_note(db, tmp_path):
    seed_debts(db)
    prompt, d, _ = goals_context(db, tmp_path, goal_facts())
    assert "Jane" not in prompt and "4421" not in prompt
    assert d["note"] == "[person]'s card first"
    assert d["protected_categories"] == ["PERSONAL_CARE"]


def test_goals_cap_and_options_match_compare(db, tmp_path):
    seed_debts(db)
    _, d, debts = goals_context(db, tmp_path, goal_facts())
    assert (d["monthly_surplus"], d["cap"], d["emergency_fund_gap"]) == (900.0, 720.0, 8200.0)
    assert d["offered_kinds"] == ["debt_payoff", "emergency_fund", "savings"]
    assert [o["extra_monthly"] for o in d["debt_plan_options"]] == [180.0, 360.0, 540.0]
    for option in d["debt_plan_options"]:
        want = compare(debts, round(option["extra_monthly"] * 100), start=PLAN_START)
        for s in ("avalanche", "snowball"):
            assert option[s] == {"months": want[s].months, "total_interest": want[s].total_interest_cents / 100}
    assert d["assumptions"] == ("monthly_surplus is what's left after minimum payments. "
                                "Debt plans charge interest monthly at APR/12 and hold minimum payments at "
                                "today's amounts.")


def test_goals_no_options_without_debt_offered(db, tmp_path):
    seed_debts(db)
    _, d, _ = goals_context(db, tmp_path, goal_facts(offered=("savings",)))
    assert "debt_plan_options" not in d and d["debts"] == []


# the store card has no APR, so it isn't something a goal can plan; the rest are highest APR first
def test_goals_includable_debts_only(db, tmp_path):
    seed_debts(db)
    _, d, _ = goals_context(db, tmp_path, goal_facts())
    assert [(x["kind"], x["apr"], x["balance"], x["min_payment"]) for x in d["debts"]] == \
        [("credit", 24.99, 3200.0, 95.0), ("student", 5.5, 18400.0, 210.0)]


def test_goals_audited_once(db, tmp_path):
    seed_debts(db)
    goals_context(db, tmp_path, goal_facts())
    [line] = audit_lines(tmp_path / "a.jsonl")
    assert (line["purpose"], line["params"]) == ("goals", {"cap_cents": 72000})


# final review: the account-name scrub turned "it's my priority" into "[person]'s my priority", losing the preference
def test_goals_note_keeps_contractions(db, tmp_path):
    seed_debts(db)
    _, d, _ = goals_context(db, tmp_path, goal_facts(note="emergency fund first, it's my priority"))
    assert d["note"] == "emergency fund first, it's my priority"
