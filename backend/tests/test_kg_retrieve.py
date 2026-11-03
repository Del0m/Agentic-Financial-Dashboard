import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app import config
from app.agents.chat_graph import build_chat_graph
from app.agents.tools import make_tools
from app.kg.retrieve import gather, read_time
from app.privacy.gateway import PrivacyGateway
from eval.fixtures import load_scenarios, prepare_graph, seed, temp_db
from pathlib import Path
from tests.fakes import FakeKB, ScriptedChatModel

REPO = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 6)
SCENARIOS = {s.id: s for s in load_scenarios(REPO / "eval" / "scenarios")}


@pytest.mark.parametrize("question, on_date, since", [
    ("What was the limit in 2024?", date(2024, 6, 1), None),
    ("What was it last year?", date(2025, 6, 1), None),
    ("What did I spend last month?", date(2026, 10, 1), None),
    ("What was it in September?", date(2026, 9, 1), None),
    ("What was it in December?", date(2025, 12, 1), None),
    ("How has it changed since August?", TODAY, date(2026, 8, 1)),
    ("What's the limit this year?", TODAY, None),
])
def test_read_time(question, on_date, since):
    assert read_time(question, TODAY) == (on_date, since)


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
    assert "Facts (valid as of 2024-06-01):" in system and "$7,000.00 for 2024" in system
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
