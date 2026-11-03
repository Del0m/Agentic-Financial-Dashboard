import json
import re
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app import config
from app.agents.tools import make_tools
from app.privacy.gateway import PrivacyGateway
from eval import agent_runner
from eval.agent_runner import run_budget_once, run_goal_once
from eval.fixtures import Expect, load_scenarios, seed, temp_db
from eval.metrics import check_facts, extract_numbers, score_budget, score_goal
from tests.fakes import FakeKB, SchemaReplyModel, ScriptedChatModel

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = load_scenarios(REPO / "eval" / "scenarios")
CHAT = [s for s in SCENARIOS if s.kind == "chat"]
BUDGET = [s for s in SCENARIOS if s.kind == "budget"]
GOAL = [s for s in SCENARIOS if s.kind == "goal"]

# the conftest swaps the forecast out for the suite's fixtures with static balances. the eval runs the real one
pytestmark = [pytest.mark.cash_forecast, pytest.mark.forecast_surplus]

# scenarios whose golden answer is right and whose agent isn't yet. each stays failing in the results until its ticket is
# fixed, and a new mismatch fails test_budget_code_decisions_match_the_golden_answer
KNOWN_FAILURES = {"control-steady-spender": "TPC-45"}


def scenario(sid):
    return next(s for s in SCENARIOS if s.id == sid)


def ids(scenarios):
    return [s.id for s in scenarios]


@pytest.fixture
def audit(tmp_path):
    return tmp_path / "audit.jsonl"


# --- the runners ---

def test_budget_run_records_flags_and_alerts_by_day(audit):
    rec = run_budget_once(scenario("budget-one-time-new-shop"), model=SchemaReplyModel(), audit_path=audit, run=2)
    assert (rec.scenario_id, rec.run, rec.kind, rec.error) == ("budget-one-time-new-shop", 2, "budget", None)
    assert rec.flags == {"GENERAL_MERCHANDISE": "2026-10-12"}
    first = next(a for a in rec.alerts if a["kind"] == "overspend")
    assert (first["day"], first["category"], first["cause"], first["action"]) == (
        "2026-10-12", "GENERAL_MERCHANDISE", "one_time", "alert")
    assert rec.latency_s > 0 and rec.llm_failures == 0


# each alert once, on the day of the run that wrote it, however many runs the window has
def test_budget_run_lists_each_alert_once(audit):
    rec = run_budget_once(scenario("signal-price-increase"), model=SchemaReplyModel(), audit_path=audit)
    prices = [a for a in rec.alerts if a["kind"] == "price_increase"]
    assert [a["day"] for a in prices] == ["2026-10-03"]


def test_budget_run_counts_model_failures(audit):
    class Rambling(SchemaReplyModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="not json"))])

    rec = run_budget_once(scenario("budget-one-time-new-shop"), model=Rambling(), audit_path=audit)
    assert rec.error is None and rec.llm_failures > 0
    # the safety net: no model, and the user is still warned, with no cause
    [first, *_] = [a for a in rec.alerts if a["kind"] == "overspend"]
    assert first["cause"] is None and first["action"] == "alert" and "limit" in first["message"]


# prompts from an eval go to the run's folder, never to the app's audit log, and the setting comes back
def test_budget_run_audits_to_its_own_file_and_restores_the_setting(audit, tmp_path, monkeypatch):
    real = tmp_path / "real.jsonl"
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", real)
    run_budget_once(scenario("budget-one-time-new-shop"), model=SchemaReplyModel(), audit_path=audit)
    assert audit.exists() and not real.exists()
    assert config.PROMPT_AUDIT_PATH == real


def test_budget_run_failure_is_a_result_and_restores_the_setting(audit, tmp_path, monkeypatch):
    real = tmp_path / "real.jsonl"
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", real)
    monkeypatch.setattr(agent_runner, "advance_days", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sc = scenario("budget-one-time-new-shop")
    rec = run_budget_once(sc, model=SchemaReplyModel(), audit_path=audit)
    assert rec.error == "RuntimeError: boom" and config.PROMPT_AUDIT_PATH == real
    out = score_budget(rec.to_dict(), sc)
    assert not out["passed"] and not any(out["checks"].values())


def goals_reply(*goals):
    return AIMessage(content=json.dumps({"goals": [
        {"kind": kind, "monthly_dollars": dollars, "rationale": "Because it fits.",
         **({"strategy": "avalanche"} if kind == "debt_payoff" else {})} for kind, dollars in goals]}))


def test_goal_run_records_plans_in_priority_order(audit):
    model = ScriptedChatModel(replies=[goals_reply(("debt_payoff", 300), ("savings", 100))])
    rec = run_goal_once(scenario("goal-debt-first"), model=model, audit_path=audit, run=3)
    assert (rec.run, rec.kind, rec.status, rec.attempts, rec.error) == (3, "goal", "ok", 1, None)
    assert (rec.surplus_cents, rec.cap_cents) == (102000, 81600)
    assert rec.offered == ["debt_payoff", "emergency_fund", "savings"]
    assert [(p["kind"], p["priority"], p["monthly_cents"]) for p in rec.plans] == [("debt_payoff", 1, 30000),
                                                                                   ("savings", 2, 10000)]
    assert rec.plans[0]["strategy"] == "avalanche" and rec.plans[0]["target_date"]
    assert audit.exists()


def test_goal_run_applies_the_scenarios_prefs(audit):
    rec = run_goal_once(scenario("goal-respects-skipped-kind"), model=ScriptedChatModel(replies=[goals_reply(("savings", 100))]),
                        audit_path=audit)
    assert rec.offered == ["emergency_fund", "savings"]


# the retry is the real graph's: an over-cap proposal goes back once with a correction
def test_goal_run_reports_a_retry(audit):
    model = ScriptedChatModel(replies=[goals_reply(("savings", 5000)), goals_reply(("savings", 100))])
    sc = scenario("goal-debt-first")
    rec = run_goal_once(sc, model=model, audit_path=audit)
    assert rec.attempts == 2 and [p["kind"] for p in rec.plans] == ["savings"]
    out = score_goal(rec.to_dict(), sc)
    assert out["attempts"] == 2 and not out["checks"]["first_kind"] and out["checks"]["under_cap"]


def test_goal_run_with_a_broken_model_is_a_result(audit):
    sc = scenario("goal-debt-first")
    rec = run_goal_once(sc, model=ScriptedChatModel(replies=[ConnectionError("down")]), audit_path=audit)
    assert rec.status == "error" and rec.plans == []
    assert not score_goal(rec.to_dict(), sc)["passed"]


def test_goal_setup_failure_is_a_result(audit, monkeypatch):
    monkeypatch.setattr(agent_runner, "run_goals", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rec = run_goal_once(scenario("goal-debt-first"), model=None, audit_path=audit)
    assert rec.error == "RuntimeError: boom" and rec.status is None


# --- every golden answer, checked against the code with no model ---

@pytest.mark.parametrize("s", CHAT, ids=ids(CHAT))
def test_chat_golden_answer_passes_its_own_facts(s):
    result = check_facts(s.golden_answer, s.expect)
    assert result.passed, (result.missing, result.forbidden_hits)


# the output of each tool the scenario expects, the way a model that picks the right arguments would call it
def _tool_outputs(s, tools) -> str:
    out = []
    for name in s.expect.tools:
        if name == "plan_debt_payoff":
            extra = re.search(r"\$(\d+)", s.question)
            out.append(tools[name].invoke({"extra_monthly": float(extra.group(1)) if extra else 0}))
        elif name == "get_spending":
            out.append(tools[name].invoke({}))
            out += [tools[name].invoke({"category": c}) for c in {t["category"] for t in s.user.get("transactions", [])}]
        else:
            out.append(tools[name].invoke({}))
    return "\n".join(out)


# a number the tools can't produce is one the model could only pass by doing arithmetic or by chance
@pytest.mark.parametrize("s", CHAT, ids=ids(CHAT))
def test_chat_number_facts_come_from_the_expected_tools(s, audit):
    with temp_db() as Session, Session() as session:
        user_id = seed(session, s, date.today())      # the gateway's own clock, as in a real run
        gateway = PrivacyGateway(session, user_id, audit_path=audit)
        tools = {t.name: t for t in make_tools(gateway, FakeKB())}
        numbers = extract_numbers(_tool_outputs(s, tools))
    for fact in s.expect.facts:
        if fact.number is not None:
            assert any(abs(n - fact.number) <= fact.tol + 1e-9 for n in numbers), (fact.label, sorted(numbers))


# a scenario expecting the model to abstain must not be answerable from what the tools return
def test_debt_none_has_no_debts_to_find(audit):
    s = scenario("debt-none")
    with temp_db() as Session, Session() as session:
        gateway = PrivacyGateway(session, seed(session, s, date.today()), audit_path=audit)
        tools = {t.name: t for t in make_tools(gateway, FakeKB())}
        assert json.loads(tools["get_debts"].invoke({}))["data"] == []


@pytest.mark.parametrize("s", BUDGET, ids=ids(BUDGET))
def test_budget_golden_answer_states_its_message_facts(s):
    assert check_facts(s.golden_answer, _facts_only(s.expect.message_facts)).passed


def _facts_only(facts):
    return Expect(facts=facts, forbid=[], tools=[], sources=[])


# what code decides, whatever the model says: which categories get flagged and when, which signals fire, how far ahead.
# cause, action and message are the model's, and only a live run can score them
CODE_CHECKS = ("flags", "flag_by", "alert_kinds", "lead_time")


@pytest.mark.parametrize("s", BUDGET, ids=ids(BUDGET))
def test_budget_code_decisions_match_the_golden_answer(s, audit):
    rec = run_budget_once(s, model=SchemaReplyModel(), audit_path=audit).to_dict()
    assert rec["error"] is None
    checks = score_budget(rec, s)["checks"]
    wrong = sorted(name for name in CODE_CHECKS if checks.get(name) is False)
    if s.id in KNOWN_FAILURES:
        # fixed? then this entry and its ticket are done
        assert wrong, f"{s.id} passes now: remove it from KNOWN_FAILURES and close {KNOWN_FAILURES[s.id]}"
    else:
        assert not wrong, (wrong, rec["flags"])


# a signal's fallback message is written by code, so its facts hold with no model at all
@pytest.mark.parametrize("sid", ["signal-price-increase", "signal-low-balance-before-payday"])
def test_signal_fallback_messages_carry_their_facts(sid, audit):
    s = scenario(sid)
    rec = run_budget_once(s, model=SchemaReplyModel(), audit_path=audit).to_dict()
    assert score_budget(rec, s)["checks"]["message"], rec["alerts"]


# replies a sensible model could give, from the expected answer, so these check the scenario and not a model. a scenario
# where nothing is affordable gets no replies: a model call would raise and fail its run
def _goal_replies(s):
    if s.expect.status != "ok":
        return []
    kinds = list(dict.fromkeys(([s.expect.first_kind] if s.expect.first_kind else []) + list(s.expect.required_kinds)))
    return [goals_reply(*((k, 400) for k in kinds or ["savings"]))]


@pytest.mark.parametrize("s", GOAL, ids=ids(GOAL))
def test_goal_scenario_passes_with_a_sensible_model(s, audit):
    rec = run_goal_once(s, model=ScriptedChatModel(replies=_goal_replies(s)), audit_path=audit).to_dict()
    out = score_goal(rec, s)
    assert out["passed"], (out["checks"], rec["status"], rec["reason"], rec["error"])
