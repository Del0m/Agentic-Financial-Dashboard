import json
from pathlib import Path

import pytest

from eval import compare as cmp
from eval import budget_improvement as improvement
from eval import run_benchmark as bench
from eval import sample_answers as sample


def write_folder(folder: Path, results: dict, commit="abc", kind="chat", scenario_hash="h"):
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"commit": commit, "scenario_hash": scenario_hash, "finished_at": "t"}))
    for sid, passes in results.items():
        for n, passed in enumerate(passes, 1):
            scores = {"facts": {"passed": passed}, "judge_passed": passed} if kind == "chat" else {"checks": {}, "passed": passed}
            record = {"scenario_id": sid, "kind": kind, "answer": "text"}
            (folder / f"{sid}.run{n}.json").write_text(json.dumps({"record": record, "scores": scores}))


TAGS = {"a": ("temporal",), "b": ("temporal",), "c": ("graph_unsuited",), "d": ("graph_unsuited",)}


def test_compare_counts_wins_losses_and_ties_per_group(tmp_path):
    write_folder(tmp_path / "w", {"a": [True] * 3, "b": [True] * 3, "c": [False] * 3, "d": [True] * 3})
    write_folder(tmp_path / "wo", {"a": [False] * 3, "b": [False] * 3, "c": [True] * 3, "d": [True] * 3})
    result = cmp.compare(tmp_path / "w", tmp_path / "wo", "chat", TAGS)
    assert result["groups"]["temporal"] == {"scenarios": 2, "wins": 2, "losses": 0, "ties": 0, "passed_with": 2,
                                            "passed_without": 0, "verdict": "helps"}
    assert result["groups"]["graph_unsuited"]["verdict"] == "does not help"
    assert result["groups"]["all"]["wins"] == 2 and result["groups"]["all"]["losses"] == 1


@pytest.mark.parametrize("wins, losses, expected", [(2, 0, "helps"), (3, 1, "helps"), (1, 0, "does not help"),
                                                    (0, 0, "does not help"), (0, 1, "does not help"), (0, 2, "hurts")])
def test_verdict_rule_matches_the_preregistration(wins, losses, expected):
    assert cmp.verdict(wins, losses) == expected


def test_scenario_passes_on_two_of_three():
    passed = lambda item: item
    assert cmp.scenario_passes([True, True, False], passed) is True
    assert cmp.scenario_passes([True, False, False], passed) is False
    assert cmp.scenario_passes([True], passed) is True


@pytest.mark.parametrize("field", ["commit", "scenario_hash"])
def test_compare_refuses_folders_that_differ(tmp_path, field):
    write_folder(tmp_path / "w", {"a": [True]})
    write_folder(tmp_path / "wo", {"a": [True]}, **{field: "other"})
    with pytest.raises(cmp.NotComparable, match=field):
        cmp.compare(tmp_path / "w", tmp_path / "wo", "chat", TAGS)


def test_retry_fire_rate_marks_a_mechanism_that_rarely_ran(tmp_path):
    write_folder(tmp_path / "w", {"b1": [True]}, kind="budget")
    write_folder(tmp_path / "wo", {"b1": [True]}, kind="budget")
    path = tmp_path / "w" / "b1.run1.json"
    item = json.loads(path.read_text())
    item["record"].update(llm_calls=100, llm_retries=1)
    path.write_text(json.dumps(item))
    result = cmp.compare(tmp_path / "w", tmp_path / "wo", "retries", TAGS)
    assert result["fire_rates"]["resend"] == 0.01 and result["uninformative"]["resend"] is True
    assert result["uninformative"]["goal_correction"] is True       # never ran at all


def test_budget_metric_reads_cause_and_action():
    item = {"scores": {"checks": {"cause": True, "action": False, "flags": True}, "passed": False}}
    assert cmp.budget_passed(item) is False
    item["scores"]["checks"]["action"] = True
    assert cmp.budget_passed(item) is True


def test_every_condition_changes_one_factor_from_the_baseline():
    rows = bench.conditions(graph_built=True)
    assert sum(1 for _, c in rows if c.label == "baseline") == 3
    labels = {c.label for _, c in rows}
    assert {"chat-none", "chat-vector", "chat-graph", "chat-hybrid-shuffled", "budget-kg-off"} <= labels
    assert all(model == bench.ABLATION_MODEL for model, c in rows if c.label != "baseline")


def test_conditions_needing_the_graph_wait_for_it():
    labels = {c.label for _, c in bench.conditions(graph_built=False)}
    assert "chat-graph" not in labels and "chat-hybrid-shuffled" not in labels and "budget-kg-off" not in labels
    assert "chat-none" in labels and "budget-retries-off" in labels


def test_finished_condition_needs_the_same_commit(tmp_path):
    folder = tmp_path / "2026-11-06" / "qwen2.5_14b-chat-none"
    write_folder(folder, {"a": [True]})
    (folder / "summary.json").write_text("{}")
    assert bench.is_finished("qwen2.5:14b", "chat-none", "abc", root=tmp_path) is True
    assert bench.is_finished("qwen2.5:14b", "chat-none", "other", root=tmp_path) is False
    assert bench.is_finished("mistral", "chat-none", "abc", root=tmp_path) is False


def test_benchmark_refuses_uncommitted_changes(monkeypatch, capsys):
    monkeypatch.setattr(bench, "dirty_files", lambda: [" M eval/run_eval.py"])
    monkeypatch.setattr(bench, "run", lambda c: pytest.fail("ran something"))
    assert bench.main([]) == 2
    assert "uncommitted" in capsys.readouterr().out


def test_allow_dirty_is_only_for_the_pilot():
    with pytest.raises(SystemExit):
        bench.main(["--allow-dirty"])


def test_sample_hides_the_condition_and_covers_each_folder(tmp_path):
    for name in ("x", "y"):
        write_folder(tmp_path / name, {f"{name}{i}": [True] for i in range(20)})
    chosen = sample.pick([tmp_path / "x", tmp_path / "y"], size=30)
    assert len(chosen) == 30 and {c["folder"] for c in chosen} == {"x", "y"}
    assert chosen == sample.pick([tmp_path / "x", tmp_path / "y"], size=30)    # a fixed seed gives the same sample
    out = tmp_path / "hand"
    sample.write_sample(chosen, out, {})
    rows = json.loads((out / "sample.json").read_text())
    assert set(rows[0]) == {"n", "question", "answer", "passes"}


def test_sample_score_reports_agreement_with_the_judge(tmp_path):
    write_folder(tmp_path / "x", {"a": [True], "b": [False]})
    out = tmp_path / "hand"
    sample.write_sample(sample.pick([tmp_path / "x"], size=2), out, {})
    rows = json.loads((out / "sample.json").read_text())
    key = {r["n"]: r for r in json.loads((out / "key.json").read_text())}
    rows[0]["passes"] = key[rows[0]["n"]]["judge_passed"]
    (out / "sample.json").write_text(json.dumps(rows))
    assert sample.score(out)["agreed"] == 1 and sample.score(out)["unrated"] == 1


# budget improvement
def test_no_ai_baseline_makes_no_proposals_and_closes_three_periods(tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from eval.budget_users import IMPROVE_USERS
    result = improvement.run_user(IMPROVE_USERS[2], model=FakeListChatModel(responses=["no"]), decisions=None,
                                  audit_path=tmp_path / "a.jsonl")
    assert result["proposals"] == 0 and len(result["periods"]) == 3
    assert all(p["mape"] is not None for p in result["periods"])


def _proposing_model():
    from tests.fakes import SchemaReplyModel
    return SchemaReplyModel(cause="recurring", first_action="adjust_next_budget")


def test_collect_then_replay_applies_decisions_and_counts_unseen(tmp_path):
    from eval.budget_users import IMPROVE_USERS
    user = IMPROVE_USERS[2]
    collected = improvement.run_user(user, model=_proposing_model(), decisions=None, audit_path=tmp_path / "a.jsonl")
    keys = [row["key"] for row in collected["proposal_rows"]]
    assert keys and all(row["proposed_limit_cents"] for row in collected["proposal_rows"])
    assert "model" not in collected["proposal_rows"][0]

    first = {keys[0]: {"decision": "accept"}}
    replayed = improvement.run_user(user, model=_proposing_model(), decisions=first, audit_path=tmp_path / "b.jsonl")
    assert replayed["accepted"] == 1 and replayed["rejected"] == 0
    assert replayed["unseen"] == replayed["proposals"] - 1      # the rest had no decision, so they count as rejected


def test_merge_drops_model_names_and_duplicates(tmp_path):
    row = {"key": "k", "category": "c", "cause": "recurring", "message": "m", "proposed_limit_cents": 1, "periods": []}
    (tmp_path / "collected-a.json").write_text(json.dumps([row]))
    (tmp_path / "collected-b.json").write_text(json.dumps([{**row, "message": "other"}]))
    assert improvement.merge_proposals(tmp_path) == {"proposals": 1}
    assert json.loads((tmp_path / "proposals.json").read_text())[0]["message"] == "m"


def test_bad_decision_value_is_refused(tmp_path):
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps({"decisions": {"k": {"decision": "maybe"}}}))
    with pytest.raises(ValueError, match="accept or reject"):
        improvement.load_decisions(path)
