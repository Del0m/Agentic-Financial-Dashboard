import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app import config
from app.agents.chat_graph import build_chat_graph
from app.agents.tools import make_tools
from app.budget_engine.txns import add_months
from app.kg.retrieve import gather, read_time
from app.privacy.gateway import PrivacyGateway
from eval.fixtures import load_scenarios, prepare_graph, seed, temp_db
from pathlib import Path
from tests.fakes import FakeKB, ScriptedChatModel

REPO = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 6)
SCENARIOS = {s.id: s for s in load_scenarios(REPO / "eval" / "scenarios")}


@pytest.mark.parametrize("question, last, first", [
    ("What was the limit in 2024?", date(2024, 12, 31), date(2024, 1, 1)),
    ("What's the limit in 2030?", date(2030, 12, 31), date(2030, 1, 1)),
    ("What did I spend in 2026?", TODAY, date(2026, 1, 1)),
    ("What was it last year?", date(2025, 12, 31), date(2025, 1, 1)),
    ("What did I spend last month?", date(2026, 10, 31), date(2026, 10, 1)),
    ("What was it in September?", date(2026, 9, 30), date(2026, 9, 1)),
    ("What was it in June 2024?", date(2024, 6, 30), date(2024, 6, 1)),
    ("What was it in December?", date(2025, 12, 31), date(2025, 12, 1)),
    ("What have I spent in November?", TODAY, date(2026, 11, 1)),
    ("How has it changed since August?", TODAY, date(2026, 8, 1)),
    ("How has it changed since 2025?", TODAY, date(2025, 1, 1)),
    ("How has it changed since last year?", TODAY, date(2025, 1, 1)),
    ("What was it on 2026-09-15?", date(2026, 9, 15), date(2026, 9, 15)),
    ("What's the limit this year?", TODAY, date(2026, 1, 1)),
    ("What's the limit?", TODAY, None),
])
def test_read_time(question, last, first):
    assert read_time(question, TODAY) == (last, first)


@pytest.fixture(autouse=True)
def hybrid(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "KG_CHAT", "hybrid")
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


def facts_for(sid, tmp_path, question=None):
    s = SCENARIOS[sid]
    with temp_db() as Session, Session() as session:
        uid = seed(session, s, TODAY)
        prepare_graph(session, uid, TODAY)
        gateway = PrivacyGateway(session, uid, audit_path=tmp_path / "audit.jsonl")
        return gateway.graph_retrieve(question or s.question)


@pytest.mark.parametrize("sid, year, cents", [("temporal-roth-2024", "2024", "$7,000.00"), ("temporal-401k-2024", "2024", "$23,000.00"),
                                              ("temporal-401k-2023", "2023", "$22,500.00"), ("temporal-401k-this-year", "2026", "$24,500.00")])
def test_a_limit_question_gets_that_years_number(sid, year, cents, tmp_path):
    found = facts_for(sid, tmp_path)
    assert any(cents in line and year in line for line in found["facts"]), found


def test_a_year_the_guidance_doesnt_cover_gets_no_limit(tmp_path):
    found = facts_for("abstain-roth-2030", tmp_path)
    assert not any("$" in line and "limit" in line for line in found["facts"]), found


@pytest.mark.parametrize("sid, expected", [
    ("temporal-gym-start", "first in June 2026"), ("temporal-first-shop", "first in March 2026"),
    ("temporal-subscription-stopped", "latest in July 2026"), ("temporal-budget-in-september", "$320.00 in September 2026"),
    ("temporal-budget-since-august", "$300.00 in August 2026"), ("temporal-decision-dismissed", "decision: dismissed"),
    ("temporal-decision-accepted", "decision: accepted"),
])
def test_a_temporal_question_finds_its_fact(sid, expected, tmp_path):
    found = facts_for(sid, tmp_path)
    assert any(expected in line for line in found["facts"]), found


def test_user_facts_are_audited_with_the_graph_purpose(tmp_path):
    facts_for("temporal-gym-start", tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [r["purpose"] for r in rows] == ["graph"]
    assert "eval-" not in rows[0]["prompt"] and "@" not in rows[0]["prompt"]


def test_guidance_alone_isnt_audited(tmp_path):
    facts_for("temporal-roth-2024", tmp_path)
    assert not (tmp_path / "audit.jsonl").exists()


def test_shuffled_facts_keep_their_length_and_change_their_values(tmp_path, monkeypatch):
    s = SCENARIOS["temporal-401k-2024"]
    q = "What was the Roth IRA and 401(k) limit in 2024?"
    plain = facts_for(s.id, tmp_path, q)["facts"]
    monkeypatch.setattr(config, "KG_CHAT", "hybrid-shuffled")
    mixed = facts_for(s.id, tmp_path, q)["facts"]
    assert plain != mixed and sum(map(len, plain)) == sum(map(len, mixed)) and len(plain) == len(mixed)
    assert sorted(l.split(": ", 1)[1] for l in plain) == sorted(l.split(": ", 1)[1] for l in mixed)
    assert mixed == facts_for(s.id, tmp_path, q)["facts"]       # the seed is fixed


def test_facts_reach_the_prompt_with_their_date(tmp_path):
    s = SCENARIOS["temporal-roth-2024"]
    model = ScriptedChatModel(replies=[AIMessage("x")])
    with temp_db() as Session, Session() as session:
        uid = seed(session, s, TODAY)
        prepare_graph(session, uid, TODAY)
        gateway = PrivacyGateway(session, uid, audit_path=tmp_path / "audit.jsonl")
        graph = build_chat_graph(make_tools(gateway, FakeKB()), model=model, graph_retrieve=gateway.graph_retrieve)
        state = graph.invoke({"messages": [HumanMessage(s.question)], "tool_rounds": 0})
    system = model.seen[0][0].content
    assert "Facts (valid as of 2024-12-31):" in system and "$7,000.00 for 2024" in system
    assert list(state["graph_by_turn"].values()) == [True]


def test_vector_mode_sends_no_graph_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KG_CHAT", "vector")
    calls = []
    model = ScriptedChatModel(replies=[AIMessage("x")])
    build_chat_graph([], model=model, graph_retrieve=lambda q: calls.append(q)).invoke(
        {"messages": [HumanMessage("hi")], "tool_rounds": 0})
    assert calls == [] and "Facts (valid" not in model.seen[0][0].content


def test_get_history_tool_only_in_graph_modes(tmp_path, monkeypatch):
    s = SCENARIOS["temporal-gym-start"]
    with temp_db() as Session, Session() as session:
        uid = seed(session, s, TODAY)
        prepare_graph(session, uid, TODAY)
        gateway = PrivacyGateway(session, uid, audit_path=tmp_path / "audit.jsonl")
        tools = {t.name: t for t in make_tools(gateway, FakeKB())}
        assert "first in June 2026" in tools["get_history"].invoke({"entity": "gym"})
        assert json.loads(tools["get_history"].invoke({"entity": "gym", "relation": "BUDGETS"}))["facts"] == []
        with pytest.raises(Exception):
            tools["get_history"].invoke({"entity": "gym", "relation": "nope"})
        monkeypatch.setattr(config, "KG_CHAT", "vector")
        assert "get_history" not in {t.name for t in make_tools(gateway, FakeKB())}


def test_a_roth_ira_finds_the_limit_on_the_ira_it_is_a_kind_of(tmp_path):
    found = facts_for("temporal-roth-2024", tmp_path, "What's the Roth IRA contribution limit this year?")
    assert any("$7,500.00 for 2026" in line for line in found["facts"]), found


def test_a_year_with_no_limit_says_so(tmp_path):
    found = facts_for("abstain-roth-2030", tmp_path)
    assert any("limit for 2030: not in the guidance" in line for line in found["facts"]), found


def test_a_price_change_is_stated_with_its_month(tmp_path):
    found = facts_for("temporal-price-change-month", tmp_path)
    assert any("$9.99 from March 2026, then $12.99 from August 2026" in line for line in found["facts"]), found


def test_a_steady_charge_states_no_price_steps(tmp_path):
    found = facts_for("temporal-gym-start", tmp_path)
    assert not any("priced" in line for line in found["facts"]), found


def test_advice_dates_read_as_words(tmp_path):
    found = facts_for("temporal-decision-dismissed", tmp_path)
    assert any("September 12, 2026" in line for line in found["facts"]), found


def _bound_tools(monkeypatch, tmp_path, found):
    s = SCENARIOS["temporal-gym-start"]
    model = ScriptedChatModel(replies=[AIMessage("x")])
    with temp_db() as Session, Session() as session:
        uid = seed(session, s, TODAY)
        gateway = PrivacyGateway(session, uid, audit_path=tmp_path / "audit.jsonl")
        graph = build_chat_graph(make_tools(gateway, FakeKB()), model=model, graph_retrieve=lambda q: found)
        graph.invoke({"messages": [HumanMessage("gym?")], "tool_rounds": 0})
    return model.tools_seen[0]


def test_get_history_is_left_out_when_facts_already_answer(monkeypatch, tmp_path):
    assert "get_history" not in _bound_tools(monkeypatch, tmp_path, {"as_of": "2026-11-06", "facts": ["a: b"]})


def test_get_history_is_offered_when_there_are_no_facts(monkeypatch, tmp_path):
    assert "get_history" in _bound_tools(monkeypatch, tmp_path, {"as_of": "2026-11-06", "facts": []})


@pytest.fixture
def dated_user(db):
    from app.db.models import User
    from app.kg.project import Assertion, apply
    with db() as s:
        s.add(User(id="dated", email="dated@example.test", password_hash="!"))
        s.flush()
        for month in (7, 8, 9, 11, 12):
            start = date(2026, month, 1)
            end = date(2027, 1, 1) if month == 12 else date(2026, month + 1, 1)
            apply(s, "dated", [
                Assertion(("user", "dated"), "BUDGETS", ("category", "FOOD_AND_DRINK"),
                          {"limit_cents": month * 10000}, start, end, dst_label="Food and drink"),
                Assertion(("user", "dated"), "SPENDS_AT", ("merchant", "cafe"),
                          {"cents": month * 100, "count": 1}, start, end, dst_label="Cafe"),
                Assertion(("user", "dated"), "SUBSCRIBES", ("stream", "out|cafe"),
                          {"typical_cents": month * 100}, start, end, dst_label="Cafe"),
                Assertion(("advice", f"advice-{month}"), "ABOUT", ("category", "FOOD_AND_DRINK"),
                          {"outcome": "accepted"}, start, end, src_label="Cafe suggestion"),
            ])
        yield s


@pytest.mark.parametrize("question", ["Cafe food and drink budget in September", "Cafe food and drink budget in September 2026",
                                      "Cafe food and drink budget on 2026-09-15"])
def test_requested_date_filters_every_user_fact(dated_user, question):
    found = gather(dated_user, "dated", question, TODAY)
    assert {f.rel for f in found.user} == {"BUDGETS", "SPENDS_AT", "SUBSCRIBES", "ABOUT"}
    assert any("$900.00 in September 2026" in f.line for f in found.user)
    assert all("November" not in f.line and "August" not in f.line and "December" not in f.line for f in found.user)


def test_september_question_cannot_use_november_only_budget(db):
    from app.db.models import User
    from app.kg.project import Assertion, apply
    with db() as s:
        s.add(User(id="u", email="u@example.test", password_hash="!"))
        s.flush()
        apply(s, "u", [Assertion(("user", "u"), "BUDGETS", ("category", "FOOD_AND_DRINK"),
                                 {"limit_cents": 90000}, date(2026, 11, 1), date(2026, 12, 1))])
        assert gather(s, "u", "What was my food and drink budget in September?", TODAY).user == ()


def test_since_window_filters_all_facts_and_preserves_changes(dated_user):
    found = gather(dated_user, "dated", "How have Cafe and my food and drink budget changed since August?", TODAY)
    assert {f.rel for f in found.user} == {"BUDGETS", "SPENDS_AT", "SUBSCRIBES", "ABOUT"}
    assert any("August 2026" in f.line and "November 2026" in f.line for f in found.user)
    assert all("July" not in f.line and "from December" not in f.line and "$12.00" not in f.line
               and "$1,200.00" not in f.line for f in found.user)


def test_explicit_history_retains_past_but_not_future_facts(dated_user):
    found = gather(dated_user, "dated", "Cafe food and drink", TODAY, history=True)
    assert any("July 2026" in f.line and "November 2026" in f.line for f in found.user)
    assert all("from December" not in f.line and "$12.00" not in f.line and "$1,200.00" not in f.line
               for f in found.user)


def test_last_month_is_a_snapshot_not_unbounded_history(dated_user):
    found = gather(dated_user, "dated", "Cafe food and drink budget last month", date(2026, 10, 20))
    assert any("$900.00 in September 2026" in f.line for f in found.user)
    assert all("July" not in f.line and "August" not in f.line and "November" not in f.line for f in found.user)


@pytest.fixture
def long_history(db):
    from app.db.models import User
    from app.kg.project import Assertion, _node, apply
    from app.kg.store import assert_edge
    with db() as s:
        s.add(User(id="long", email="long@example.test", password_hash="!"))
        s.flush()
        months = [(2024, m, 5000) for m in range(1, 13)] + [(2025, m, 6000) for m in range(1, 13)] + \
                 [(2026, m, 7000) for m in range(1, 12)]
        for year, month, cents in months:
            start, end = date(year, month, 1), add_months(date(year, month, 1), 1)
            apply(s, "long", [Assertion(("user", "long"), "SPENDS_AT", ("merchant", "fitclub gym"),
                                        {"cents": cents, "count": 1, "category": "PERSONAL_CARE"}, start, end,
                                        dst_label="FitClub Gym")], historical=True)
        advice = _node(s, "long", ("advice", "advice-1"), label="Venmo to John Smith increased from $5 to $9.")
        food = _node(s, "long", ("category", "FOOD_AND_DRINK"))
        assert_edge(s, user_id="long", src_id=advice.id, rel="ABOUT", dst_id=food.id,
                    value={"action": "alert", "outcome": "dismissed"}, valid_from=date(2026, 9, 15), valid_to=date(2026, 9, 16))
        yield s


def _merchant_line(session, question):
    return next(f.line for f in gather(session, "long", question, TODAY).user if f.rel == "SPENDS_AT")


@pytest.mark.parametrize("question", ["Has my FitClub gym gone up in price?", "How many months have I paid FitClub?",
                                      "How much do I pay FitClub?"])
def test_a_question_naming_no_period_keeps_the_whole_history(long_history, question):
    line = _merchant_line(long_history, question)
    assert "first in January 2024" in line and "in 35 months" in line
    assert "$50.00 from January 2024, then $60.00 from January 2025, then $70.00 from January 2026" in line


def test_a_year_means_the_whole_year_with_its_total(long_history):
    line = _merchant_line(long_history, "How much did I spend at FitClub in 2024?")
    assert "in the period asked" in line and "first in January 2024" in line and "latest in December 2024" in line
    assert "in 12 months" in line and "$600.00 in total" in line


def test_last_year_means_the_whole_of_last_year(long_history):
    line = _merchant_line(long_history, "How much did I spend at FitClub last year?")
    assert "first in January 2025" in line and "latest in December 2025" in line and "$720.00 in total" in line


def test_since_a_year_runs_to_today(long_history):
    line = _merchant_line(long_history, "How has FitClub changed since 2025?")
    assert "first in January 2025" in line and "latest in November 2026" in line and "2024" not in line


@pytest.mark.parametrize("question", ["What did I decide about food and drink in September?",
                                      "What advice did you give on food and drink in September?",
                                      "What did I decide about food and drink in 2026?"])
def test_advice_dated_inside_the_period_is_found(long_history, question):
    assert any(f.rel == "ABOUT" for f in gather(long_history, "long", question, TODAY).user)


def test_advice_outside_the_period_isnt(long_history):
    assert not gather(long_history, "long", "What did I decide about food and drink in August?", TODAY).user


def test_a_budget_alone_narrows_to_today_when_no_period_is_named(dated_user):
    question = "What's my food and drink budget?"
    assert [f.line for f in gather(dated_user, "dated", question, date(2026, 9, 10)).user if f.rel == "BUDGETS"] == [
        "food and drink budget limit: $900.00 in September 2026"]
    changed = gather(dated_user, "dated", "Has my food and drink budget gone up?", date(2026, 9, 10)).user
    assert any(f.rel == "BUDGETS" and "July 2026" in f.line and "September 2026" in f.line for f in changed)


def test_advice_text_is_scrubbed_before_the_model_sees_it(tmp_path, long_history):
    gateway = PrivacyGateway(long_history, "long", audit_path=tmp_path / "audit.jsonl")
    found = gateway.graph_retrieve("What did I decide about food and drink?")
    line = next(l for l in found["facts"] if "decision: dismissed" in l)
    assert "Venmo to [person]" in line and "John" not in line and "Smith" not in line
    assert "John" not in (tmp_path / "audit.jsonl").read_text()
