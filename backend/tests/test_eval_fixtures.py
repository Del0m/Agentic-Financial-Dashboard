import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Account, Liability, Transaction, User
from eval.fixtures import ScenarioError, load_scenario, load_scenarios, seed, temp_db

REPO = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 27)


def scenario_dict(id="debt-highest-apr"):
    return {
        "id": id,
        "question": "Which of my debts should I pay off first?",
        "user": {
            "accounts": [
                {"name": "Everyday Checking", "type": "depository", "subtype": "checking", "balance": 2150.00},
                {"name": "High Yield Savings", "type": "depository", "subtype": "savings", "balance": 500.00},
                {"name": "Platinum Card", "type": "credit", "balance": 3200.00,
                 "liability": {"kind": "credit", "apr": 24.99, "min_payment": 95.00, "due_in_days": 12}},
            ],
            "transactions": [
                {"account": "Everyday Checking", "days_ago": 4, "amount": 42.10,
                 "category": "FOOD_AND_DRINK", "merchant": "Chipotle"},
                {"account": "Everyday Checking", "days_ago": 6, "amount": -12.50,
                 "category": "FOOD_AND_DRINK", "merchant": "Chipotle"},
            ],
        },
        "golden_answer": "Pay the Platinum Card first.",
        "expect": {"facts": [{"text": "Platinum Card"}, {"number": 24.99, "tol": 0.01}],
                   "forbid": ["http"], "tools": ["get_debts"]},
    }


# writes <id>.json (or `name`) under tmp_path; `edit` mutates the dict first
def write(tmp_path, edit=None, name=None, raw=None):
    d = scenario_dict()
    if edit:
        edit(d)
    path = tmp_path / (name or f"{d['id']}.json")
    path.write_text(raw if raw is not None else json.dumps(d))
    return path


def rejected(path, *fragments):
    with pytest.raises(ScenarioError) as err:
        load_scenario(path)
    for f in (path.name, *fragments):
        assert f in str(err.value)


def test_seed_creates_rows(tmp_path):
    scenario = load_scenario(write(tmp_path))
    with temp_db() as Session, Session() as s:
        seed(s, scenario, TODAY)
        accts = {a.name: a for a in s.scalars(select(Account))}
        liab = s.scalar(select(Liability))
        txns = sorted(s.scalars(select(Transaction)), key=lambda t: t.amount_cents)

    assert accts["Platinum Card"].current_balance_cents == 320000
    assert accts["High Yield Savings"].subtype == "savings"
    assert (liab.apr, liab.min_payment_cents, liab.next_due_date) == (24.99, 9500, TODAY + timedelta(days=12))
    assert liab.account_id == accts["Platinum Card"].id
    assert [(t.amount_cents, t.date) for t in txns] == [(-1250, TODAY - timedelta(days=6)),
                                                       (4210, TODAY - timedelta(days=4))]
    assert txns[1].pf_category_primary == "FOOD_AND_DRINK" and txns[1].merchant_name == "Chipotle"


def test_seed_user_is_scenario_scoped(tmp_path):
    scenario = load_scenario(write(tmp_path))
    with temp_db() as Session, Session() as s:
        uid = seed(s, scenario, TODAY)
        owners = {a.user_id for a in s.scalars(select(Account))}
        users = [u.id for u in s.scalars(select(User))]

    assert uid == "eval-debt-highest-apr"
    assert owners == {uid} and users == [uid]


def test_unknown_category_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["user"]["transactions"][0].update(category="GROCERIES")), "category")


def test_unknown_account_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["user"]["transactions"][0].update(account="Nope")), "account")


def test_negative_days_ago_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["user"]["transactions"][0].update(days_ago=-1)), "days_ago")


def test_empty_facts_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["expect"].update(facts=[])), "facts")


def test_missing_question_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d.pop("question")), "question")


def test_bad_json_rejected(tmp_path):
    rejected(write(tmp_path, raw="{not json"), "json")


@pytest.mark.parametrize("fact", [{"text": "a", "number": 1}, {}])
def test_fact_needs_exactly_one_of_text_or_number(tmp_path, fact):
    rejected(write(tmp_path, lambda d: d["expect"].update(facts=[fact])), "facts")


def test_id_must_match_filename(tmp_path):
    rejected(write(tmp_path, name="other.json"), "id")


def test_only_unknown_id_rejected(tmp_path):
    write(tmp_path)
    with pytest.raises(ScenarioError, match="no scenario"):
        load_scenarios(tmp_path, only="nope")


def test_committed_scenarios_load():
    assert len(load_scenarios(REPO / "eval" / "scenarios")) == 40


# the payoff scenario's facts are the planner's numbers (tpc-32); months and interest don't depend on the start date
def test_debt_plan_scenario_matches_planner():
    from app.budget_engine.payoff import compare
    from app.budget_service.debts import load_debts

    scenario = load_scenario(REPO / "eval" / "scenarios" / "debt-plan-extra-200.json")
    with temp_db() as factory, factory() as s:
        plan = compare(load_debts(s, seed(s, scenario, TODAY)), 20000, start=date(2026, 11, 1))["avalanche"]
    assert [(f.number, f.tol) for f in scenario.expect.facts] == [(plan.months, 0), (plan.total_interest_cents / 100, 1)]


# --- final-review fixes ---

def test_empty_text_fact_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["expect"].update(facts=[{"text": ""}])), "facts")


@pytest.mark.parametrize("forbid", ["http", [""], [3]])
def test_forbid_must_be_list_of_strings(tmp_path, forbid):
    rejected(write(tmp_path, lambda d: d["expect"].update(forbid=forbid)), "forbid")


def test_non_numeric_number_fact_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["expect"].update(facts=[{"number": "abc"}])), "facts")


def test_liability_without_kind_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["user"]["accounts"][2]["liability"].pop("kind")), "kind")


# a text fact the question already contains passes on any answer that echoes the question
def test_text_facts_not_in_question():
    for s in load_scenarios(REPO / "eval" / "scenarios"):
        for f in s.expect.facts if s.kind == "chat" else ():
            assert f.text is None or f.text.lower() not in s.question.lower(), (s.id, f.text)


# review on #10: liability fields seed() reads must be checked at load time, not fail inside a run as a silent 0
@pytest.mark.parametrize("field, value", [
    ("apr", "24.99"), ("min_payment", "95"), ("due_in_days", 12.5), ("due_in_days", -1), ("kind", "credit card"),
])
def test_bad_liability_field_rejected(tmp_path, field, value):
    rejected(write(tmp_path, lambda d: d["user"]["accounts"][2]["liability"].update({field: value})), field)


# two accounts with one name would share a plaid_account_id and fail at seed time
def test_duplicate_account_name_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["user"]["accounts"][1].update(name="Everyday Checking")), "name")


# --- budget and goal scenarios, abstention and tags (tpc-26) ---

def budget_dict(**kw):
    return {"id": "b1", "kind": "budget", "profile": "overspend-one-time", "start": "2026-10-11", "end": "2026-10-13",
            "golden_answer": "A one-off at ElectroMart.", "tags": ["one_time"],
            "expect": {"flags": ["GENERAL_MERCHANDISE"], "causes": {"GENERAL_MERCHANDISE": "one_time"},
                       "actions": {"GENERAL_MERCHANDISE": {"accept": ["alert"], "ban": ["adjust_next_budget"]}},
                       "flag_by": {"GENERAL_MERCHANDISE": "2026-10-12"}, "alert_kinds": ["low_balance"],
                       "min_days_ahead": 3, "message_facts": [{"text": "ElectroMart"}]}, **kw}


def goal_dict(**kw):
    return {"id": "g1", "kind": "goal", "profile": "goals-card-24", "today": "2026-11-02", "golden_answer": "Debt first.",
            "expect": {"status": "ok", "required_kinds": ["debt_payoff"], "banned_kinds": ["savings"],
                       "first_kind": "debt_payoff"}, **kw}


def write_dict(tmp_path, d, edit=None):
    if edit:
        edit(d)
    path = tmp_path / f"{d['id']}.json"
    path.write_text(json.dumps(d))
    return path


def test_chat_is_the_default_kind(tmp_path):
    scenario = load_scenario(write(tmp_path))
    assert (scenario.kind, scenario.tags, scenario.expect.abstain) == ("chat", (), False)


def test_budget_scenario_loads(tmp_path):
    s = load_scenario(write_dict(tmp_path, budget_dict()))
    assert (s.kind, s.profile, s.start, s.end, s.tags) == ("budget", "overspend-one-time", date(2026, 10, 11),
                                                           date(2026, 10, 13), ("one_time",))
    e = s.expect
    assert e.flags == ("GENERAL_MERCHANDISE",) and e.causes == {"GENERAL_MERCHANDISE": "one_time"}
    assert e.actions["GENERAL_MERCHANDISE"].accept == ("alert",) and e.actions["GENERAL_MERCHANDISE"].ban == ("adjust_next_budget",)
    assert e.flag_by == {"GENERAL_MERCHANDISE": date(2026, 10, 12)} and e.alert_kinds == ("low_balance",)
    assert e.min_days_ahead == 3 and e.message_facts[0].text == "ElectroMart" and not s.is_control


# an empty list is a control; leaving flags out is a scenario about something else, whose flags aren't checked
def test_empty_flags_is_a_control_and_absent_flags_is_not(tmp_path):
    control = load_scenario(write_dict(tmp_path, budget_dict(id="c1", expect={"flags": []})))
    signal = load_scenario(write_dict(tmp_path, budget_dict(id="s1", expect={"alert_kinds": ["price_increase"]})))
    assert control.is_control and control.expect.flags == ()
    assert not signal.is_control and signal.expect.flags is None


def test_goal_scenario_loads(tmp_path):
    s = load_scenario(write_dict(tmp_path, goal_dict(prefs={"max_share": 0.5})))
    assert (s.kind, s.profile, s.today, s.prefs) == ("goal", "goals-card-24", date(2026, 11, 2), {"max_share": 0.5})
    assert (s.expect.status, s.expect.required_kinds, s.expect.banned_kinds, s.expect.first_kind) == (
        "ok", ("debt_payoff",), ("savings",), "debt_payoff")


def test_abstain_scenario_needs_no_facts(tmp_path):
    def edit(d):
        d["expect"] = {"abstain": True}
    scenario = load_scenario(write(tmp_path, edit))
    assert scenario.expect.abstain and scenario.expect.facts == []


def test_abstain_with_facts_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d["expect"].update(abstain=True)), "expect.facts")


def test_abstain_must_be_a_bool(tmp_path):
    rejected(write(tmp_path, lambda d: d["expect"].update(abstain="yes")), "expect.abstain")


def test_unknown_kind_rejected(tmp_path):
    rejected(write(tmp_path, lambda d: d.update(kind="close")), "kind")


@pytest.mark.parametrize("tags", ["one_time", [""], [3]])
def test_tags_must_be_a_list_of_strings(tmp_path, tags):
    rejected(write(tmp_path, lambda d: d.update(tags=tags)), "tags")


@pytest.mark.parametrize("edit, field", [
    (lambda d: d.update(profile="nobody"), "profile"),
    (lambda d: d.pop("profile"), "profile"),
    (lambda d: d.update(start="soon"), "start"),
    (lambda d: d.update(end="2026-10-11"), "end"),
    (lambda d: d["expect"].update(flags="GENERAL_MERCHANDISE"), "expect.flags"),
    (lambda d: d["expect"].update(flags=["GROCERIES"]), "expect.flags"),
    (lambda d: d["expect"].update(causes={"GROCERIES": "one_time"}), "expect.causes"),
    (lambda d: d["expect"].update(causes={"GENERAL_MERCHANDISE": "bad_luck"}), "expect.causes"),
    (lambda d: d["expect"].update(actions={"GENERAL_MERCHANDISE": "alert"}), "expect.actions"),
    (lambda d: d["expect"].update(actions={"GENERAL_MERCHANDISE": {"accept": []}}), "accept"),
    (lambda d: d["expect"].update(actions={"GENERAL_MERCHANDISE": {"accept": ["panic"]}}), "accept"),
    (lambda d: d["expect"].update(actions={"GENERAL_MERCHANDISE": {"accept": ["alert"], "ban": ["panic"]}}), "ban"),
    (lambda d: d["expect"].update(flag_by={"GENERAL_MERCHANDISE": "soon"}), "flag_by"),
    (lambda d: d["expect"].update(alert_kinds=["overspend"]), "alert_kinds"),
    (lambda d: d["expect"].update(min_days_ahead=-1), "min_days_ahead"),
    (lambda d: d["expect"].update(min_days_ahead=True), "min_days_ahead"),
    (lambda d: d["expect"].update(message_facts=[{"text": ""}]), "facts"),
    (lambda d: d.pop("golden_answer"), "golden_answer"),
])
def test_bad_budget_scenario_rejected(tmp_path, edit, field):
    rejected(write_dict(tmp_path, budget_dict(), edit), field)


@pytest.mark.parametrize("edit, field", [
    (lambda d: d.update(profile="nobody"), "profile"),
    (lambda d: d.update(today="soon"), "today"),
    (lambda d: d.update(prefs="low"), "prefs"),
    (lambda d: d["expect"].update(status="error"), "expect.status"),
    (lambda d: d["expect"].pop("status"), "expect.status"),
    (lambda d: d["expect"].update(required_kinds=["vacation"]), "required_kinds"),
    (lambda d: d["expect"].update(banned_kinds=["vacation"]), "banned_kinds"),
    (lambda d: d["expect"].update(first_kind="vacation"), "first_kind"),
    (lambda d: d.pop("golden_answer"), "golden_answer"),
])
def test_bad_goal_scenario_rejected(tmp_path, edit, field):
    rejected(write_dict(tmp_path, goal_dict(), edit), field)


# the ticket's done-when and its six categories
def test_committed_scenarios_cover_the_ticket():
    scenarios = load_scenarios(REPO / "eval" / "scenarios")
    assert 30 <= len(scenarios) <= 50
    assert all(s.golden_answer.strip() for s in scenarios)
    tags = {tag for s in scenarios for tag in s.tags}
    assert {"high_interest_debt", "unused_subscription", "one_time", "recurring", "seasonal", "no_overspend_control",
            "goal_affordability"} <= tags
    kinds = [s.kind for s in scenarios]
    assert (kinds.count("chat"), kinds.count("budget"), kinds.count("goal")) == (22, 12, 6)
    assert len([s for s in scenarios if "abstain" in s.tags and s.expect.abstain]) == 4
    assert all(s.tags for s in scenarios)
