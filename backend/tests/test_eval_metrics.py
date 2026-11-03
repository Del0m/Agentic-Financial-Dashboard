import math
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.fixtures import ActionRule, BudgetExpect, BudgetScenario, Expect, Fact, GoalExpect, GoalScenario, Scenario
from eval.metrics import (SIM_PREFIX, check_facts, extract_numbers, mape, score_budget, score_goal, score_run,
                          similarity, sources_ok, summarize, summarize_budget, summarize_goal, tools_ok)

CHART = "https://www.irs.gov/retirement-plans/roth-comparison-chart"


def expect(facts=(), forbid=(), tools=(), sources=()):
    return Expect(facts=list(facts), forbid=list(forbid), tools=list(tools), sources=list(sources))


def scenario(**kw):
    exp = kw.pop("expect", expect([Fact(text="Platinum Card"), Fact(number=24.99, tol=0.01)]))
    return Scenario(id=kw.get("id", "s1"), question="q?", user={}, golden_answer="gold", expect=exp,
                    path=Path("s1.json"))


def record(answer="", error=None, latency=1.0, sid="s1", tools=(), sources=()):
    return SimpleNamespace(scenario_id=sid, answer=answer, error=error, latency_s=latency,
                           tool_calls=[{"name": t, "args": {}} for t in tools], tool_results=[],
                           sources=[{"source_url": u} for u in sources])


# returns preset vectors in order and records what it was asked to embed
class FakeEmbeddings:
    def __init__(self, vectors):
        self.vectors, self.seen = vectors, []

    def embed_documents(self, texts):
        self.seen.append(texts)
        return self.vectors


class Explodes:
    def __getattr__(self, name):
        raise AssertionError(f"model should not be called ({name})")


def test_extract_numbers_formats():
    nums = extract_numbers("$3,200.00 at 24.99% and 24.99 percent, refund -12.50")
    assert 3200.0 in nums and 24.99 in nums and -12.5 in nums
    k = extract_numbers("about 3.2k a month")
    assert 3200.0 not in k and 3.2 not in k


def test_401k_does_not_match_401_fact():
    assert check_facts("put it in a 401(k) plan", expect([Fact(number=401)])).found == 0


def test_number_fact_tolerance():
    e = expect([Fact(number=3200, tol=1)])
    assert check_facts("$3,200.99", e).passed
    assert not check_facts("$3,201.50", e).passed


def test_text_fact_case_insensitive():
    assert check_facts("Pay the PLATINUM CARD first", expect([Fact(text="platinum card")])).passed


def test_forbidden_fails_complete_answer():
    r = check_facts("Platinum Card at 24.99%, see https://x", expect(
        [Fact(text="Platinum Card"), Fact(number=24.99, tol=0.01)], forbid=["http"]))
    assert (r.found, r.total, r.passed, r.forbidden_hits) == (2, 2, False, ["http"])


def test_missing_lists_labels():
    r = check_facts("nothing useful", expect([Fact(text="Platinum Card"), Fact(number=24.99, tol=0.01)]))
    assert r.missing == ["Platinum Card", "24.99±0.01"]
    assert r.score == 0.0


def test_tools_and_sources_subset():
    calls = [{"name": "get_debts", "args": {}}, {"name": "get_accounts", "args": {}}]
    assert tools_ok(expect(tools=["get_debts"]), calls)
    assert not tools_ok(expect(tools=["get_spending"]), calls)
    assert tools_ok(expect(), [])
    assert sources_ok(expect(sources=[CHART]), [{"source_url": CHART}, {"source_url": "x"}])
    assert not sources_ok(expect(sources=[CHART]), [{"source_url": "x"}])
    assert sources_ok(expect(), [])


def test_similarity_cosine():
    fake = FakeEmbeddings([[1.0, 0.0], [1.0, 1.0]])
    assert similarity("answer", "golden", fake) == pytest.approx(1 / math.sqrt(2))
    assert fake.seen == [[SIM_PREFIX + "answer", SIM_PREFIX + "golden"]]
    assert SIM_PREFIX == "clustering: "


def test_similarity_empty_answer_is_zero():
    assert similarity("  ", "golden", Explodes()) == 0.0


def test_score_errored_run_skips_models():
    s = score_run(record(error="ConnectionError: down"), scenario(), embeddings=Explodes(), judge_model=Explodes())
    assert s["similarity"] == 0.0 and s["judge"] is None and s["judge_passed"] is False
    assert s["facts"]["found"] == 0 and s["facts"]["passed"] is False


def scored(sid, *, error=None, fact_found=1, sim=0.9, judge=True, latency=1.0, tools_ok=True):
    return {"record": {"scenario_id": sid, "error": error, "latency_s": latency},
            "scores": {"facts": {"found": fact_found, "total": 1, "passed": fact_found == 1,
                                 "score": float(fact_found)},
                       "tools_ok": tools_ok, "sources_ok": True, "similarity": sim,
                       "judge": {"coherent": 5, "consistent_with_data": 4, "safe": 5} if judge else None,
                       "judge_passed": judge}}


def test_summarize_counts_errors_and_rates():
    items = [scored("a", error="Boom: x", fact_found=0, sim=0.0, judge=False, latency=3.0, tools_ok=False),
             scored("a", sim=0.9, latency=1.0),
             scored("b", sim=0.5, latency=2.0)]
    out = summarize(items)
    o = out["overall"]
    assert (o["runs"], o["errors"]) == (3, 1)
    assert o["similarity_at_target_rate"] == pytest.approx(1 / 3)
    assert o["fact_pass_rate"] == pytest.approx(2 / 3)
    assert o["judge_pass_rate"] == pytest.approx(2 / 3)
    assert o["judge_means"]["consistent_with_data"] == 4
    assert o["tool_accuracy"] == pytest.approx(2 / 3)
    assert o["latency_p95_s"] == 3.0 and o["latency_mean_s"] == 2.0
    a = out["scenarios"]["a"]
    assert (a["runs"], a["errors"]) == (2, 1)
    assert (a["similarity_min"], a["similarity_max"]) == (0.0, 0.9)
    assert (a["fact_score_min"], a["fact_score_max"]) == (0.0, 1.0)


# --- coherence judge (task 3) ---

from langchain_core.messages import AIMessage

from eval.metrics import JUDGE_PASS, judge
from tests.fakes import ScriptedChatModel

GOOD = '{"coherent": 4, "consistent_with_data": 5, "safe": 4, "reason": "fine"}'


def judge_with(*replies):
    model = ScriptedChatModel(replies=[r if isinstance(r, Exception) else AIMessage(r) for r in replies])
    return judge("Which debt first?", ['{"data": [{"name": "Platinum Card"}]}'], "Pay the Platinum Card.", model), model


def test_judge_parses_json():
    result, model = judge_with(GOOD)
    assert (result.coherent, result.consistent_with_data, result.safe) == (4, 5, 4)
    assert result.passed and JUDGE_PASS == 4
    prompt = model.seen[0][-1].content
    assert "Which debt first?" in prompt and "Platinum Card" in prompt and "Pay the Platinum Card." in prompt


def test_judge_extracts_from_code_fence():
    result, _ = judge_with(f"Here you go:\n```json\n{GOOD}\n```\nHope that helps.")
    assert result is not None and result.reason == "fine"


def test_judge_retries_once():
    result, model = judge_with("not json", GOOD)
    assert result is not None and len(model.seen) == 2


def test_judge_two_failures_returns_none():
    result, model = judge_with("nope", "still nope")
    assert result is None and len(model.seen) == 2


def test_judge_out_of_range_rejected():
    bad = GOOD.replace('"coherent": 4', '"coherent": 7')
    assert judge_with(bad, bad)[0] is None


def test_judge_model_exception_returns_none():
    assert judge_with(TimeoutError(), TimeoutError())[0] is None


def test_judge_pass_threshold():
    result, _ = judge_with('{"coherent": 3, "consistent_with_data": 5, "safe": 5, "reason": "x"}')
    assert result is not None and not result.passed


def test_score_run_uses_judge():
    model = ScriptedChatModel(replies=[AIMessage(GOOD)])
    s = score_run(record(answer="Pay the Platinum Card at 24.99%."), scenario(),
                  embeddings=FakeEmbeddings([[1.0, 0.0], [1.0, 0.0]]), judge_model=model)
    assert s["judge_passed"] is True and s["judge"]["consistent_with_data"] == 5


def test_judge_told_when_expected_tools_missing():
    model = ScriptedChatModel(replies=[AIMessage(GOOD)])
    judge("Card or emergency fund?", [], "Generic advice.", model, missing_tools=["get_debts"])
    prompt = model.seen[0][-1].content
    assert "did not look up" in prompt and "get_debts" in prompt


def test_score_run_passes_missing_tools_to_judge():
    model = ScriptedChatModel(replies=[AIMessage(GOOD)])
    sc = scenario(expect=expect([Fact(text="x")], tools=["get_debts"]))
    score_run(record(answer="Generic advice."), sc, embeddings=FakeEmbeddings([[1.0], [1.0]]), judge_model=model)
    assert "get_debts" in model.seen[0][-1].content


def test_mape_known_case():
    assert mape([(110, 100), (90, 100), (200, 100)]) == 40.0


def test_mape_empty():
    assert mape([]) is None


def test_mape_rounds():
    assert mape([(1, 3)]) == 66.7


# review on #10: a run that crashed never called a tool or retrieved a source, even when none were expected
def test_errored_run_fails_tools_and_sources():
    s = score_run(record(error="ConnectionError: down"), scenario(expect=expect([Fact(text="x")])),
                  embeddings=Explodes(), judge_model=Explodes())

    assert s["tools_ok"] is False and s["sources_ok"] is False


# review on #10: a value exactly one tol away passes on both sides, despite float rounding
def test_number_tolerance_is_symmetric():
    e = expect([Fact(number=124.25, tol=0.01)])

    assert check_facts("$124.24", e).passed and check_facts("$124.26", e).passed
    assert not check_facts("$124.27", e).passed


# --- abstention, pass^3, and budget and goal scoring (tpc-26) ---

ABSTAIN = Expect(facts=[], forbid=["http"], tools=[], sources=[], abstain=True)


@pytest.mark.parametrize("answer", ["I don't have your balance from 2019.", "I don’t have that.",
                                    "That isn't available in your data.", "I do not see any purchases at Costco.",
                                    "The IRS has not yet published that limit."])
def test_abstain_passes_when_it_says_so(answer):
    result = check_facts(answer, ABSTAIN)
    assert result.passed and (result.found, result.total, result.score) == (1, 1, 1.0)


def test_abstain_fails_without_saying_so():
    result = check_facts("Your balance was fine that year.", ABSTAIN)
    assert not result.passed and result.missing == ["says the data isn't available"] and result.score == 0.0


@pytest.mark.parametrize("answer", ["I don't have 2019, but it was about $1,200.", "I don't have that; maybe 1200 dollars.",
                                    "I don't have that: around 300 USD."])
def test_abstain_fails_on_a_dollar_amount(answer):
    result = check_facts(answer, ABSTAIN)
    assert not result.passed and result.missing == ["states no dollar amount"]


# a year in the question isn't a stated amount
def test_abstain_allows_a_bare_year():
    assert check_facts("I don't have your balance from 2019.", ABSTAIN).passed


def test_abstain_still_honours_forbid():
    result = check_facts("I don't have that, see http://x", ABSTAIN)
    assert not result.passed and result.forbidden_hits == ["http"] and not result.missing


GM = "GENERAL_MERCHANDISE"


def budget_scenario(flags=(GM,), **kw):
    fields = dict(causes={}, actions={}, flag_by={}, alert_kinds=(), message_facts=[], min_days_ahead=None)
    return BudgetScenario(id=kw.pop("id", "b1"), profile="p", start=date(2026, 10, 1), end=date(2026, 10, 3),
                          golden_answer="gold", path=Path("b1.json"), tags=kw.pop("tags", ()),
                          expect=BudgetExpect(flags=flags, **{**fields, **kw}))


def alert(kind="overspend", category=GM, cause="one_time", action="alert", day="2026-10-02", message="ElectroMart hit",
          signal_days=None):
    return {"day": day, "kind": kind, "category": category, "cause": cause, "action": action, "severity": "high",
            "message": message, "signal_days": signal_days}


def budget_record(flags=None, alerts=(), error=None, failures=0, sid="b1"):
    return {"scenario_id": sid, "run": 1, "flags": {GM: "2026-10-02"} if flags is None else flags, "alerts": list(alerts),
            "llm_failures": failures, "latency_s": 1.0, "error": error, "kind": "budget"}


def test_budget_flags_are_exact():
    assert score_budget(budget_record(), budget_scenario())["checks"] == {"flags": True}
    extra = score_budget(budget_record(flags={GM: "2026-10-02", "TRAVEL": "2026-10-03"}), budget_scenario())
    assert not extra["passed"] and extra["extra_flags"] == ["TRAVEL"]
    assert not score_budget(budget_record(flags={}), budget_scenario())["passed"]


# a signal scenario leaves flags out: lumpy pace flags on the same user aren't what it's testing
def test_budget_flags_left_out_are_not_checked():
    out = score_budget(budget_record(flags={"TRAVEL": "2026-10-03"}, alerts=[alert(kind="price_increase", category=None)]),
                       budget_scenario(flags=None, alert_kinds=("price_increase",)))
    assert out["checks"] == {"alert_kinds": True} and out["extra_flags"] == []


def test_budget_cause_scores_the_first_alert():
    scenario = budget_scenario(causes={GM: "one_time"})
    right_first = [alert(), alert(cause="recurring", day="2026-10-03")]
    wrong_first = [alert(cause="recurring"), alert(day="2026-10-03")]
    assert score_budget(budget_record(alerts=right_first), scenario)["checks"]["cause"]
    assert not score_budget(budget_record(alerts=wrong_first), scenario)["checks"]["cause"]
    assert not score_budget(budget_record(alerts=[]), scenario)["checks"]["cause"]


def test_budget_action_accepts_a_set_and_bans_any_alert():
    scenario = budget_scenario(actions={GM: ActionRule(accept=("alert", "suggest_cuts"), ban=("adjust_next_budget",))})
    assert score_budget(budget_record(alerts=[alert(action="suggest_cuts")]), scenario)["checks"]["action"]
    assert not score_budget(budget_record(alerts=[alert(action="no_op")]), scenario)["checks"]["action"]
    later_ban = [alert(), alert(action="adjust_next_budget", day="2026-10-03")]
    assert not score_budget(budget_record(alerts=later_ban), scenario)["checks"]["action"]


def test_budget_flag_by_is_a_deadline():
    by = budget_scenario(flag_by={GM: date(2026, 10, 2)})
    assert score_budget(budget_record(), by)["checks"]["flag_by"]
    assert not score_budget(budget_record(flags={GM: "2026-10-03"}), by)["checks"]["flag_by"]
    assert not score_budget(budget_record(flags={}), by)["checks"]["flag_by"]


def test_budget_alert_kinds_must_all_appear():
    scenario = budget_scenario(flags=None, alert_kinds=("price_increase", "low_balance"))
    assert not score_budget(budget_record(alerts=[alert(kind="low_balance")]), scenario)["passed"]
    both = [alert(kind="low_balance"), alert(kind="price_increase")]
    assert score_budget(budget_record(alerts=both), scenario)["passed"]


def test_budget_message_facts_read_every_alert():
    scenario = budget_scenario(message_facts=[Fact(text="electromart"), Fact(number=43, tol=0.01)])
    alerts = [alert(message="ElectroMart hit"), alert(message="Now $43.00", day="2026-10-03")]
    assert score_budget(budget_record(alerts=alerts), scenario)["checks"]["message"]
    assert not score_budget(budget_record(alerts=alerts[:1]), scenario)["checks"]["message"]


def test_budget_lead_time_takes_the_earliest_warning():
    scenario = budget_scenario(flags=None, min_days_ahead=3)
    alerts = [alert(kind="low_balance", signal_days=2), alert(kind="low_balance", signal_days=6)]
    out = score_budget(budget_record(alerts=alerts), scenario)
    assert out["checks"]["lead_time"] and out["low_balance_days_ahead"] == 6
    assert not score_budget(budget_record(alerts=alerts[:1]), scenario)["checks"]["lead_time"]
    assert not score_budget(budget_record(alerts=[]), scenario)["checks"]["lead_time"]


# the code's plain alert still warned the user: the flag passes, and what the model should have added fails on its own
def test_budget_fallback_scores_each_check_apart():
    scenario = budget_scenario(causes={GM: "one_time"}, message_facts=[Fact(text="ElectroMart")])
    fallback = alert(cause=None, message="General merchandise is over its $150.00 limit: $600.00 so far.")
    out = score_budget(budget_record(alerts=[fallback], failures=2), scenario)
    assert out["checks"] == {"flags": True, "cause": False, "message": False}
    assert not out["passed"] and out["llm_failures"] == 2


def test_budget_control_false_alarm():
    control = budget_scenario(flags=())
    assert control.is_control
    quiet = score_budget(budget_record(flags={}), control)
    assert quiet["passed"] and not quiet["false_alarm"]
    assert score_budget(budget_record(), control)["false_alarm"]
    # a low balance warning isn't an overspend alarm
    assert not score_budget(budget_record(flags={}, alerts=[alert(kind="low_balance")]), control)["false_alarm"]


def test_budget_error_fails_every_check():
    out = score_budget(budget_record(error="Boom: x"), budget_scenario(flags=()))
    assert out["checks"] == {"flags": False} and not out["passed"] and not out["false_alarm"]


def test_budget_flag_to_alert_days():
    out = score_budget(budget_record(alerts=[alert(day="2026-10-03")]), budget_scenario())
    assert out["flag_to_alert_days"] == [1]


def goal_scenario(status="ok", required=(), banned=(), first=None, sid="g1"):
    return GoalScenario(id=sid, profile="p", today=date(2026, 11, 2), prefs=None, golden_answer="gold",
                        expect=GoalExpect(status, required, banned, first), path=Path("g1.json"))


def plan(kind, priority=1, cents=10000):
    return {"kind": kind, "priority": priority, "monthly_cents": cents}


def goal_record(status="ok", plans=(), cap=50000, attempts=1, error=None, sid="g1"):
    return {"scenario_id": sid, "run": 1, "status": status, "reason": None, "cap_cents": cap, "surplus_cents": cap,
            "offered": [], "attempts": attempts, "plans": list(plans), "latency_s": 1.0, "error": error, "kind": "goal"}


def test_goal_status_required_banned_and_first():
    scenario = goal_scenario(required=("debt_payoff",), banned=("savings",), first="debt_payoff")
    good = score_goal(goal_record(plans=[plan("emergency_fund", 2), plan("debt_payoff", 1)]), scenario)
    assert good["passed"] and good["checks"] == {"status": True, "banned_kinds": True, "required_kinds": True,
                                                 "first_kind": True, "under_cap": True}
    wrong_order = score_goal(goal_record(plans=[plan("emergency_fund", 1), plan("debt_payoff", 2)]), scenario)
    assert wrong_order["checks"]["required_kinds"] and not wrong_order["checks"]["first_kind"]
    banned = score_goal(goal_record(plans=[plan("debt_payoff", 1), plan("savings", 2)]), scenario)
    assert not banned["checks"]["banned_kinds"]
    assert not score_goal(goal_record(status="error"), scenario)["checks"]["status"]


def test_goal_over_cap_fails_even_with_the_right_kinds():
    over = score_goal(goal_record(plans=[plan("savings", cents=50001)]), goal_scenario())
    assert not over["checks"]["under_cap"] and not over["passed"]
    assert score_goal(goal_record(plans=[plan("savings", cents=50000)]), goal_scenario())["checks"]["under_cap"]
    assert not score_goal(goal_record(plans=[]), goal_scenario())["checks"]["under_cap"]


def test_goal_nothing_affordable_means_no_model_call():
    scenario = goal_scenario(status="no_surplus")
    assert score_goal(goal_record(status="no_surplus", cap=None, attempts=0), scenario)["checks"] == {
        "status": True, "banned_kinds": True, "model_not_called": True}
    assert not score_goal(goal_record(status="no_surplus", attempts=1), scenario)["passed"]


# retries are reported, and a valid final plan still passes
def test_goal_attempts_are_reported_not_failed():
    out = score_goal(goal_record(plans=[plan("savings")], attempts=2), goal_scenario())
    assert out["passed"] and out["attempts"] == 2


def test_goal_error_fails_every_check():
    out = score_goal(goal_record(error="Boom: x", plans=[plan("savings")]), goal_scenario())
    assert not out["passed"] and not any(out["checks"].values())


def chat_item(sid, passed):
    return {"record": {"scenario_id": sid, "error": None, "latency_s": 1.0},
            "scores": {"facts": {"passed": passed, "score": 1.0 if passed else 0.0}, "similarity": 0.9, "judge": None,
                       "judge_passed": False, "tools_ok": True, "sources_ok": True}}


# a 3-run mean of 83% hides that only half the scenarios pass every time
def test_summarize_reports_pass_on_all_runs_and_tags():
    items = [chat_item("a", True), chat_item("a", True), chat_item("a", True),
             chat_item("b", True), chat_item("b", True), chat_item("b", False)]
    out = summarize(items, tags={"a": ("debt",), "b": ("debt", "kb")})
    assert out["overall"]["fact_pass_rate"] == 5 / 6 and out["overall"]["fact_pass_all_runs_rate"] == 0.5
    assert out["by_tag"] == {"debt": {"runs": 6, "fact_pass_rate": 5 / 6}, "kb": {"runs": 3, "fact_pass_rate": 2 / 3}}


def test_summarize_with_no_chat_runs():
    assert summarize([]) == {"overall": {"runs": 0}, "scenarios": {}, "by_tag": {}}


def scored_budget(record, scenario):
    return {"record": record, "scores": score_budget(record, scenario)}


def test_summarize_budget():
    control = budget_scenario(flags=(), id="c1", tags=("control",))
    planted = budget_scenario(id="b1", tags=("one_time",), causes={GM: "one_time"})
    items = [scored_budget(budget_record(flags={}, sid="c1"), control),
             scored_budget(budget_record(sid="c1"), control),
             scored_budget(budget_record(alerts=[alert()], sid="b1", failures=1), planted),
             scored_budget(budget_record(alerts=[alert(cause="recurring", day="2026-10-03")], sid="b1"), planted)]
    out = summarize_budget(items, {"c1": control, "b1": planted})
    assert (out["runs"], out["errors"], out["pass_rate"], out["pass_all_runs_rate"]) == (4, 0, 0.5, 0.0)
    assert (out["control_runs"], out["false_alarm_rate"]) == (2, 0.5)
    assert out["checks"]["cause"] == 0.5 and out["checks"]["flags"] == 0.75 and out["checks"]["lead_time"] is None
    assert out["flag_to_alert_days_mean"] == 0.5 and out["llm_failures"] == 1
    assert out["by_tag"]["control"] == {"runs": 2, "pass_rate": 0.5}
    assert out["scenarios"]["b1"]["pass_rate"] == 0.5


def test_summarize_budget_without_controls():
    planted = budget_scenario()
    out = summarize_budget([scored_budget(budget_record(), planted)], {"b1": planted})
    assert out["false_alarm_rate"] is None and out["flag_to_alert_days_mean"] is None


def test_summarize_goal():
    scenario = goal_scenario(first="savings")
    items = [{"record": r, "scores": score_goal(r, scenario)} for r in (
        goal_record(plans=[plan("savings")]), goal_record(plans=[plan("emergency_fund")], attempts=2))]
    out = summarize_goal(items, {"g1": scenario})
    assert (out["runs"], out["pass_rate"], out["pass_all_runs_rate"], out["attempts_mean"]) == (2, 0.5, 0.0, 1.5)
    assert out["checks"]["first_kind"] == 0.5 and out["checks"]["model_not_called"] is None


# seen live: this is a correct answer for a user who never shopped there
def test_abstain_accepts_saying_there_was_no_spending():
    assert check_facts("You did not spend any money at Costco last year.", ABSTAIN).passed


def test_budget_lead_time_ignores_warnings_where_no_dip_was_planted():
    out = score_budget(budget_record(alerts=[alert(kind="low_balance", signal_days=1)]), budget_scenario())
    assert out["low_balance_days_ahead"] is None
