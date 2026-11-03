import json
from datetime import date

import pytest

from eval import run_eval
from eval.agent_runner import BudgetRecord, GoalRecord
from eval.runner import RunRecord
from tests.fakes import FakeKB
from tests.test_eval_fixtures import write

DAY = date(2026, 10, 27)


def test_results_dir_sanitizes_and_never_overwrites(tmp_path):
    first = run_eval.results_dir(tmp_path, DAY, "qwen2.5:14b")
    second = run_eval.results_dir(tmp_path, DAY, "qwen2.5:14b")
    third = run_eval.results_dir(tmp_path, DAY, "qwen2.5:14b")

    assert first == tmp_path / "2026-10-27" / "qwen2.5_14b" and first.is_dir()
    assert (second.name, third.name) == ("qwen2.5_14b-2", "qwen2.5_14b-3")
    assert run_eval.results_dir(tmp_path, DAY, "a/b").name == "a_b"


def test_preflight_reports_each_problem(monkeypatch):
    monkeypatch.setattr(run_eval, "missing_models", lambda models: None)
    [down] = run_eval.preflight("qwen2.5:14b", "gemma3:12b", FakeKB())
    assert "isn't reachable" in down and "ollama serve" in down

    monkeypatch.setattr(run_eval, "missing_models", lambda models: ["gemma3:12b", "nomic-embed-text"])
    problems = run_eval.preflight("qwen2.5:14b", "gemma3:12b", FakeKB(chunks=0))
    assert "ollama pull gemma3:12b" in problems[0] and "ollama pull nomic-embed-text" in problems[1]
    assert "ingest_kb.py" in problems[2] and len(problems) == 3

    monkeypatch.setattr(run_eval, "missing_models", lambda models: [])
    assert run_eval.preflight("qwen2.5:14b", "gemma3:12b", FakeKB()) == []


# no real models, kb or scenarios: every outside dependency of main() is swapped out
@pytest.fixture
def offline(monkeypatch, tmp_path):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    write(scenarios)
    write(scenarios, lambda d: d.update(id="second"))
    monkeypatch.setattr(run_eval, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(run_eval, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(run_eval, "KnowledgeBase", FakeKB)
    monkeypatch.setattr(run_eval, "get_chat_model", lambda **kw: object())
    monkeypatch.setattr(run_eval, "get_embeddings", lambda: object())
    monkeypatch.setattr(run_eval, "preflight", lambda *a: [])
    monkeypatch.setattr(run_eval, "score_run", lambda rec, sc, **kw: {
        "facts": {"found": 1, "total": 1, "passed": True, "score": 1.0, "missing": [], "forbidden_hits": []},
        "tools_ok": True, "sources_ok": True, "similarity": 0.9, "judge": None, "judge_passed": False})
    return tmp_path


def fake_run(scenario, *, run, **kw):
    return RunRecord(scenario_id=scenario.id, run=run, answer="ok", latency_s=1.0)


def test_main_returns_2_on_preflight_failure_and_runs_nothing(offline, monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "preflight", lambda *a: ["Model x isn't pulled. Run: ollama pull x"])
    monkeypatch.setattr(run_eval, "run_once", lambda *a, **k: pytest.fail("ran a scenario"))

    assert run_eval.main([]) == 2
    assert "ollama pull x" in capsys.readouterr().out


def test_main_returns_2_on_bad_scenario(offline, monkeypatch, capsys):
    (run_eval.SCENARIOS_DIR / "broken.json").write_text("{not json")
    monkeypatch.setattr(run_eval, "run_once", lambda *a, **k: pytest.fail("ran a scenario"))

    assert run_eval.main([]) == 2
    assert "broken.json" in capsys.readouterr().out


def test_main_writes_runs_and_summary(offline, monkeypatch):
    monkeypatch.setattr(run_eval, "run_once", fake_run)

    assert run_eval.main(["--runs", "2", "--model", "m:1"]) == 0
    [out] = list((offline / "results").glob("*/m_1"))
    assert len(list(out.glob("*.run*.json"))) == 4
    summary = json.loads((out / "summary.json").read_text())
    cfg = json.loads((out / "config.json").read_text())
    assert summary["overall"]["runs"] == 4
    assert cfg["runs"] == 2 and cfg["model"] == "m:1" and cfg["judge"] == "gemma3:12b"
    assert cfg["scenarios"] == ["debt-highest-apr", "second"] and "finished_at" in cfg
    run1 = json.loads((out / "second.run1.json").read_text())
    assert run1["record"]["answer"] == "ok" and run1["scores"]["similarity"] == 0.9


def test_run_written_before_next_starts(offline, monkeypatch):
    calls = []

    def flaky(scenario, *, run, **kw):
        calls.append(run)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return fake_run(scenario, run=run)

    monkeypatch.setattr(run_eval, "run_once", flaky)
    with pytest.raises(KeyboardInterrupt):
        run_eval.main(["--runs", "2"])
    [out] = list((offline / "results").glob("*/*"))
    assert (out / "debt-highest-apr.run1.json").exists()


# --- budget and goal scenarios (tpc-26) ---

def budget_file(directory, sid="b1"):
    (directory / f"{sid}.json").write_text(json.dumps({
        "id": sid, "kind": "budget", "tags": ["control"], "profile": "control-minimal", "start": "2026-10-01",
        "end": "2026-10-03", "golden_answer": "Nothing is overspent.", "expect": {"flags": []}}))


def goal_file(directory, sid="g1"):
    (directory / f"{sid}.json").write_text(json.dumps({
        "id": sid, "kind": "goal", "profile": "goals-no-surplus", "today": "2026-11-02",
        "golden_answer": "Nothing is affordable.", "expect": {"status": "no_surplus"}}))


def fake_budget(scenario, *, run, **kw):
    return BudgetRecord(scenario_id=scenario.id, run=run, latency_s=1.0)


def fake_goal(scenario, *, run, **kw):
    return GoalRecord(scenario_id=scenario.id, run=run, status="no_surplus", latency_s=1.0)


@pytest.fixture
def agents(offline, monkeypatch):
    budget_file(run_eval.SCENARIOS_DIR)
    goal_file(run_eval.SCENARIOS_DIR)
    monkeypatch.setattr(run_eval, "run_once", fake_run)
    monkeypatch.setattr(run_eval, "run_budget_once", fake_budget)
    monkeypatch.setattr(run_eval, "run_goal_once", fake_goal)
    return offline


def test_kind_defaults_to_every_kind(agents):
    assert run_eval.parse_args([]).kind is None
    assert run_eval.main(["--runs", "1", "--model", "m"]) == 0
    [out] = list((agents / "results").glob("*/m"))
    summary = json.loads((out / "summary.json").read_text())
    assert summary["overall"]["runs"] == 2 and summary["budget"]["runs"] == 1 and summary["goal"]["runs"] == 1
    assert summary["budget"]["false_alarm_rate"] == 0.0 and summary["budget"]["by_tag"]["control"]["runs"] == 1
    assert json.loads((out / "b1.run1.json").read_text())["scores"]["checks"] == {"flags": True}
    assert json.loads((out / "g1.run1.json").read_text())["scores"]["checks"]["model_not_called"] is True


def test_kind_rejects_an_unknown_value():
    with pytest.raises(SystemExit):
        run_eval.parse_args(["--kind", "close"])


# only chat uses the knowledge base, the judge and the embeddings, so a budget run needs none of them
def test_kind_budget_builds_nothing_only_chat_needs(agents, monkeypatch, capsys):
    built = []
    monkeypatch.setattr(run_eval, "KnowledgeBase", lambda: pytest.fail("built the knowledge base"))
    monkeypatch.setattr(run_eval, "get_embeddings", lambda: pytest.fail("built the embeddings"))
    monkeypatch.setattr(run_eval, "get_chat_model", lambda **kw: built.append(kw["model"]) or object())
    monkeypatch.setattr(run_eval, "run_once", lambda *a, **k: pytest.fail("ran a chat scenario"))

    assert run_eval.main(["--runs", "1", "--model", "m", "--kind", "budget"]) == 0
    assert built == ["m"]
    [out] = list((agents / "results").glob("*/m"))
    summary = json.loads((out / "summary.json").read_text())
    assert summary["overall"] == {"runs": 0} and summary["budget"]["runs"] == 1 and "goal" not in summary
    assert json.loads((out / "config.json").read_text())["scenarios"] == ["b1"]
    printed = capsys.readouterr().out
    assert "budget scenario" in printed and "false alarms on controls: 0%" in printed


def test_only_a_budget_scenario_also_skips_chats_needs(agents, monkeypatch):
    monkeypatch.setattr(run_eval, "KnowledgeBase", lambda: pytest.fail("built the knowledge base"))
    assert run_eval.main(["--runs", "1", "--model", "m", "--only", "b1"]) == 0


def test_kind_with_nothing_to_run_returns_2(offline, capsys):
    assert run_eval.main(["--kind", "goal"]) == 2
    assert "nothing to run" in capsys.readouterr().out


def test_preflight_without_chat_needs_only_the_agent_model(monkeypatch):
    asked = []
    monkeypatch.setattr(run_eval, "missing_models", lambda models: asked.extend(models) or [])
    assert run_eval.preflight("qwen2.5:14b", "gemma3:12b", None) == []
    assert asked == ["qwen2.5:14b"]


def test_chat_summary_carries_tags(offline, monkeypatch):
    write(run_eval.SCENARIOS_DIR, lambda d: d.update(id="tagged", tags=["debt"]))
    monkeypatch.setattr(run_eval, "run_once", fake_run)
    assert run_eval.main(["--runs", "1", "--model", "m"]) == 0
    [out] = list((offline / "results").glob("*/m"))
    assert json.loads((out / "summary.json").read_text())["by_tag"] == {"debt": {"runs": 1, "fact_pass_rate": 1.0}}


def test_skip_tag_leaves_out_tagged_scenarios(offline, capsys):
    assert run_eval.parse_args(["--skip-tag", "temporal", "--skip-tag", "x"]).skip_tag == ["temporal", "x"]
    assert run_eval.parse_args([]).skip_tag == []


def test_new_flags_parse():
    args = run_eval.parse_args(["--retrieval", "none", "--kg", "on", "--retries", "off", "--temperature", "0.7",
                                "--seed", "3", "--today", "2026-11-06", "--label", "chat-none", "--limit", "4"])
    assert (args.retrieval, args.kg, args.retries, args.temperature, args.seed, args.label, args.limit) == \
        ("none", "on", "off", 0.7, 3, "chat-none", 4)
    assert args.today == date(2026, 11, 6)


def test_defaults_keep_todays_run():
    args = run_eval.parse_args([])
    assert (args.retrieval, args.kg, args.retries, args.temperature, args.seed, args.today, args.label, args.limit) == \
        (None, None, None, 0.0, None, None, None, None)


def test_label_goes_in_the_results_folder(tmp_path):
    assert run_eval.results_dir(tmp_path, date(2026, 11, 6), "a:b", "chat-none").name == "a_b-chat-none"


@pytest.mark.parametrize("mode", ["graph", "hybrid", "hybrid-shuffled"])
def test_graph_modes_are_refused_until_the_graph_is_built(mode, capsys):
    assert run_eval.main(["--retrieval", mode]) == 2
    assert "TPC-39" in capsys.readouterr().out


def test_limit_spreads_across_the_list():
    assert run_eval.spread(list(range(10)), 4) == [0, 2, 5, 7]
    assert run_eval.spread([1, 2], 4) == [1, 2]
